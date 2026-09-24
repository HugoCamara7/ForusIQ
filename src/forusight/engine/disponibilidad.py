"""Disponibilidad (fill de tallas) como la mide Forus.

Por tienda × modelo-color que la tienda maneja (tiene stock o vendió en 12 semanas):

    talla disponible  = tiene stock en la tienda
                        o el CD tampoco la tiene (una talla que no existe en el CD no se
                        puede reponer, así que no le resta disponibilidad al modelo)
    disp. del modelo  = tallas disponibles / tallas del modelo en la tienda

Ejemplo: modelo de 12 tallas con 3 tallas en quiebre que el CD no tiene → 100 %.
Si el CD sí tiene esas 3 tallas → 9 / 12 = 75 % (y se corrige con el envío).

La disponibilidad de la tienda promedia sus modelos ponderando por la venta del modelo, y la
total promedia las tiendas ponderando por su flujo (venta de 12 semanas). Se calcula antes
(stock actual) y después del envío (stock + cantidad).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

KEY = ["tienda_id", "modelo_color_id"]


def por_modelo(detalle: pd.DataFrame) -> pd.DataFrame:
    """Una fila por tienda × modelo-color manejado: disponibilidad antes y después."""
    d = detalle[
        [
            "tienda_id",
            "modelo_color_id",
            "stock_tienda",
            "cantidad",
            "stock_cd_disponible",
            "venta_12s",
        ]
    ].copy()
    stock = d["stock_tienda"].fillna(0).to_numpy(dtype=float)
    cant = d["cantidad"].fillna(0).to_numpy(dtype=float)
    sin_cd = d["stock_cd_disponible"].fillna(0).to_numpy(dtype=float) <= 0
    d["ok_antes"] = (stock > 0) | sin_cd
    d["ok_despues"] = (stock + cant > 0) | sin_cd
    d["con_stock"] = stock > 0
    g = d.groupby(KEY, sort=False).agg(
        tallas=("ok_antes", "size"),
        ok_antes=("ok_antes", "sum"),
        ok_despues=("ok_despues", "sum"),
        con_stock=("con_stock", "sum"),
        venta=("venta_12s", "sum"),
    )
    g = g.loc[(g["con_stock"] > 0) | (g["venta"] > 0)]  # modelos que la tienda maneja
    g["disp_antes"] = g["ok_antes"] / g["tallas"]
    g["disp_despues"] = g["ok_despues"] / g["tallas"]
    return g.reset_index()


def _prom(x: pd.Series, w: pd.Series) -> float:
    w = w.clip(lower=0)
    return float(np.average(x, weights=w)) if w.sum() > 0 else float(x.mean()) if len(x) else 1.0


def por_tienda(detalle: pd.DataFrame) -> pd.DataFrame:
    m = por_modelo(detalle)
    if m.empty:
        return pd.DataFrame(columns=["tienda_id", "disp_antes", "disp_despues", "flujo", "modelos"])
    peso = m["venta"] + 1  # un modelo sin venta también cuenta, con peso mínimo
    m = m.assign(_w=peso, _a=m["disp_antes"] * peso, _d=m["disp_despues"] * peso)
    t = m.groupby("tienda_id").agg(
        _a=("_a", "sum"),
        _d=("_d", "sum"),
        _w=("_w", "sum"),
        flujo=("venta", "sum"),
        modelos=("modelo_color_id", "size"),
    )
    t["disp_antes"] = t["_a"] / t["_w"]
    t["disp_despues"] = t["_d"] / t["_w"]
    return t.drop(columns=["_a", "_d", "_w"]).reset_index()


def total(detalle: pd.DataFrame) -> dict:
    """Disponibilidad total (ponderada por flujo de cada tienda) antes y después del envío."""
    t = por_tienda(detalle)
    if t.empty:
        return {"antes": 1.0, "despues": 1.0, "tiendas": 0}
    w = t["flujo"] + 1
    return {
        "antes": _prom(t["disp_antes"], w),
        "despues": _prom(t["disp_despues"], w),
        "tiendas": int(len(t)),
    }


#: Disponibilidad: la meta es 100 %; el mínimo aceptable es 92 % (quiebre ≤ 8 %).
META = 1.0
MINIMO = 0.92


def kpis(detalle: pd.DataFrame) -> dict:
    """KPI del piloto sobre los SKU activos (de modelos que la tienda maneja).

    * disponibilidad simple = SKU disponibles / SKU activos (quiebre = 1 − disponibilidad);
    * disponibilidad ponderada = misma cuenta ponderando cada SKU por su venta de 12 semanas;
    * WOS (semanas de cobertura) = (stock + tránsito [+ envío]) / demanda semanal.
    Un SKU sin stock que el CD tampoco tiene no cuenta como quiebre (no se puede reponer).
    """
    d = detalle.copy()
    manejados = por_modelo(d)[KEY]
    d = d.merge(manejados, on=KEY)
    if d.empty:
        return {"activos": 0}
    stock = d["stock_tienda"].fillna(0).to_numpy(dtype=float)
    trans = d.get("stock_transito", pd.Series(0.0, index=d.index)).fillna(0).to_numpy(dtype=float)
    cant = d["cantidad"].fillna(0).to_numpy(dtype=float)
    sin_cd = d["stock_cd_disponible"].fillna(0).to_numpy(dtype=float) <= 0
    venta = d["venta_12s"].fillna(0).to_numpy(dtype=float)
    dem = d["demanda_semanal"].fillna(0).to_numpy(dtype=float)
    ok_a = (stock > 0) | sin_cd
    ok_d = (stock + cant > 0) | sin_cd
    w = venta if venta.sum() > 0 else np.ones_like(venta)
    dem_t = dem.sum()
    return {
        "activos": int(len(d)),
        "simple_antes": float(ok_a.mean()),
        "simple_despues": float(ok_d.mean()),
        "ponderada_antes": float(np.average(ok_a, weights=w)),
        "ponderada_despues": float(np.average(ok_d, weights=w)),
        "wos_antes": float((stock + trans).sum() / dem_t) if dem_t > 0 else float("nan"),
        "wos_despues": float((stock + trans + cant).sum() / dem_t) if dem_t > 0 else float("nan"),
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
