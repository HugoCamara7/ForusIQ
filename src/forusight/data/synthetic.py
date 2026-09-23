"""Generador de datos sintéticos que cumplen los contratos canónicos.

Sirve para tests, para el modo demo de la app (sin BigQuery) y para calibrar parámetros.
Determinista dado ``seed``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from forusight.engine.pipeline import EngineInputs

CATEGORIAS = ["ZAPATO", "SANDALIA", "BOTA", "ZAPATILLA"]
COLORES = ["NEGRO", "MARRON", "BLANCO", "ROJO", "AZUL"]
TALLAS = {"DAMA": list(range(35, 41)), "CABALLERO": list(range(38, 44))}
CURVA = {
    "DAMA": [0.06, 0.16, 0.26, 0.26, 0.18, 0.08],
    "CABALLERO": [0.06, 0.14, 0.24, 0.26, 0.20, 0.10],
}


@dataclass
class ConfigSintetica:
    seed: int = 7
    n_tiendas: int = 24
    n_modelos: int = 16
    semanas: int = 16
    fecha_corte: str = "2026-09-21"  # lunes de la semana en curso
    prob_tiene: float = 0.55
    prob_quiebre: float = 0.12
    prob_sin_cluster: float = 0.1


def generar(cfg: ConfigSintetica | None = None) -> tuple[EngineInputs, pd.Timestamp]:
    cfg = cfg or ConfigSintetica()
    rng = np.random.default_rng(cfg.seed)
    corte = pd.Timestamp(cfg.fecha_corte)

    # ---------------- tiendas
    tids = [f"T{i:03d}" for i in range(1, cfg.n_tiendas + 1)]
    clusters = rng.choice(["A", "B", "C"], size=cfg.n_tiendas, p=[0.3, 0.45, 0.25])
    clusters = np.where(rng.random(cfg.n_tiendas) < cfg.prob_sin_cluster, None, clusters)
    tamano = rng.lognormal(0, 0.45, cfg.n_tiendas)
    dim_tienda = pd.DataFrame(
        {
            "tienda_id": tids,
            "nombre": [f"Tienda {t}" for t in tids],
            "cluster": clusters,
            "formato": rng.choice(["MALL", "CALLE", "OUTLET"], size=cfg.n_tiendas),
            "importancia_comercial": np.round(np.clip(tamano / tamano.max(), 0.05, 1), 3),
            "activa": [True] * (cfg.n_tiendas - 1) + [False],
            "max_unidades_corrida": [np.nan] * (cfg.n_tiendas - 2) + [40.0, np.nan],
        }
    )
    afin_cluster_cat = {
        (c, cat): rng.uniform(0.4, 1.6) for c in ["A", "B", "C", None] for cat in CATEGORIAS
    }

    # ---------------- productos
    filas = []
    for m in range(1, cfg.n_modelos + 1):
        genero = "DAMA" if rng.random() < 0.7 else "CABALLERO"
        cat = CATEGORIAS[m % len(CATEGORIAS)]
        rango = rng.choice(["BAJO", "MEDIO", "ALTO"])
        tallas = TALLAS[genero]
        if rng.random() < 0.2:  # modelo que no fabrica la talla más grande
            tallas = tallas[:-1]
        for c in rng.choice(COLORES, size=rng.integers(1, 4), replace=False):
            mc = f"M{m:03d}-{c[:3]}"
            for t in tallas:
                filas.append(
                    {
                        "sku": f"{mc}-{t}",
                        "modelo_id": f"M{m:03d}",
                        "modelo_color_id": mc,
                        "color": c,
                        "talla": str(t),
                        "talla_orden": float(t),
                        "categoria": cat,
                        "genero": genero,
                        "rango_precio": rango,
                    }
                )
    dim_producto = pd.DataFrame(filas)
    popularidad = {mc: rng.lognormal(0, 0.6) for mc in dim_producto["modelo_color_id"].unique()}

    # ---------------- ventas semanales y stock
    semanas = [corte - pd.Timedelta(weeks=k) for k in range(cfg.semanas, 0, -1)]
    v_rows, s_rows = [], []
    for ti, t in enumerate(tids):
        for mc, grp in dim_producto.groupby("modelo_color_id", sort=True):
            if rng.random() > cfg.prob_tiene:
                continue
            gen = grp["genero"].iat[0]
            cat = grp["categoria"].iat[0]
            curva = np.array(CURVA[gen][: len(grp)])
            curva = curva / curva.sum()
            lam = 1.6 * tamano[ti] * popularidad[mc] * afin_cluster_cat[(clusters[ti], cat)]
            lam *= 0.0 if rng.random() < 0.08 else 1.0  # tiendas donde el modelo no vende
            inicio = rng.integers(0, cfg.semanas // 2)
            quiebre = rng.random() < cfg.prob_quiebre
            for (_, p), share in zip(grp.iterrows(), curva, strict=True):
                for k, sem in enumerate(semanas):
                    if k < inicio:
                        continue
                    dias = 7 if rng.random() > 0.15 else int(rng.integers(0, 7))
                    if quiebre and k >= cfg.semanas - 2:
                        dias = 0
                    u = rng.poisson(lam * share * dias / 7.0)
                    if dias > 0 or u > 0:
                        v_rows.append((sem, t, p["sku"], float(u), dias))
                stock = 0.0 if quiebre else float(rng.poisson(max(lam * share * 5, 1.0)))
                if not quiebre and rng.random() < 0.1:
                    stock = 0.0  # talla faltante → curva rota
                transito = float(rng.poisson(0.3)) if rng.random() < 0.1 else 0.0
                s_rows.append((t, p["sku"], stock, transito))
    ventas = pd.DataFrame(
        v_rows, columns=["semana_inicio", "tienda_id", "sku", "unidades", "dias_con_stock"]
    )
    stock_tienda = pd.DataFrame(
        s_rows, columns=["tienda_id", "sku", "stock_disponible", "stock_transito"]
    )

    # ---------------- CD 320
    skus = dim_producto["sku"].to_numpy()
    fisico = rng.poisson(25, len(skus)).astype(float)
    fisico[rng.random(len(skus)) < 0.12] = 0.0  # SKUs sin stock en CD
    escaso = rng.random(len(skus)) < 0.3
    fisico[escaso] = rng.integers(0, 6, escaso.sum())
    reservado = np.minimum(fisico, rng.poisson(1, len(skus)))
    comprometido = np.minimum(fisico - reservado, rng.poisson(1, len(skus)))
    stock_cd = pd.DataFrame(
        {
            "sku": skus,
            "fisico": fisico,
            "reservado": reservado.astype(float),
            "comprometido": comprometido.astype(float),
        }
    )

    return EngineInputs(
        ventas=ventas,
        stock_tienda=stock_tienda,
        stock_cd=stock_cd,
        dim_producto=dim_producto,
        dim_tienda=dim_tienda,
    ), corte
