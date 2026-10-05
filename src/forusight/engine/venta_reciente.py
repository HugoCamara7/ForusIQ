"""Reponer la venta desde la última ruta.

El stock_bi de fecha_corte F es el cierre de ese día F (normalmente ayer). Si la tienda
vendió 10 y 15 en los dos días desde su ruta anterior, hay que reponer esas 25 unidades (si el
CD las tiene), además de lo que pida el análisis de 12 semanas:

    ruta anterior      = último día con ruta del mall antes del día de reposición
                         (sin ruta: día de reposición − período de revisión)
    venta desde ruta   = venta diaria desde la ruta anterior hasta el día antes de la de hoy
                         (lo que se vendió después de armar el envío anterior)
    venta post corte   = venta neta después del día del corte (fecha > F) y antes de hoy,
                         aún no descontada del stock: se resta del stock físico. Con el corte
                         de ayer es 0; con stock_bi atrasado (lunes: corte del sábado) es la
                         venta del domingo. Las notas de crédito restan (puede ser negativa).

La necesidad es max(nivel máximo − posición, venta desde ruta).
"""

from __future__ import annotations

import math

import pandas as pd

DIAS = ["LU", "MA", "MI", "JU", "VI", "SA", "DO"]
COLUMNAS = ["tienda_id", "sku", "venta_desde_ruta", "venta_post_corte"]


def ruta_anterior(dia, dias: str, revision_dias: float | None = None) -> pd.Timestamp:
    """Último día de ruta antes de ``dia`` según los días del mall ('LU,MI,VI')."""
    d = pd.Timestamp(dia).normalize()
    ds = {x for x in str(dias or "").split(",") if x}
    if ds:
        for k in range(1, 8):
            f = d - pd.Timedelta(days=k)
            if DIAS[f.weekday()] in ds:
                return f
    rev = revision_dias if revision_dias and not pd.isna(revision_dias) else 7
    return d - pd.Timedelta(days=max(1, math.ceil(rev)))


def resumir(
    venta_diaria: pd.DataFrame | None, dim_tienda: pd.DataFrame, dia, fecha_foto
) -> pd.DataFrame:
    """tienda_id, sku, venta_desde_ruta, venta_post_corte (sólo pares con venta)."""
    if venta_diaria is None or venta_diaria.empty:
        return pd.DataFrame(columns=COLUMNAS)
    d = pd.Timestamp(dia).normalize()
    # fecha_corte F del stock = cierre de F: la venta posterior a F no está descontada.
    corte = pd.Timestamp(fecha_foto).normalize() if fecha_foto else d - pd.Timedelta(days=1)
    t = dim_tienda.drop_duplicates("tienda_id").set_index("tienda_id")
    dias = t["dias_reposicion"] if "dias_reposicion" in t else pd.Series("", index=t.index)
    rev = t["revision_dias"] if "revision_dias" in t else pd.Series(None, index=t.index)
    desde = {tid: ruta_anterior(d, dias.get(tid, ""), rev.get(tid)) for tid in t.index.astype(str)}
    v = venta_diaria.copy()
    v["fecha"] = pd.to_datetime(v["fecha"]).dt.normalize()
    v = v.loc[v["fecha"] < d]
    inicio = v["tienda_id"].astype(str).map(desde)
    v["venta_desde_ruta"] = v["unidades"].where(inicio.notna() & (v["fecha"] >= inicio), 0)
    v["venta_post_corte"] = v["unidades"].where(v["fecha"] > corte, 0)
    g = v.groupby(["tienda_id", "sku"], as_index=False)[
        ["venta_desde_ruta", "venta_post_corte"]
    ].sum()
    g["venta_desde_ruta"] = g["venta_desde_ruta"].clip(lower=0)  # se repone lo vendido
    # venta_post_corte neta: una nota de crédito devuelve la prenda al stock.
    return g.loc[g["venta_desde_ruta"].gt(0) | g["venta_post_corte"].ne(0), COLUMNAS]
