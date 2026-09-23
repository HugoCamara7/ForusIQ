"""Tiendas similares: mismo cluster o, si no hay cluster, k vecinos por coseno del mix."""

from __future__ import annotations

import numpy as np
import pandas as pd

from forusight.config.settings import EngineParams


def mix_tiendas(semanal: pd.DataFrame, tiendas: pd.Series) -> pd.DataFrame:
    """Participación de venta por categoría×género de cada tienda (filas = tiendas)."""
    mix = semanal.pivot_table(
        index="tienda_id",
        columns=["categoria", "genero"],
        values="unidades",
        aggfunc="sum",
        fill_value=0.0,
    )
    mix = mix.reindex(pd.Index(sorted(tiendas), name="tienda_id"), fill_value=0.0)
    tot = mix.sum(axis=1).replace(0, np.nan)
    return mix.div(tot, axis=0).fillna(0.0)


def tiendas_similares(
    dim_tienda: pd.DataFrame, semanal: pd.DataFrame, params: EngineParams
) -> pd.DataFrame:
    """Pares (tienda_id, tienda_similar), sin incluir la propia tienda."""
    t = dim_tienda[["tienda_id", "cluster"]].copy()
    pares = []

    con_cluster = t.loc[t["cluster"].notna()]
    tam = con_cluster.groupby("cluster")["tienda_id"].transform("size")
    grupo = con_cluster.loc[tam > 1]
    if not grupo.empty:
        p = grupo.merge(grupo, on="cluster", suffixes=("", "_s"))
        p = p.loc[p["tienda_id"] != p["tienda_id_s"]]
        pares.append(
            p[["tienda_id", "tienda_id_s"]].rename(columns={"tienda_id_s": "tienda_similar"})
        )

    sin_cluster = sorted(set(t["tienda_id"]) - set(grupo["tienda_id"]))
    if sin_cluster and len(t) > 1:
        mix = mix_tiendas(semanal, t["tienda_id"])
        m = mix.to_numpy()
        norm = np.linalg.norm(m, axis=1)
        idx = {tid: i for i, tid in enumerate(mix.index)}
        k = params.similitud.k_vecinos
        for tid in sin_cluster:
            i = idx[tid]
            if norm[i] == 0:
                continue
            sim = (m @ m[i]) / np.where(norm == 0, np.inf, norm * norm[i])
            sim[i] = -np.inf
            # desempate determinista: similitud desc, luego tienda_id asc
            orden = sorted(
                (j for j in range(len(sim)) if sim[j] > 0), key=lambda j: (-sim[j], mix.index[j])
            )[:k]
            pares.extend(
                pd.DataFrame({"tienda_id": [tid], "tienda_similar": [mix.index[j]]}) for j in orden
            )

    if not pares:
        return pd.DataFrame(
            {"tienda_id": pd.Series(dtype=str), "tienda_similar": pd.Series(dtype=str)}
        )
    return pd.concat(pares, ignore_index=True).drop_duplicates().reset_index(drop=True)
