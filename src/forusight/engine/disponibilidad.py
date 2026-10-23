"""Disponibilidad del retail (velocímetro) como la mide Forus.

    SKU activo     = tienda × SKU que vendió en las últimas 8 semanas cerradas
                     (``disponibilidad.semanas_venta_activo``; columna ``venta_8s``)
    SKU disponible = SKU activo con stock en la tienda (> 0)
    disponibilidad = SKU disponibles / SKU activos      (quiebre = 1 − disponibilidad)

El stock del CD no cuenta: un SKU que vendió y no tiene stock en la tienda es quiebre aunque
el CD tampoco lo tenga. Un SKU sin venta en las 8 semanas no se mide, aunque tenga stock.
Ejemplo: 10 SKU con venta, 5 con stock en la tienda → 50 %.

Se calcula hoy (stock), con tránsito (stock + tránsito) y después del envío (stock + cantidad).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: Disponibilidad: la meta es 100 %; el mínimo aceptable es 92 % (quiebre ≤ 8 %).
META = 1.0
MINIMO = 0.92


def _venta_activo(d: pd.DataFrame) -> pd.Series:
    """Venta que define el SKU activo: 8 semanas; corridas sin la columna usan 12 semanas."""
    col = "venta_8s" if "venta_8s" in d else "venta_12s"
    return d[col].fillna(0).astype(float)


def activos(detalle: pd.DataFrame) -> pd.DataFrame:
    """SKU activos (los que cuenta :func:`kpis`): tienda × SKU con venta en 8 semanas."""
    return detalle.loc[_venta_activo(detalle) > 0].reset_index(drop=True)


#: Columnas por SKU activo que guarda la corrida para el detalle de disponibilidad.
COLUMNAS_SKU = [
    "tienda_id",
    "modelo_color_id",
    "sku",
    "talla",
    "stock_tienda",
    "stock_transito",
    "cantidad",
    "stock_cd_disponible",
    "venta_8s",
    "venta_12s",
    "demanda_semanal",
]


def sku_activos(detalle: pd.DataFrame) -> pd.DataFrame:
    """SKU activos con lo necesario para recalcular la disponibilidad por tienda (pocas
    columnas: se guarda en el resultado de la corrida para todo el retail)."""
    d = activos(detalle)
    if "venta_8s" not in d:
        d = d.assign(venta_8s=_venta_activo(d))
    for c in COLUMNAS_SKU:
        if c not in d:
            d[c] = 0.0 if c not in ("talla",) else ""
    return d[COLUMNAS_SKU].copy()


def _flags(d: pd.DataFrame) -> pd.DataFrame:
    stock = d["stock_tienda"].fillna(0).astype(float)
    trans = (
        d["stock_transito"].fillna(0).astype(float)
        if "stock_transito" in d
        else pd.Series(0.0, index=d.index)
    )
    cant = d["cantidad"].fillna(0).astype(float)
    dem = (
        d["demanda_semanal"].fillna(0).astype(float)
        if "demanda_semanal" in d
        else pd.Series(0.0, index=d.index)
    )
    return d.assign(
        ok_antes=stock > 0,
        ok_transito=stock + trans > 0,
        ok_despues=stock + cant > 0,
        quiebre=stock <= 0,
        quiebre_se_repone=(stock <= 0) & (cant > 0),
        _venta=_venta_activo(d),
        _cob=stock + trans,
        _cant=cant,
        _dem=dem,
    )


def por_tienda_kpis(skus: pd.DataFrame) -> pd.DataFrame:
    """Disponibilidad por tienda con las mismas cuentas que el velocímetro (:func:`kpis`):
    sumando sus SKU activos y disponibles se obtiene exactamente el total."""
    cols = [
        "tienda_id",
        "activos",
        "disponibles",
        "quiebres",
        "quiebres_se_reponen",
        "disp_antes",
        "disp_transito",
        "disp_despues",
        "disp_ponderada",
        "wos",
    ]
    if skus is None or skus.empty:
        return pd.DataFrame(columns=cols)
    d = _flags(skus)
    d["_vok"] = d["_venta"] * d["ok_antes"]
    g = d.groupby("tienda_id").agg(
        activos=("ok_antes", "size"),
        disponibles=("ok_antes", "sum"),
        _tr=("ok_transito", "sum"),
        _de=("ok_despues", "sum"),
        quiebres=("quiebre", "sum"),
        quiebres_se_reponen=("quiebre_se_repone", "sum"),
        _v=("_venta", "sum"),
        _vok=("_vok", "sum"),
        _cob=("_cob", "sum"),
        _dem=("_dem", "sum"),
    )
    g["disp_antes"] = g["disponibles"] / g["activos"]
    g["disp_transito"] = g["_tr"] / g["activos"]
    g["disp_despues"] = g["_de"] / g["activos"]
    g["disp_ponderada"] = np.where(
        g["_v"] > 0, g["_vok"] / g["_v"].where(g["_v"] > 0, 1), g["disp_antes"]
    )
    g["wos"] = np.where(g["_dem"] > 0, g["_cob"] / g["_dem"].where(g["_dem"] > 0, 1), np.nan)
    return g.reset_index()[cols]


def kpis_skus(skus: pd.DataFrame) -> dict:
    """Totales sobre los SKU activos de :func:`sku_activos` (mismas cuentas que :func:`kpis`)."""
    if skus is None or skus.empty:
        return {"activos": 0}
    d = _flags(skus)
    w = d["_venta"] if d["_venta"].sum() > 0 else pd.Series(1.0, index=d.index)
    dem = d["_dem"].sum()
    return {
        "activos": int(len(d)),
        "simple_antes": float(d["ok_antes"].mean()),
        "simple_despues": float(d["ok_despues"].mean()),
        "con_transito_antes": float(d["ok_transito"].mean()),
        "ponderada_antes": float(np.average(d["ok_antes"], weights=w)),
        "wos": float(d["_cob"].sum() / dem) if dem > 0 else float("nan"),
    }


def quiebres(skus: pd.DataFrame) -> pd.DataFrame:
    """SKU activos en quiebre (vendieron y no tienen stock en la tienda), con lo que se envía."""
    if skus is None or skus.empty:
        return pd.DataFrame(columns=COLUMNAS_SKU)
    d = _flags(skus)
    return d.loc[d["quiebre"], COLUMNAS_SKU].reset_index(drop=True)


def kpis(detalle: pd.DataFrame) -> dict:
    """KPI sobre los SKU activos (venta en 8 semanas).

    * disponibilidad simple = SKU con stock en la tienda / SKU activos;
    * disponibilidad ponderada = misma cuenta ponderando cada SKU por su venta de 8 semanas;
    * disponibilidad con tránsito = además cuenta el SKU que ya va en camino;
    * WOS (semanas de cobertura) = (stock + tránsito [+ envío]) / demanda semanal.
    """
    d = activos(detalle)
    if d.empty:
        return {"activos": 0}
    f = _flags(d)
    w = f["_venta"] if f["_venta"].sum() > 0 else pd.Series(1.0, index=f.index)
    dem_t = f["_dem"].sum()
    return {
        "activos": int(len(f)),
        "simple_antes": float(f["ok_antes"].mean()),
        "simple_despues": float(f["ok_despues"].mean()),
        "con_transito_antes": float(f["ok_transito"].mean()),
        "ponderada_antes": float(np.average(f["ok_antes"], weights=w)),
        "ponderada_despues": float(np.average(f["ok_despues"], weights=w)),
        "wos_antes": float(f["_cob"].sum() / dem_t) if dem_t > 0 else float("nan"),
        "wos_despues": float((f["_cob"] + f["_cant"]).sum() / dem_t) if dem_t > 0 else float("nan"),
    }


def control_cd(detalle: pd.DataFrame, cantidad: pd.Series | None = None) -> pd.DataFrame:
    """Por SKU: stock disponible del CD, unidades enviadas y lo que queda (nunca negativo)."""
    q = detalle["cantidad"] if cantidad is None else cantidad
    d = pd.DataFrame(
        {"sku": detalle["sku"], "cd": detalle["stock_cd_disponible"].fillna(0), "enviado": q}
    )
    g = d.groupby("sku").agg(
        stock_cd=("cd", "first"),
        enviado=("enviado", "sum"),
        tiendas=("enviado", lambda x: (x > 0).sum()),
    )
    g["queda"] = g["stock_cd"] - g["enviado"]
    return g.reset_index()


def control_venta(detalle: pd.DataFrame) -> dict:
    """¿Se repuso lo vendido desde la ruta anterior? Unidades vendidas, repuestas y, de lo no
    repuesto, por qué: sin stock en el CD, CD insuficiente (se priorizó a otras tiendas) o tope
    de la tienda. «otro» debe ser siempre 0."""
    piso = (
        detalle["piso_venta"].fillna(0).to_numpy(dtype=float)
        if "piso_venta" in detalle
        else np.zeros(len(detalle))
    )
    q = detalle["cantidad"].fillna(0).to_numpy(dtype=float)
    falta = np.maximum(piso - q, 0)
    cd = detalle["stock_cd_disponible"].fillna(0).to_numpy(dtype=float)
    motivo = detalle["motivo_codigo"].astype(str).to_numpy()
    parcial = (
        detalle["motivo_parcial"].astype(str).to_numpy()
        if "motivo_parcial" in detalle
        else np.full(len(detalle), "")
    )
    tope = np.isin(motivo, ["NO_TOPE_TIENDA"]) | (parcial == "PARCIAL_TOPE")
    sin_cd = (cd <= 0) & ~tope
    escaso = (cd > 0) & ~tope
    return {
        "filas": int((piso > 0).sum()),
        "vendido": int(piso.sum()),
        "repuesto": int(np.minimum(piso, q).sum()),
        "sin_stock_cd": int(falta[sin_cd].sum()),
        "cd_insuficiente": int(falta[escaso].sum()),
        "tope_tienda": int(falta[tope].sum()),
        "otro": int(falta.sum() - falta[sin_cd].sum() - falta[escaso].sum() - falta[tope].sum()),
    }
