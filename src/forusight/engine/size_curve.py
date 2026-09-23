"""Curva de tallas por tienda×MC con suavizado bayesiano y redondeo de Hamilton.

share_talla = (n·local + k·prior) / (n + k)
  - local: tasa corregida por disponibilidad de cada talla (venta corregida / semanas
    expuestas de esa talla). Tallas sin exposición local se imputan con el prior.
  - n: pares vendidos del MC en la tienda (ventana); k: fuerza del prior.
  - prior (primer nivel con volumen suficiente):
      tienda×categoría×género → cluster×modelo → cluster×categoría → nacional
      (categoría×género) → uniforme.
Sólo se consideran tallas que el modelo fabrica (las filas de dim_producto).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from forusight.config.settings import EngineParams
from forusight.engine.common import KEY_MC, factor_exposicion

NIVELES_PRIOR: list[tuple[str, list[str]]] = [
    ("TIENDA_CATEGORIA_GENERO", ["tienda_id", "categoria", "genero"]),
    ("CLUSTER_MODELO", ["cluster", "modelo_id"]),
    ("CLUSTER_CATEGORIA", ["cluster", "categoria"]),
    ("NACIONAL", ["categoria", "genero"]),
]


# ------------------------------------------------------------------ Hamilton


def hamilton(total: int, shares, orden=None) -> np.ndarray:
    """Reparte ``total`` unidades según ``shares`` (resto mayor). Suma exacta.

    Desempate determinista: mayor resto, luego menor ``orden`` (por defecto, posición).
    """
    total = int(total)
    s = np.asarray(shares, dtype=float)
    if total < 0:
        raise ValueError("total debe ser >= 0")
    if s.size == 0:
        if total:
            raise ValueError("no hay tallas para repartir")
        return np.zeros(0, dtype=np.int64)
    s = np.clip(s, 0, None)
    s = s / s.sum() if s.sum() > 0 else np.full(s.size, 1.0 / s.size)
    raw = total * s
    base = np.floor(raw).astype(np.int64)
    frac = np.round(raw - base, 12)
    faltan = total - int(base.sum())
    orden = np.arange(s.size) if orden is None else np.asarray(orden)
    idx = np.lexsort((orden, -frac))[:faltan]
    base[idx] += 1
    return base


def repartir_hamilton(
    df: pd.DataFrame, total_col: str, share_col: str, group_cols: list[str], orden_cols: list[str]
) -> pd.Series:
    """Versión vectorizada: ``total_col`` es el total del grupo repetido en cada fila."""
    g = df[group_cols + orden_cols].copy()
    ssum = df.groupby(group_cols, sort=False)[share_col].transform("sum")
    n = df.groupby(group_cols, sort=False)[share_col].transform("size")
    share = np.where(ssum > 0, df[share_col] / ssum.where(ssum > 0, 1), 1.0 / n)
    total = df[total_col].astype("int64").to_numpy()
    raw = total * share
    base = np.floor(raw + 1e-12).astype(np.int64)
    g["_base"] = base
    g["_frac"] = np.round(raw - base, 12)
    faltan = pd.Series(total, index=df.index) - g.groupby(group_cols, sort=False)[
        "_base"
    ].transform("sum")
    g = g.sort_values(
        group_cols + ["_frac"] + orden_cols,
        ascending=[True] * len(group_cols) + [False] + [True] * len(orden_cols),
        kind="mergesort",
    )
    rank = g.groupby(group_cols, sort=False).cumcount()
    extra = (rank.to_numpy() < faltan.loc[g.index].to_numpy()).astype(np.int64)
    out = pd.Series(base, index=df.index)
    out.loc[g.index] += extra
    return out


# ------------------------------------------------------------------ curva


def _corregida_sku(semanal: pd.DataFrame, params: EngineParams) -> pd.DataFrame:
    w = semanal.loc[(semanal["dias_con_stock"] > 0) | (semanal["unidades"] > 0)].copy()
    w["corr"] = w["unidades"] * factor_exposicion(w["dias_con_stock"], params)
    return w


def curva_tallas(sku: pd.DataFrame, semanal: pd.DataFrame, params: EngineParams) -> pd.DataFrame:
    """Agrega ``share_talla`` y ``nivel_prior`` a cada fila tienda×SKU."""
    k = params.curva.k_prior
    n_min = params.curva.n_min_nivel_prior
    out = sku.copy()
    w = _corregida_sku(semanal, params)
    w = w.merge(out[["tienda_id", "cluster"]].drop_duplicates(), on="tienda_id", how="left")

    # --- prior jerárquico
    prior = np.full(len(out), np.nan)
    nivel = np.full(len(out), "UNIFORME", dtype=object)
    pendiente = np.ones(len(out), dtype=bool)
    grp = [out[c] for c in KEY_MC]
    for nombre, keys in NIVELES_PRIOR:
        p = w.groupby(keys + ["talla"], dropna=True)["corr"].sum().rename("_p")
        m = out[keys + ["talla"]].join(p, on=keys + ["talla"])["_p"].fillna(0.0)
        if "cluster" in keys:
            m = m.where(out["cluster"].notna(), 0.0)
        tot = m.groupby(grp, sort=False).transform("sum")
        ok = (tot.to_numpy() >= max(n_min, 1e-9)) & pendiente
        prior = np.where(ok, m.to_numpy() / np.where(tot > 0, tot, 1), prior)
        nivel = np.where(ok, nombre, nivel)
        pendiente &= ~ok
    n_tallas = out.groupby(KEY_MC, sort=False)["sku"].transform("size").to_numpy()
    prior = np.where(pendiente, 1.0 / n_tallas, prior)
    out["prior_talla"] = prior
    out["nivel_prior"] = nivel

    # --- local: tasa corregida por semana expuesta de cada talla
    loc = w.groupby(["tienda_id", "sku"]).agg(
        _c=("corr", "sum"), _n=("corr", "size"), _u=("unidades", "sum")
    )
    out = out.join(loc, on=["tienda_id", "sku"])
    tasa = (out["_c"] / out["_n"]).fillna(0.0)
    observada = out["_n"].fillna(0).gt(0)
    s_tasa = tasa.where(observada, 0.0).groupby(grp, sort=False).transform("sum")
    s_prior_obs = out["prior_talla"].where(observada, 0.0).groupby(grp, sort=False).transform("sum")
    escala = np.where(s_prior_obs > 0, s_tasa / s_prior_obs.where(s_prior_obs > 0, 1), 0.0)
    local = np.where(observada, tasa, out["prior_talla"] * escala)
    local = pd.Series(local, index=out.index)
    s_local = local.groupby(grp, sort=False).transform("sum")
    local_share = np.where(s_local > 0, local / s_local.where(s_local > 0, 1), out["prior_talla"])

    n = out["_u"].fillna(0.0).groupby(grp, sort=False).transform("sum").to_numpy()
    den = n + k
    share = np.where(
        den > 0,
        (n * local_share + k * out["prior_talla"]) / np.where(den > 0, den, 1),
        out["prior_talla"],
    )
    share = pd.Series(share, index=out.index)
    out["share_talla"] = share / share.groupby(grp, sort=False).transform("sum")
    out["n_curva"] = n
    return out.drop(columns=["_c", "_n", "_u"])
