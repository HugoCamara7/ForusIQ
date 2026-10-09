"""Orquestación del motor: entradas canónicas → distribución propuesta con motivo."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from forusight.config.settings import EngineParams
from forusight.data.schemas import DistribucionSchema, validar
from forusight.engine.affinity import calcular_afinidad
from forusight.engine.allocation import asignar
from forusight.engine.availability import estados_disponibilidad
from forusight.engine.common import KEY_MC, resolver_fecha_corte
from forusight.engine.demand import estimar_demanda
from forusight.engine.reasons import asignar_codigos, generar_textos
from forusight.engine.similarity import tiendas_similares
from forusight.engine.size_curve import curva_tallas
from forusight.engine.target import (
    aplicar_llenado_inicial,
    calcular_necesidad,
    calcular_objetivo_mc,
)
from forusight.engine.universe import construir_base

COLUMNAS_SALIDA = [
    "run_id",
    "tienda_id",
    "modelo_color_id",
    "sku",
    "talla",
    "estado_mc",
    "estado_sku",
    "stock_tienda",
    "stock_transito",
    "venta_4s",
    "venta_12s",
    "demanda_semanal",
    "stock_objetivo",
    "necesidad",
    "stock_cd_disponible",
    "cantidad",
    "afinidad",
    "motivo_codigo",
    "motivo_texto",
]
COLUMNAS_EXTRA = [
    "modelo_id",
    "minimo_exhibicion",
    "color",
    "categoria",
    "genero",
    "rango_precio",
    "talla_orden",
    "cluster",
    "es_core",
    "share_talla",
    "prior_talla",
    "nivel_prior",
    "demanda_mc",
    "fuente_demanda",
    "factor_tendencia",
    "rotacion",
    "cobertura_semanas",
    "cobertura_actual",
    "sobrestock",
    "curva_rota",
    "talla_core_faltante",
    "es_introduccion",
    "venta_desde_ruta",
    "venta_post_corte",
    "recepcion_post_corte",
    "piso_venta",
    "necesidad_bruta",
    "etapa",
    "prioridad_inicial",
    "motivo_parcial",
    "rop_ref",
    "smt_ref",
    "ue_ref",
]


@dataclass
class EngineInputs:
    ventas: pd.DataFrame
    stock_tienda: pd.DataFrame
    stock_cd: pd.DataFrame
    dim_producto: pd.DataFrame
    dim_tienda: pd.DataFrame
    #: Opcional: pares (tienda_id, modelo_id) donde se puede INTRODUCIR un modelo.
    permitidos: pd.DataFrame | None = None
    #: Opcional: reporte de distribución del día usado como base (filas originales).
    reporte: pd.DataFrame | None = None
    #: Opcional: pedidos hacia cada tienda (tienda_id, sku, estado, clasificacion, unidades),
    #: de las tablas de pedidos; de ahí sale el tránsito (data.transito.aplicar_pedidos).
    pedidos: pd.DataFrame | None = None
    #: Opcional: venta diaria reciente (fecha, tienda_id, sku, unidades), de BigQuery.
    venta_diaria: pd.DataFrame | None = None
    #: Opcional: venta desde la última ruta y posterior al corte, por tienda×SKU
    #: (engine.venta_reciente.resumir).
    venta_reciente: pd.DataFrame | None = None
    #: Opcional: modelo-color bloqueado por tienda (reporte de bloqueos).
    bloqueos: pd.DataFrame | None = None
    #: Opcional: nivel máximo / punto de reorden del último reporte de cada tienda.
    niveles_ref: pd.DataFrame | None = None
    #: Opcional: maestro de planificación (SMT, nivel, UE por tienda×SKU; k por categoría).
    planificacion: object | None = None

    def validadas(self) -> EngineInputs:
        return EngineInputs(
            permitidos=self.permitidos,
            reporte=self.reporte,
            venta_diaria=self.venta_diaria,
            venta_reciente=self.venta_reciente,
            bloqueos=self.bloqueos,
            niveles_ref=self.niveles_ref,
            planificacion=self.planificacion,
            ventas=validar("ventas", self.ventas),
            stock_tienda=validar("stock_tienda", self.stock_tienda),
            stock_cd=validar("stock_cd", self.stock_cd),
            dim_producto=validar("dim_producto", self.dim_producto),
            dim_tienda=validar("dim_tienda", self.dim_tienda),
        )


@dataclass
class EngineResult:
    run_id: str
    fecha_corte: pd.Timestamp
    params: EngineParams
    detalle: pd.DataFrame  # una fila por tienda×SKU evaluada (enviada o no)
    mc: pd.DataFrame  # diagnóstico tienda×modelo-color
    resumen: dict = field(default_factory=dict)
    #: Tiendas de la corrida con calendario aplicado (mall, lead time, revisión, recibe hoy).
    tiendas: pd.DataFrame | None = None
    #: Productos con la temporada comercial del maestro (la que filtró la corrida).
    productos: pd.DataFrame | None = None

    @property
    def propuesta(self) -> pd.DataFrame:
        return self.detalle.loc[self.detalle["cantidad"] > 0]


def aplicar_surtido(sku, dim_producto, bloqueos, params: EngineParams):
    """Bloqueos por tienda y temporadas a reponer: necesidad 0 (manda sobre reponer lo vendido)."""
    from forusight.data.bloqueos import fuera_de_surtido

    temp = None
    if "temporada_comercial" in dim_producto:  # maestro de temporadas (data/temporadas.py)
        temp = sku["sku"].map(
            dim_producto.drop_duplicates("sku").set_index("sku")["temporada_comercial"]
        )
    bloq, fuera = fuera_de_surtido(
        sku["tienda_id"], sku["modelo_color_id"], temp, bloqueos, params.surtido.temporadas_reponer
    )
    if not (bloq.any() or fuera.any()):
        return sku
    sku = sku.copy()
    corta = (bloq | fuera).to_numpy()
    sku.loc[corta, "necesidad"] = 0
    if "piso_venta" in sku:
        sku.loc[corta, "piso_venta"] = 0
        sku.loc[corta, "repone_venta_ruta"] = False
    sku.loc[fuera.to_numpy(), "motivo_bloqueo"] = "NO_TEMPORADA"
    sku.loc[bloq.to_numpy(), "motivo_bloqueo"] = "NO_BLOQUEO_TIENDA"
    return sku


def topes_por_tienda(dim_tienda: pd.DataFrame, params: EngineParams) -> dict[str, int]:
    default = params.tope_tienda.max_unidades_por_tienda
    tope = dim_tienda["max_unidades_corrida"].fillna(default)
    if "recibe_hoy" in dim_tienda:  # calendario: hoy no le toca reponer
        tope = tope.where(dim_tienda["recibe_hoy"].fillna(True).astype(bool), 0)
    return dict(zip(dim_tienda["tienda_id"], tope.astype("int64"), strict=True))


def ejecutar(
    inputs: EngineInputs,
    params: EngineParams,
    fecha_corte: pd.Timestamp | str | None = None,
    run_id: str | None = None,
    cd_id: str = "320",
    validar_entradas: bool = True,
) -> EngineResult:
    """Corre el motor completo. Determinista para mismas entradas y parámetros."""
    run_id = run_id or uuid.uuid4().hex[:12]
    inp = inputs.validadas() if validar_entradas else inputs
    corte = resolver_fecha_corte(inp.ventas, fecha_corte)

    base = construir_base(
        inp.ventas,
        inp.stock_tienda,
        inp.stock_cd,
        inp.dim_producto,
        inp.dim_tienda,
        params,
        corte,
        inp.permitidos,
    )
    sku, mc = estados_disponibilidad(base.sku, base.semanal, params)
    similares = tiendas_similares(base.tiendas, base.semanal, params)
    mc = estimar_demanda(mc, base.semanal, similares, params)
    mc = calcular_afinidad(mc, base.semanal, params)
    mc = mc.merge(
        base.tiendas[
            ["tienda_id", "factor_cobertura_tienda", "tienda_liquidadora"]
            + [c for c in ("leadtime_dias", "revision_dias") if c in base.tiendas]
        ],
        on="tienda_id",
        how="left",
    )
    mc = calcular_objetivo_mc(mc, params)
    sku = curva_tallas(sku, base.semanal, params)
    vr = inp.venta_reciente
    ajustes = ["venta_desde_ruta", "venta_post_corte", "recepcion_post_corte"]
    if vr is not None and len(vr):
        sku = sku.merge(
            vr[["tienda_id", "sku", *[c for c in ajustes if c in vr]]],
            on=["tienda_id", "sku"],
            how="left",
        )
    for c in ajustes:
        sku[c] = sku[c].fillna(0).astype(float) if c in sku else 0.0
    nr = inp.niveles_ref
    if (nr is None or not len(nr)) and params.nivel_neo.activo:
        from forusight.engine.nivel_neo import niveles

        nr = niveles(sku, base.semanal, base.tiendas, inp.dim_producto, inp.planificacion, params)
    if nr is not None and len(nr):
        extra = [c for c in ("ue_ref", "pronostico_ref", "smt_ref") if c in nr]
        sku = sku.merge(
            nr[["tienda_id", "sku", "nivel_ref", "rop_ref", *extra]].drop_duplicates(
                ["tienda_id", "sku"]
            ),
            on=["tienda_id", "sku"],
            how="left",
        )
    sku = calcular_necesidad(sku, mc, params)
    sku = aplicar_llenado_inicial(sku, base.tiendas, params)  # marca nueva: primer envío
    sku = aplicar_surtido(sku, inp.dim_producto, inp.bloqueos, params)

    disp = dict(zip(base.cd["sku"], base.cd["disponible"], strict=True))
    res = asignar(sku, disp, topes_por_tienda(base.tiendas, params), params)
    det = res.filas.merge(
        mc[KEY_MC + ["dias_12s"]].rename(columns={"dias_12s": "dias_12s_mc"}), on=KEY_MC, how="left"
    )
    for c in ("rop_ref", "smt_ref", "ue_ref"):  # sólo con nivel del reporte / nivel_neo
        if c not in det:
            det[c] = np.nan
    det = asignar_codigos(det, params)
    # Disponibilidad de TODO el retail (todas las tiendas, antes de quedarse con las de hoy).
    from forusight.engine import disponibilidad as DISP

    disp_retail = DISP.kpis(
        det.rename(
            columns={"stock_disponible": "stock_tienda", "cd_disponible": "stock_cd_disponible"}
        )
    )
    if "recibe_hoy" in base.tiendas:  # el resultado sólo lista las tiendas que reponen hoy
        hoy = set(
            base.tiendas.loc[base.tiendas["recibe_hoy"].fillna(True).astype(bool), "tienda_id"]
        )
        det = det.loc[det["tienda_id"].isin(hoy)]
    if "cd_origen" in inp.dim_producto:  # bodega de cada SKU (AZALEIA: 380) para el texto
        det["cd_origen"] = det["sku"].map(
            inp.dim_producto.drop_duplicates("sku").set_index("sku")["cd_origen"]
        )
    det["motivo_texto"] = generar_textos(det, params, cd_id)
    det["run_id"] = run_id
    det = det.rename(
        columns={
            "stock_disponible": "stock_tienda",
            "cd_disponible": "stock_cd_disponible",
        }
    )
    det["demanda_mc"] = det["demanda_semanal"]
    det["demanda_semanal"] = det["demanda_sku"]  # demanda estimada de la talla
    det = (
        det[COLUMNAS_SALIDA + COLUMNAS_EXTRA]
        .sort_values(["tienda_id", "modelo_color_id", "talla_orden", "sku"], kind="mergesort")
        .reset_index(drop=True)
    )
    for c in ("stock_objetivo", "necesidad", "cantidad"):
        det[c] = det[c].astype("int64")
    det = DistribucionSchema.validate(det)

    resumen = {
        "run_id": run_id,
        "fecha_corte": corte.date().isoformat(),
        "cd_id": cd_id,
        "filas_evaluadas": int(len(det)),
        "filas_con_envio": int((det["cantidad"] > 0).sum()),
        "unidades_a_distribuir": int(det["cantidad"].sum()),
        "necesidad_total": int(det["necesidad"].sum()),
        "tiendas_con_envio": int(det.loc[det["cantidad"] > 0, "tienda_id"].nunique()),
        "stock_cd_disponible": int(base.cd["disponible"].sum()),
        "iteraciones_asignacion": res.iteraciones,
        "fill_rate": float(np.round(det["cantidad"].sum() / max(det["necesidad"].sum(), 1), 4)),
        "disponibilidad_retail": disp_retail.get("simple_antes"),
        "disponibilidad_retail_despues": disp_retail.get("simple_despues"),
        "sku_activos_retail": disp_retail.get("activos", 0),
    }
    return EngineResult(
        run_id=run_id,
        fecha_corte=corte,
        params=params,
        detalle=det,
        mc=mc,
        resumen=resumen,
        tiendas=base.tiendas,
        productos=inp.dim_producto,
    )
