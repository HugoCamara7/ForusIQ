"""Rotación, cobertura, stock objetivo por talla y necesidad.

cobertura_semanas = lead time + ciclo de revisión + seguridad(rotación)   [por categoría]
objetivo_mc       = redondeo(demanda_semanal × cobertura)
objetivo_talla    = max(mínimo de exhibición si talla core, Hamilton(objetivo_mc, share_talla))
necesidad         = max(0, objetivo_talla − stock − tránsito), con tope por SKU×tienda
Sobrestock (cobertura actual > umbral): necesidad 0 y se marca.
Introducción (NUNCA_TUVO, o EXPOSICION_INSUFICIENTE sin stock ni tránsito): exige
afinidad >= umbral; si no, necesidad 0.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from forusight.config.settings import EngineParams
from forusight.engine.common import (
    EXPOSICION_INSUFICIENTE,
    KEY_MC,
    NUNCA_TUVO,
    safe_div,
)
from forusight.engine.demand import FUENTE_SIN_DEMANDA, FUENTE_SIN_REFERENCIA
from forusight.engine.size_curve import repartir_hamilton

# Motivos de bloqueo a nivel MC / fila (se traducen en reasons.py)
NO_SIN_DEMANDA = "NO_SIN_DEMANDA"
NO_SIN_REFERENCIA = "NO_SIN_REFERENCIA"
NO_AFINIDAD_BAJA = "NO_AFINIDAD_BAJA"
NO_SOBRESTOCK = "NO_SOBRESTOCK"
NO_SIN_NECESIDAD = "NO_SIN_NECESIDAD"


def clasificar_rotacion(mc: pd.DataFrame, params: EngineParams) -> pd.DataFrame:
    """Rotación por percentil de sell-through nacional del MC dentro de su categoría."""
    r = params.rotacion
    nac = mc.groupby(["modelo_color_id", "categoria"], sort=True).agg(
        venta=("venta_12s", "sum"), stock=("stock_disponible", "sum")
    )
    nac["sell_through"] = safe_div(nac["venta"], nac["venta"] + nac["stock"], np.nan)
    pct = nac.groupby(level="categoria")["sell_through"].rank(pct=True, method="average")
    nac["rotacion"] = np.select(
        [pct.isna(), pct >= r.percentil_alta, pct < r.percentil_baja],
        ["media", "alta", "baja"],
        "media",
    )
    nac = nac.reset_index()[["modelo_color_id", "sell_through", "rotacion"]]
    return mc.merge(nac, on="modelo_color_id", how="left")


def calcular_objetivo_mc(mc: pd.DataFrame, params: EngineParams) -> pd.DataFrame:
    mc = clasificar_rotacion(mc, params)
    pares = mc[["categoria", "rotacion"]].drop_duplicates()
    cob = pd.DataFrame(
        [(c, r, *params.cobertura.para(c, r)) for c, r in pares.itertuples(index=False)],
        columns=["categoria", "rotacion", "cobertura_semanas", "umbral_sobrestock"],
    )
    mc = mc.merge(cob, on=["categoria", "rotacion"], how="left")

    dem = mc["demanda_semanal"].to_numpy()
    mc["objetivo_mc"] = np.floor(dem * mc["cobertura_semanas"] + 0.5).astype("int64")
    pos = mc["stock_disponible"] + mc["stock_transito"]
    mc["cobertura_actual"] = np.where(dem > 0, pos / np.where(dem > 0, dem, 1), np.inf)
    mc["sobrestock"] = (dem > 0) & (mc["cobertura_actual"] > mc["umbral_sobrestock"])
    mc["es_introduccion"] = mc["estado_mc"].eq(NUNCA_TUVO) | (
        mc["estado_mc"].eq(EXPOSICION_INSUFICIENTE) & pos.le(0)
    )
    afin_baja = mc["es_introduccion"] & (mc["afinidad"] < params.afinidad.umbral_introduccion)
    mc["bloqueo_mc"] = np.select(
        [
            mc["fuente_demanda"].eq(FUENTE_SIN_DEMANDA),
            mc["fuente_demanda"].eq(FUENTE_SIN_REFERENCIA),
            afin_baja,
            mc["sobrestock"],
        ],
        [NO_SIN_DEMANDA, NO_SIN_REFERENCIA, NO_AFINIDAD_BAJA, NO_SOBRESTOCK],
        "",
    )
    return mc


MC_A_SKU = [
    "demanda_semanal",
    "fuente_demanda",
    "factor_tendencia",
    "tendencia_significativa",
    "afinidad",
    "rotacion",
    "cobertura_semanas",
    "objetivo_mc",
    "cobertura_actual",
    "sobrestock",
    "es_introduccion",
    "bloqueo_mc",
]


def calcular_necesidad(sku: pd.DataFrame, mc: pd.DataFrame, params: EngineParams) -> pd.DataFrame:
    """Objetivo por talla, necesidad, curva rota y requisito de curva mínima."""
    out = sku.merge(mc[KEY_MC + MC_A_SKU], on=KEY_MC, how="left")
    activo = out["demanda_semanal"].gt(0)
    out["objetivo_curva"] = repartir_hamilton(
        out, "objetivo_mc", "share_talla", KEY_MC, ["talla_orden", "sku"]
    )
    minimo = np.where(out["es_core"] & activo, out["minimo_exhibicion"], 0)
    out["stock_objetivo"] = np.maximum(minimo, out["objetivo_curva"]).astype("int64")
    out["demanda_sku"] = out["demanda_semanal"] * out["share_talla"]

    pos = out["stock_disponible"] + out["stock_transito"]
    bruta = np.maximum(0, out["stock_objetivo"] - np.ceil(pos)).astype("int64")
    tope = params.tope_tienda.max_unidades_por_sku
    bloqueada = out["bloqueo_mc"].ne("")
    out["necesidad_bruta"] = bruta
    out["necesidad"] = np.where(bloqueada, 0, np.minimum(bruta, tope)).astype("int64")
    out["tope_sku_aplicado"] = ~bloqueada & (bruta > tope)
    out["motivo_bloqueo"] = np.where(
        bloqueada, out["bloqueo_mc"], np.where(bruta == 0, NO_SIN_NECESIDAD, "")
    )

    # Curva rota: MC activo que ya está en la tienda y le faltan tallas core.
    falta_core = out["es_core"] & pos.le(0)
    g = out.groupby(KEY_MC, sort=False)
    n_core = g["es_core"].transform("sum")
    n_falta = falta_core.groupby([out[c] for c in KEY_MC], sort=False).transform("sum")
    rota = activo & ~out["es_introduccion"] & (n_core > 0) & (n_falta > 0)
    out["curva_rota"] = rota
    out["talla_core_faltante"] = rota & falta_core

    # Curva mínima para introducir: tallas core; si el modelo no fabrica core,
    # las N tallas con mayor share.
    out["requerido_curva"] = False
    out["req_curva"] = 0
    intro = out["es_introduccion"] & ~bloqueada
    if intro.any():
        sub = out.loc[intro]
        tiene_core = sub.groupby(KEY_MC, sort=False)["es_core"].transform("any")
        n_min = params.exhibicion.tallas_minimas_sin_core
        orden = sub.sort_values(
            KEY_MC + ["share_talla", "talla_orden"],
            ascending=[True, True, False, True],
            kind="mergesort",
        )
        rank = orden.groupby(KEY_MC, sort=False).cumcount().reindex(sub.index)
        req = np.where(tiene_core, sub["es_core"], rank < n_min)
        out.loc[sub.index, "requerido_curva"] = req
        out.loc[sub.index, "req_curva"] = np.where(
            req, np.maximum(sub["minimo_exhibicion"], 1), 0
        ).astype("int64")
    return out
