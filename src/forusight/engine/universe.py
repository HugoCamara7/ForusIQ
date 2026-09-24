"""Construcción del universo de evaluación tienda×SKU y agregados base.

Universo a nivel tienda×modelo-color (MC):
  - todo MC con venta en la ventana, stock o tránsito en la tienda;
  - todo MC con stock disponible en el CD, en todas las tiendas activas
    (candidatos a introducción).
Luego se expande a TODAS las tallas que el modelo fabrica (dim_producto), para que
la curva y la necesidad cubran tallas nunca recibidas.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from forusight.config.settings import EngineParams
from forusight.engine.common import KEY_MC, KEY_SKU, bloque_semana, semana_relativa

PROD_COLS = [
    "sku",
    "modelo_id",
    "modelo_color_id",
    "color",
    "talla",
    "talla_orden",
    "categoria",
    "genero",
    "rango_precio",
]


@dataclass
class Base:
    """Datos base de una corrida."""

    semanal: pd.DataFrame  # tienda×SKU×semana (ventana), con rel/bloque y atributos de producto
    sku: pd.DataFrame  # universo tienda×SKU con stock, venta, CD y atributos
    tiendas: pd.DataFrame  # tiendas activas
    cd: pd.DataFrame  # stock CD por SKU con disponible


def preparar_cd(stock_cd: pd.DataFrame, params: EngineParams | None = None) -> pd.DataFrame:
    cd = stock_cd[["sku", "fisico", "reservado", "comprometido"]].copy()
    comp = params.stock_cd.componentes if params is not None else "tiendas+bodega"
    if comp != "tiendas+bodega" and f"cd_stock_{comp}" in stock_cd:
        cd["fisico"] = stock_cd[f"cd_stock_{comp}"].fillna(0).to_numpy()
    cd["disponible"] = np.floor(
        np.maximum(cd["fisico"] - cd["reservado"] - cd["comprometido"], 0.0)
    ).astype("int64")
    return cd


def marcar_prioridad(tiendas: pd.DataFrame, params: EngineParams) -> pd.DataFrame:
    """Tiendas prioritarias (nombre contiene un patrón, p. ej. JOCKEY): importancia máxima y
    factor de cobertura mayor. Liquidadoras (cadenas DH, SE, FB): importancia mínima, menos
    cobertura y sin introducciones. El resto conserva su importancia (peso de su venta)."""
    p = params.prioridad_tiendas
    nombre = tiendas["nombre"].astype("string").str.upper().fillna("")
    prio = pd.Series(False, index=tiendas.index)
    for patron in p.patrones:
        if str(patron).strip():
            prio |= nombre.str.contains(str(patron).strip().upper(), regex=False)
    cadena = (
        tiendas["cadena"].astype("string").str.upper()
        if "cadena" in tiendas
        else pd.Series(pd.NA, index=tiendas.index, dtype="string")
    )
    cadena = cadena.fillna(nombre.str.split().str[0])
    # Prioridad por venta (A, B, C) del maestro de la marca; manda sobre el patrón de nombre.
    nivel = (
        tiendas["prioridad"].astype("string").str.strip().str.upper()
        if "prioridad" in tiendas
        else pd.Series(pd.NA, index=tiendas.index, dtype="string")
    )
    con_nivel = nivel.notna() & nivel.isin(list(p.niveles))
    prio = np.where(con_nivel, nivel.eq("A").fillna(False), prio)
    liq = cadena.isin([str(c).strip().upper() for c in p.cadenas_liquidadoras]) & ~prio
    tiendas["tienda_prioritaria"] = np.asarray(prio, dtype=bool)
    tiendas["tienda_liquidadora"] = liq.to_numpy(dtype=bool)
    imp = pd.to_numeric(tiendas["importancia_comercial"], errors="coerce").fillna(0.5)
    imp = np.where(prio & ~con_nivel, np.maximum(imp, p.importancia), imp)
    factor = np.select([prio, liq], [p.factor_cobertura, p.factor_cobertura_liquidadora], 1.0)
    for n, cfg in p.niveles.items():
        es = (con_nivel & nivel.eq(n)).fillna(False).to_numpy(dtype=bool)
        factor = np.where(es, cfg.factor_cobertura, factor)
        if cfg.importancia is not None:
            imp = np.where(es, cfg.importancia, imp)
    tiendas["importancia_comercial"] = np.where(liq, p.importancia_liquidadora, imp)
    tiendas["factor_cobertura_tienda"] = factor
    return tiendas


def construir_base(
    ventas: pd.DataFrame,
    stock_tienda: pd.DataFrame,
    stock_cd: pd.DataFrame,
    dim_producto: pd.DataFrame,
    dim_tienda: pd.DataFrame,
    params: EngineParams,
    fecha_corte: pd.Timestamp,
    permitidos: pd.DataFrame | None = None,
) -> Base:
    """``permitidos`` (tienda_id, modelo_id), si viene, limita las INTRODUCCIONES a los
    pares tienda×modelo autorizados (maestro modelo → cadena). La reposición de lo que la
    tienda ya tiene o vendió no se restringe."""
    n_sem = params.horizonte.semanas_analisis
    b = params.horizonte.semanas_bloque_reciente
    prod = dim_producto[PROD_COLS].copy()
    tiendas = marcar_prioridad(dim_tienda.loc[dim_tienda["activa"]].copy(), params)
    activas = set(tiendas["tienda_id"])
    cd = preparar_cd(stock_cd, params)
    cd = cd.loc[cd["sku"].isin(set(prod["sku"]))]

    # --- venta semanal en la ventana, sólo tiendas activas y SKUs conocidos
    v = ventas.loc[ventas["tienda_id"].isin(activas)].copy()
    v["rel"] = semana_relativa(v["semana_inicio"], fecha_corte)
    v = v.loc[(v["rel"] >= 1) & (v["rel"] <= n_sem)]
    v["unidades"] = v["unidades"].clip(lower=0.0)  # devoluciones netas negativas → 0
    v["bloque"] = bloque_semana(v["rel"], params)
    semanal = v.merge(prod, on="sku", how="inner")[
        [
            "tienda_id",
            "sku",
            "modelo_id",
            "modelo_color_id",
            "categoria",
            "genero",
            "rango_precio",
            "talla",
            "rel",
            "bloque",
            "unidades",
            "dias_con_stock",
        ]
    ]

    st = stock_tienda.loc[stock_tienda["tienda_id"].isin(activas)]
    st = st.merge(prod[["sku", "modelo_color_id"]], on="sku", how="inner")

    # --- universo MC
    mc_hist = semanal[KEY_MC].drop_duplicates()
    mc_stock = st.loc[(st["stock_disponible"] > 0) | (st["stock_transito"] > 0), KEY_MC]
    mc_cd = prod.loc[prod["sku"].isin(cd.loc[cd["disponible"] > 0, "sku"]), ["modelo_color_id"]]
    mc_cd = mc_cd.drop_duplicates().merge(tiendas[["tienda_id"]], how="cross")
    if permitidos is not None:
        mod = prod[["modelo_color_id", "modelo_id"]].drop_duplicates()
        mc_cd = mc_cd.merge(mod, on="modelo_color_id").merge(
            permitidos[["tienda_id", "modelo_id"]].drop_duplicates(), on=["tienda_id", "modelo_id"]
        )
    universo_mc = pd.concat([mc_hist, mc_stock, mc_cd[KEY_MC]]).drop_duplicates()

    # --- expandir a todas las tallas fabricadas
    sku = universo_mc.merge(prod, on="modelo_color_id", how="inner")

    # stock y tránsito
    sku = sku.merge(
        st[["tienda_id", "sku", "stock_disponible", "stock_transito"]], on=KEY_SKU, how="left"
    )
    sku[["stock_disponible", "stock_transito"]] = sku[
        ["stock_disponible", "stock_transito"]
    ].fillna(0.0)

    # venta 4S / 12S y días con stock por SKU
    agg = semanal.groupby(KEY_SKU, sort=False).agg(
        venta_12s=("unidades", "sum"), dias_12s=("dias_con_stock", "sum")
    )
    v4 = semanal.loc[semanal["rel"] <= b].groupby(KEY_SKU, sort=False)["unidades"].sum()
    agg["venta_4s"] = v4
    sku = sku.merge(agg.reset_index(), on=KEY_SKU, how="left")
    sku[["venta_12s", "dias_12s", "venta_4s"]] = sku[["venta_12s", "dias_12s", "venta_4s"]].fillna(
        0
    )

    # stock CD
    sku = sku.merge(
        cd[["sku", "disponible"]].rename(columns={"disponible": "cd_disponible"}),
        on="sku",
        how="left",
    )
    sku["cd_disponible"] = sku["cd_disponible"].fillna(0).astype("int64")

    # atributos de tienda
    sku = sku.merge(
        tiendas[["tienda_id", "cluster", "importancia_comercial", "tienda_prioritaria"]],
        on="tienda_id",
        how="left",
    )
    sku = sku.sort_values(["tienda_id", "modelo_color_id", "talla_orden", "sku"], kind="mergesort")
    return Base(semanal=semanal, sku=sku.reset_index(drop=True), tiendas=tiendas, cd=cd)
