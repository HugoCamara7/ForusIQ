"""Demanda semanal estimada a nivel tienda×modelo-color.

1. Semanas sin exposición (0 días con stock y 0 venta) se excluyen.
2. Exposición parcial: venta / max(dias/7, 0.3), con tope de corrección 2x.
3. Tasas por bloque (4S, 5-8S, 9-12S) = venta corregida / semanas expuestas del bloque.
4. Pesos 0.5/0.3/0.2 sólo si volumen >= 8 pares y la tendencia es significativa;
   si no, tasa plana (12S).
5. Tendencia = tasa 4S / tasa 12S; factor acotado a [0.85, 1.2] (sólo si significativa).
6. Sin evidencia en la tienda: velocidad en tiendas similares × índice de la tienda
   en la categoría × 0.7.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from forusight.config.settings import EngineParams
from forusight.engine.common import (
    ESTADOS_CON_EVIDENCIA,
    ESTADOS_CON_HISTORIA_VENTA,
    KEY_MC,
    TUVO_SIN_VENTA,
    factor_exposicion,
    safe_div,
)

FUENTE_HISTORIA = "HISTORIA"
FUENTE_SIN_DEMANDA = "SIN_DEMANDA"
FUENTE_SIMILARES = "SIMILARES"
FUENTE_NACIONAL = "NACIONAL"
FUENTE_SIN_REFERENCIA = "SIN_REFERENCIA"


def semanal_mc(semanal: pd.DataFrame) -> pd.DataFrame:
    """Serie semanal tienda×MC: unidades sumadas, días = máximo entre tallas."""
    return (
        semanal.groupby(KEY_MC + ["rel", "bloque"], sort=False)
        .agg(unidades=("unidades", "sum"), dias_con_stock=("dias_con_stock", "max"))
        .reset_index()
    )


def demanda_historica(semanal: pd.DataFrame, params: EngineParams) -> pd.DataFrame:
    """Tasa semanal corregida por exposición, con pesos y tendencia. Índice = KEY_MC."""
    d = params.demanda
    w = semanal_mc(semanal)
    w = w.loc[(w["dias_con_stock"] > 0) | (w["unidades"] > 0)].copy()
    w["corr"] = w["unidades"] * factor_exposicion(w["dias_con_stock"], params)

    blk = w.groupby(KEY_MC + ["bloque"], sort=False).agg(corr=("corr", "sum"), n=("corr", "size"))
    tasa_b = (blk["corr"] / blk["n"]).unstack("bloque")
    tasa_b = tasa_b.reindex(columns=[1, 2, 3])
    tot = w.groupby(KEY_MC, sort=False).agg(
        corr=("corr", "sum"), n=("corr", "size"), venta=("unidades", "sum")
    )
    out = pd.DataFrame(index=tot.index)
    out["tasa_12s"] = tot["corr"] / tot["n"]
    out["tasa_4s"] = tasa_b[1].reindex(out.index)
    out["semanas_expuestas_hist"] = tot["n"]

    ratio = safe_div(out["tasa_4s"].fillna(np.nan), out["tasa_12s"], default=1.0)
    ratio = np.where(out["tasa_4s"].isna(), 1.0, ratio)
    out["tendencia_ratio"] = ratio
    significativa = (
        (tot["venta"].to_numpy() >= d.volumen_min_pesos)
        & (np.abs(ratio - 1.0) >= d.umbral_tendencia)
        & out["tasa_4s"].notna().to_numpy()
    )
    out["tendencia_significativa"] = significativa

    pesos = np.array(d.pesos_bloques, dtype=float)
    tb = tasa_b.reindex(out.index).to_numpy()
    disponible = ~np.isnan(tb)
    wsum = (disponible * pesos).sum(axis=1)
    ponderada = safe_div(np.nansum(np.nan_to_num(tb) * pesos, axis=1), wsum, default=0.0)
    base = np.where(significativa, ponderada, out["tasa_12s"].to_numpy())

    factor = np.clip(ratio, d.tendencia_min, d.tendencia_max)
    factor = np.where(significativa & d.aplicar_factor_tendencia, factor, 1.0)
    out["factor_tendencia"] = factor
    out["demanda_historica"] = base * factor
    return out


def _indice_categoria(
    semanal: pd.DataFrame, similares: pd.DataFrame, tiendas: pd.Series, params: EngineParams
) -> pd.DataFrame:
    """Índice de la tienda en la categoría vs sus similares y vs el total nacional."""
    d = params.demanda
    vc = semanal.groupby(["tienda_id", "categoria"])["unidades"].sum()
    cats = semanal["categoria"].unique()
    full = pd.MultiIndex.from_product(
        [sorted(tiendas), sorted(cats)], names=["tienda_id", "categoria"]
    )
    vc = vc.reindex(full, fill_value=0.0).rename("venta_cat").reset_index()

    nac = vc.groupby("categoria")["venta_cat"].mean().rename("media_nac")
    sim = similares.merge(
        vc.rename(columns={"tienda_id": "tienda_similar", "venta_cat": "v_s"}), on="tienda_similar"
    )
    media_sim = sim.groupby(["tienda_id", "categoria"])["v_s"].mean().rename("media_sim")
    vc = vc.join(nac, on="categoria").join(media_sim, on=["tienda_id", "categoria"])
    lo, hi = d.indice_categoria_min, d.indice_categoria_max
    vc["indice_sim"] = np.clip(safe_div(vc["venta_cat"], vc["media_sim"].fillna(0), 1.0), lo, hi)
    vc["indice_nac"] = np.clip(safe_div(vc["venta_cat"], vc["media_nac"], 1.0), lo, hi)
    return vc[["tienda_id", "categoria", "indice_sim", "indice_nac"]]


def estimar_demanda(
    mc: pd.DataFrame,
    semanal: pd.DataFrame,
    similares: pd.DataFrame,
    params: EngineParams,
) -> pd.DataFrame:
    """Agrega a ``mc``: demanda_semanal, fuente_demanda, tasas, tendencia, velocidades ref."""
    hist = demanda_historica(semanal, params)
    mc = mc.merge(hist.reset_index(), on=KEY_MC, how="left")

    con_hist = mc["estado_mc"].isin(ESTADOS_CON_HISTORIA_VENTA)
    mc["demanda_historica"] = np.where(con_hist, mc["demanda_historica"].fillna(0.0), 0.0)
    for c, default in (
        ("factor_tendencia", 1.0),
        ("tendencia_ratio", 1.0),
        ("tasa_4s", 0.0),
        ("tasa_12s", 0.0),
    ):
        mc[c] = mc[c].fillna(default)
    mc["tendencia_significativa"] = mc["tendencia_significativa"].fillna(False).astype(bool)

    # Referencias: tiendas con evidencia (incluye los ceros verdaderos de TUVO_SIN_VENTA).
    ref = mc.loc[mc["estado_mc"].isin(ESTADOS_CON_EVIDENCIA), KEY_MC + ["demanda_historica"]]
    vel_nac = ref.groupby("modelo_color_id")["demanda_historica"].mean().rename("vel_nacional")
    sim = similares.merge(ref.rename(columns={"tienda_id": "tienda_similar"}), on="tienda_similar")
    vel_sim = sim.groupby(KEY_MC)["demanda_historica"].mean().rename("vel_similares")
    mc = mc.join(vel_nac, on="modelo_color_id").join(vel_sim, on=KEY_MC)

    idx = _indice_categoria(semanal, similares, mc["tienda_id"].unique(), params)
    mc = mc.merge(idx, on=["tienda_id", "categoria"], how="left")
    mc[["indice_sim", "indice_nac"]] = mc[["indice_sim", "indice_nac"]].fillna(1.0)

    f = params.demanda.factor_sin_historia
    sin_ev = ~mc["estado_mc"].isin(ESTADOS_CON_EVIDENCIA)
    usa_sim = sin_ev & mc["vel_similares"].notna()
    usa_nac = sin_ev & ~usa_sim & mc["vel_nacional"].notna()
    mc["indice_categoria"] = np.select(
        [usa_sim, usa_nac], [mc["indice_sim"], mc["indice_nac"]], 1.0
    )
    mc["demanda_semanal"] = np.select(
        [con_hist, mc["estado_mc"].eq(TUVO_SIN_VENTA), usa_sim, usa_nac],
        [
            mc["demanda_historica"],
            0.0,
            mc["vel_similares"] * mc["indice_sim"] * f,
            mc["vel_nacional"] * mc["indice_nac"] * f,
        ],
        0.0,
    )
    mc["fuente_demanda"] = np.select(
        [con_hist, mc["estado_mc"].eq(TUVO_SIN_VENTA), usa_sim, usa_nac],
        [FUENTE_HISTORIA, FUENTE_SIN_DEMANDA, FUENTE_SIMILARES, FUENTE_NACIONAL],
        FUENTE_SIN_REFERENCIA,
    )
    return mc.drop(columns=["indice_sim", "indice_nac"])
