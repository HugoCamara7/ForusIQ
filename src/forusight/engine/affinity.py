"""Afinidad tienda×modelo-color en [0, 1].

A1 historia propia: velocidad propia vs promedio nacional (0 si TUVO_SIN_VENTA).
A2 índice de la tienda en categoría, género y rango de precio (mix vs nacional).
A3 modelos similares (misma categoría×género×rango, excluyendo el propio MC).
A4 desempeño del MC en tiendas similares vs nacional.
A5 presencia histórica: fracción de semanas con exposición al mismo modelo (cualquier color).
Los componentes no disponibles se omiten y los pesos se renormalizan. Índices relativos
se llevan a [0, 1) con r / (1 + r) (r = 1 → 0.5).
Penalización fuerte si TUVO_SIN_VENTA con exposición suficiente.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from forusight.config.settings import EngineParams
from forusight.engine.common import (
    ESTADOS_CON_HISTORIA_VENTA,
    TUVO_SIN_VENTA,
    ratio_a_score,
    safe_div,
)

COMPONENTES = ["a1", "a2", "a3", "a4", "a5"]


def _indice_mix(semanal: pd.DataFrame, dim: str) -> pd.DataFrame:
    """share_tienda(dim) / share_nacional(dim) por (tienda_id, dim)."""
    vt = semanal.groupby("tienda_id")["unidades"].sum()
    vtd = semanal.groupby(["tienda_id", dim])["unidades"].sum().rename("v").reset_index()
    vn = semanal.groupby(dim)["unidades"].sum()
    tot = vn.sum()
    vtd["share_t"] = safe_div(vtd["v"], vtd["tienda_id"].map(vt), np.nan)
    vtd["share_n"] = safe_div(vtd[dim].map(vn), tot, np.nan)
    vtd[f"idx_{dim}"] = safe_div(vtd["share_t"], vtd["share_n"], np.nan)
    return vtd[["tienda_id", dim, f"idx_{dim}"]]


def calcular_afinidad(
    mc: pd.DataFrame, semanal: pd.DataFrame, params: EngineParams
) -> pd.DataFrame:
    a = params.afinidad
    n_sem = params.horizonte.semanas_analisis
    out = mc.copy()
    est = out["estado_mc"]

    # A1
    r1 = safe_div(out["demanda_historica"], out["vel_nacional"].fillna(0), np.nan)
    out["a1"] = np.where(
        est.isin(ESTADOS_CON_HISTORIA_VENTA),
        ratio_a_score(np.nan_to_num(r1, nan=1.0)),
        np.where(est.eq(TUVO_SIN_VENTA), 0.0, np.nan),
    )

    # A2: promedio de índices disponibles; tienda sin venta → NaN
    idx_cols = []
    for dim in ("categoria", "genero", "rango_precio"):
        out = out.merge(_indice_mix(semanal, dim), on=["tienda_id", dim], how="left")
        idx_cols.append(f"idx_{dim}")
    vt = semanal.groupby("tienda_id")["unidades"].sum()
    tiene_venta = out["tienda_id"].map(vt).fillna(0).gt(0)
    idx = out[idx_cols].fillna(0.0)  # la tienda vende, pero nada de esa dimensión → 0
    out["a2"] = np.where(tiene_venta, ratio_a_score(idx.mean(axis=1)), np.nan)

    # A3: modelos similares excluyendo el propio MC (mix de la tienda vs nacional)
    g = ["categoria", "genero", "rango_precio"]
    v_tg = semanal.groupby(["tienda_id"] + g)["unidades"].sum().rename("v_tg")
    v_tmc = semanal.groupby(["tienda_id", "modelo_color_id"])["unidades"].sum().rename("v_tmc")
    v_g = semanal.groupby(g)["unidades"].sum().rename("v_g")
    v_mc = semanal.groupby("modelo_color_id")["unidades"].sum().rename("v_mc")
    out = out.join(v_tg, on=["tienda_id"] + g).join(v_tmc, on=["tienda_id", "modelo_color_id"])
    out = out.join(v_g, on=g).join(v_mc, on="modelo_color_id")
    for c in ("v_tg", "v_tmc", "v_g", "v_mc"):
        out[c] = out[c].fillna(0.0)
    tot_n = semanal["unidades"].sum()
    share_t = safe_div(out["v_tg"] - out["v_tmc"], out["tienda_id"].map(vt).fillna(0), np.nan)
    share_n = safe_div(out["v_g"] - out["v_mc"], tot_n, np.nan)
    r3 = safe_div(share_t, np.nan_to_num(share_n, nan=0.0), np.nan)
    out["a3"] = np.where(np.isnan(r3), np.nan, ratio_a_score(np.nan_to_num(r3)))

    # A4: tiendas similares vs nacional
    vn = out["vel_nacional"]
    r4 = safe_div(out["vel_similares"].fillna(0), vn.fillna(0), 0.0)
    out["a4"] = np.where(out["vel_similares"].isna() | vn.isna(), np.nan, ratio_a_score(r4))
    out.loc[vn.eq(0) & out["vel_similares"].notna(), "a4"] = 0.0

    # A5: presencia histórica del modelo (cualquier color)
    pres = (
        semanal.loc[semanal["dias_con_stock"] > 0, ["tienda_id", "modelo_id", "rel"]]
        .drop_duplicates()
        .groupby(["tienda_id", "modelo_id"])
        .size()
        .rename("_sem_modelo")
    )
    out = out.join(pres, on=["tienda_id", "modelo_id"])
    out["a5"] = (out["_sem_modelo"].fillna(0) / n_sem).clip(0, 1)

    pesos = a.pesos
    w = np.array(
        [
            pesos.a1_historia_propia,
            pesos.a2_indice_tienda,
            pesos.a3_modelos_similares,
            pesos.a4_tiendas_similares,
            pesos.a5_presencia_historica,
        ]
    )
    comp = out[COMPONENTES].to_numpy(dtype=float)
    disp = ~np.isnan(comp)
    score = safe_div((np.nan_to_num(comp) * w).sum(axis=1), (disp * w).sum(axis=1), 0.0)

    penaliza = est.eq(TUVO_SIN_VENTA) & (
        out["dias_12s"] >= params.disponibilidad.dias_min_exposicion
    )
    score = np.where(penaliza, score * a.penalizacion_sin_venta, score)
    out["afinidad"] = np.clip(score, 0.0, 1.0)
    out["afinidad_penalizada"] = penaliza.to_numpy()
    drop = idx_cols + ["v_tg", "v_tmc", "v_g", "v_mc", "_sem_modelo"]
    return out.drop(columns=drop)
