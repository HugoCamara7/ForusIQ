"""Códigos de motivo por fila y su traducción a texto.

Se guardan tanto las filas enviadas como las no enviadas, cada una con su motivo.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from forusight.config.settings import EngineParams
from forusight.engine.common import QUIEBRE

ENVIO_QUIEBRE = "ENVIO_QUIEBRE"
ENVIO_CURVA_ROTA = "ENVIO_CURVA_ROTA"
ENVIO_INTRODUCCION = "ENVIO_INTRODUCCION"
ENVIO_REPOSICION = "ENVIO_REPOSICION"

PARCIAL_CD = "PARCIAL_CD"
PARCIAL_TOPE = "PARCIAL_TOPE"
PARCIAL_MULTIPLO = "PARCIAL_MULTIPLO"

NO_SIN_STOCK_CD = "NO_SIN_STOCK_CD"
NO_CD_INSUFICIENTE = "NO_CD_INSUFICIENTE"
NO_TOPE_TIENDA = "NO_TOPE_TIENDA"
NO_MULTIPLO_ENVIO = "NO_MULTIPLO_ENVIO"

DESCRIPCION_CODIGOS: dict[str, str] = {
    ENVIO_QUIEBRE: "Envío por quiebre (vendía y hoy está en 0 o bajo el mínimo)",
    ENVIO_CURVA_ROTA: "Envío para completar talla core faltante",
    ENVIO_INTRODUCCION: "Introducción de modelo con afinidad suficiente",
    ENVIO_REPOSICION: "Reposición hasta el stock objetivo",
    "NO_SIN_DEMANDA": "Tuvo exposición suficiente y no vendió",
    "NO_SIN_REFERENCIA": "Sin historia ni referencias de venta",
    "NO_AFINIDAD_BAJA": "Afinidad bajo el umbral para introducir",
    "NO_SOBRESTOCK": "Cobertura actual sobre el umbral de sobrestock",
    "NO_SIN_NECESIDAD": "Stock + tránsito cubren el objetivo",
    "NO_CURVA_MINIMA_CD": "El CD no cubre la curva mínima para introducir",
    NO_SIN_STOCK_CD: "Sin stock disponible en el CD",
    NO_CD_INSUFICIENTE: "El CD no alcanza; se priorizó a otras tiendas",
    NO_TOPE_TIENDA: "Tope de unidades de la tienda alcanzado",
    NO_MULTIPLO_ENVIO: "Necesidad menor al múltiplo de envío",
    "PEND_DISTRIBUCION": "Carga manual pendiente de distribución",
    "NO_ALMACENAMIENTO": "La tienda no tiene capacidad de almacenamiento",
}


def asignar_codigos(f: pd.DataFrame, params: EngineParams) -> pd.DataFrame:
    m = params.asignacion.multiplo_envio
    out = f.copy()
    envia = out["cantidad"] > 0
    quiebre = out["estado_mc"].eq(QUIEBRE) | out["estado_sku"].eq(QUIEBRE)
    cod_envio = np.select(
        [out["es_introduccion"], quiebre, out["talla_core_faltante"]],
        [ENVIO_INTRODUCCION, ENVIO_QUIEBRE, ENVIO_CURVA_ROTA],
        ENVIO_REPOSICION,
    )
    bloqueo = out["motivo_asignacion"].where(out["motivo_asignacion"].ne(""), out["motivo_bloqueo"])
    cod_no = np.select(
        [
            bloqueo.ne(""),
            out["cd_disponible"].le(0),
            out["necesidad"].lt(m),
            out["tope_tienda_agotado"],
        ],
        [bloqueo, NO_SIN_STOCK_CD, NO_MULTIPLO_ENVIO, NO_TOPE_TIENDA],
        NO_CD_INSUFICIENTE,
    )
    out["motivo_codigo"] = np.where(envia, cod_envio, cod_no)
    parcial = envia & (out["cantidad"] < out["necesidad"])
    out["motivo_parcial"] = np.where(
        parcial,
        np.select(
            [out["cd_agotado"], out["tope_tienda_agotado"]],
            [PARCIAL_CD, PARCIAL_TOPE],
            PARCIAL_MULTIPLO,
        ),
        "",
    )
    return out


def _n(x: float) -> str:
    x = float(x)
    return f"{x:.0f}" if x == int(x) else f"{x:.1f}"


def _pares(x: float) -> str:
    return f"{_n(x)} par" if float(x) == 1 else f"{_n(x)} pares"


def texto_motivo(r: dict, params: EngineParams, cd_id: str = "320") -> str:
    """Texto legible para una fila (dict con las columnas de detalle)."""
    c = r["motivo_codigo"]
    q = int(r["cantidad"])
    base = (
        f"stock {_n(r['stock_disponible'])} + tránsito {_n(r['stock_transito'])}, "
        f"objetivo {int(r['stock_objetivo'])} ({_n(r['cobertura_semanas'])} sem de cobertura, "
        f"rotación {r['rotacion']}), demanda estimada {r['demanda_sku']:.2f} pares/sem"
    )
    envio = f"Se recomienda enviar {q} unidad{'es' if q != 1 else ''} porque "
    umbral = params.afinidad.umbral_introduccion
    if c == ENVIO_QUIEBRE:
        t = (
            envio + f"está en quiebre: vendió {_pares(r['venta_12s'])} en 12 semanas "
            f"({_n(r['venta_4s'])} en las últimas 4) y hoy tiene {_n(r['stock_disponible'])}; "
            + base
        )
    elif c == ENVIO_CURVA_ROTA:
        t = (
            envio + f"la talla {r['talla']} es core y está en 0 mientras el modelo sigue vendiendo "
            f"(curva rota); " + base
        )
    elif c == ENVIO_INTRODUCCION:
        t = (
            envio + f"se introduce el modelo: afinidad {r['afinidad']:.2f} ≥ umbral {umbral:.2f} "
            f"y demanda estimada por {str(r['fuente_demanda']).lower()} de "
            f"{r['demanda_semanal']:.2f} pares/sem del modelo-color; " + base
        )
    elif c == ENVIO_REPOSICION:
        t = envio + "hay que reponer hasta el stock objetivo: " + base
    elif c == "NO_SIN_DEMANDA":
        t = (
            f"No se envía: el modelo estuvo expuesto {int(r['dias_12s_mc'])} días en 12 semanas "
            "sin ninguna venta (sin demanda)."
        )
    elif c == "NO_SIN_REFERENCIA":
        t = "No se envía: sin historia en la tienda ni referencias de venta en tiendas similares."
    elif c == "NO_AFINIDAD_BAJA":
        t = (
            f"No se introduce: afinidad {r['afinidad']:.2f} bajo el umbral {umbral:.2f}, "
            "aunque el CD tenga stock."
        )
    elif c == "NO_SOBRESTOCK":
        t = (
            f"No se envía: sobrestock, la cobertura actual ({_n(r['cobertura_actual'])} semanas) "
            "supera el umbral."
        )
    elif c == "NO_SIN_NECESIDAD" and int(r["stock_objetivo"]) == 0:
        t = (
            "No se envía: la demanda estimada "
            f"({r['demanda_sku']:.2f} pares/sem) no llega a 1 par en la cobertura objetivo."
        )
    elif c == "NO_SIN_NECESIDAD":
        t = "No se envía: " + base + "; el stock actual ya cubre el objetivo."
    elif c == "NO_CURVA_MINIMA_CD":
        t = (
            "No se introduce: el CD no cubre la curva mínima de tallas del modelo "
            "(o la demanda no alcanza a cubrirla)."
        )
    elif c == NO_SIN_STOCK_CD:
        t = f"No se envía: necesidad {int(r['necesidad'])}, pero el CD {cd_id} no tiene stock disponible."
    elif c == NO_MULTIPLO_ENVIO:
        t = (
            f"No se envía: la necesidad ({int(r['necesidad'])}) es menor que el múltiplo de envío "
            f"({params.asignacion.multiplo_envio})."
        )
    elif c == NO_TOPE_TIENDA:
        t = "No se envía: la tienda alcanzó su tope de unidades para esta corrida."
    else:  # NO_CD_INSUFICIENTE
        t = (
            f"No se envía: necesidad {int(r['necesidad'])}, pero el stock del CD {cd_id} "
            "se asignó a tiendas con mayor prioridad."
        )

    p = r.get("motivo_parcial", "")
    if p == PARCIAL_CD:
        t += f". Se envía menos que la necesidad ({int(r['necesidad'])}) porque el CD no alcanza."
    elif p == PARCIAL_TOPE:
        t += f". Se envía menos que la necesidad ({int(r['necesidad'])}) por el tope de la tienda."
    elif p == PARCIAL_MULTIPLO:
        t += f". Se envía menos que la necesidad ({int(r['necesidad'])}) por el múltiplo de envío."
    if r.get("tope_sku_aplicado"):
        t += f" Necesidad acotada al máximo por SKU ({params.tope_tienda.max_unidades_por_sku})."
    return t


def generar_textos(f: pd.DataFrame, params: EngineParams, cd_id: str = "320") -> pd.Series:
    cols = [
        "motivo_codigo",
        "motivo_parcial",
        "cantidad",
        "necesidad",
        "stock_disponible",
        "stock_transito",
        "stock_objetivo",
        "cobertura_semanas",
        "rotacion",
        "demanda_sku",
        "demanda_semanal",
        "venta_12s",
        "venta_4s",
        "talla",
        "afinidad",
        "fuente_demanda",
        "dias_12s_mc",
        "cobertura_actual",
        "tope_sku_aplicado",
    ]
    registros = f[cols].to_dict("records")
    return pd.Series([texto_motivo(r, params, cd_id) for r in registros], index=f.index)
