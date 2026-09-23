"""Estados de disponibilidad (ventana de 12 semanas) a nivel tienda×MC y tienda×SKU.

Reglas (en orden):
  1. venta > 0:
       stock en mano == 0 o bajo el mínimo de exhibición → QUIEBRE
       en otro caso                                      → TUVO_Y_VENDE
     "Bajo el mínimo": por SKU, stock < mínimo de la talla core; por modelo-color,
     al menos ``quiebre_fraccion_core`` de sus tallas core bajo su mínimo (si faltan
     menos tallas core, el caso es "curva rota", no quiebre).
  2. venta == 0:
       sin días con stock y sin stock hoy            → NUNCA_TUVO
       días con stock < dias_min_exposicion          → EXPOSICION_INSUFICIENTE
       en otro caso                                  → TUVO_SIN_VENTA
Sólo en TUVO_SIN_VENTA un 0 en ventas se interpreta como "sin demanda".
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from forusight.config.settings import EngineParams
from forusight.engine.common import (
    EXPOSICION_INSUFICIENTE,
    KEY_MC,
    NUNCA_TUVO,
    QUIEBRE,
    TUVO_SIN_VENTA,
    TUVO_Y_VENDE,
)


def marcar_tallas_core(sku: pd.DataFrame, params: EngineParams) -> pd.DataFrame:
    """Agrega ``es_core`` y ``minimo_exhibicion`` (sólo tallas core) por fila."""
    ex = params.exhibicion
    pares = sku[["categoria", "genero"]].drop_duplicates()
    filas = [
        (c, g, t, ex.minimo(c))
        for c, g in pares.itertuples(index=False)
        for t in ex.tallas_core(c, g)
    ]
    core = pd.DataFrame(filas, columns=["categoria", "genero", "talla", "_min"])
    out = sku.merge(core, on=["categoria", "genero", "talla"], how="left")
    out["es_core"] = out["_min"].notna()
    out["minimo_exhibicion"] = out["_min"].fillna(0).astype("int64")
    return out.drop(columns="_min")


def clasificar(
    venta: np.ndarray,
    dias: np.ndarray,
    stock: np.ndarray,
    bajo_minimo: np.ndarray,
    dias_min: int,
) -> np.ndarray:
    """Clasificación vectorizada (ver docstring del módulo)."""
    vende = venta > 0
    quiebre = vende & ((stock <= 0) | bajo_minimo)
    nunca = ~vende & (dias <= 0) & (stock <= 0)
    insuf = ~vende & ~nunca & (dias < dias_min)
    return np.select(
        [quiebre, vende, nunca, insuf],
        [QUIEBRE, TUVO_Y_VENDE, NUNCA_TUVO, EXPOSICION_INSUFICIENTE],
        default=TUVO_SIN_VENTA,
    )


def estados_disponibilidad(
    sku: pd.DataFrame, semanal: pd.DataFrame, params: EngineParams
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Devuelve (sku con estado_sku/es_core, tabla MC con estado_mc y agregados)."""
    dias_min = params.disponibilidad.dias_min_exposicion
    frac = params.disponibilidad.quiebre_fraccion_core
    sku = marcar_tallas_core(sku, params)
    sku["estado_sku"] = clasificar(
        sku["venta_12s"].to_numpy(),
        sku["dias_12s"].to_numpy(),
        sku["stock_disponible"].to_numpy(),
        (sku["stock_disponible"] < sku["minimo_exhibicion"]).to_numpy(),
        dias_min,
    )
    sku["_core_bajo_min"] = sku["es_core"] & (sku["stock_disponible"] < sku["minimo_exhibicion"])

    # Exposición del MC por semana = máximo de días con stock entre sus tallas.
    sem_mc = semanal.groupby(KEY_MC + ["rel"], sort=False)["dias_con_stock"].max()
    dias_mc = sem_mc.groupby(level=KEY_MC).sum().rename("dias_12s")
    semanas_mc = (sem_mc > 0).groupby(level=KEY_MC).sum().rename("semanas_expuestas")

    attrs = ["modelo_id", "categoria", "genero", "rango_precio", "cluster", "importancia_comercial"]
    mc = sku.groupby(KEY_MC, sort=True).agg(
        **{a: (a, "first") for a in attrs},
        venta_12s=("venta_12s", "sum"),
        venta_4s=("venta_4s", "sum"),
        stock_disponible=("stock_disponible", "sum"),
        stock_transito=("stock_transito", "sum"),
        minimo_exhibicion=("minimo_exhibicion", "sum"),
        n_tallas=("sku", "size"),
        n_core=("es_core", "sum"),
        n_core_bajo_min=("_core_bajo_min", "sum"),
        cd_disponible=("cd_disponible", "sum"),
    )
    mc = mc.join(dias_mc).join(semanas_mc)
    mc[["dias_12s", "semanas_expuestas"]] = mc[["dias_12s", "semanas_expuestas"]].fillna(0)
    mc["estado_mc"] = clasificar(
        mc["venta_12s"].to_numpy(),
        mc["dias_12s"].to_numpy(),
        mc["stock_disponible"].to_numpy(),
        ((mc["n_core"] > 0) & (mc["n_core_bajo_min"] >= frac * mc["n_core"])).to_numpy(),
        dias_min,
    )
    mc = mc.reset_index()
    sku = sku.drop(columns="_core_bajo_min")
    sku = sku.merge(mc[KEY_MC + ["estado_mc"]], on=KEY_MC, how="left")
    return sku, mc
