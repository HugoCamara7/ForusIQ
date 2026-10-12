"""Tránsito hacia cada tienda, desde las tablas de pedidos del sistema.

Tránsito de una tienda × SKU = pedidos hacia la tienda (``pedidos_header_table`` +
``pedidos_detail_table``) en los estados y clasificaciones de ``params.recepcion``
(ver ``fuentes.sql_pedidos`` y ``fuentes.transito_de_pedidos``). Reemplaza al
``stock_transito`` de la foto de stock, que viene en 0: ``stock_bi.transito`` es la salida
de la tienda origen, no lo que llega.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from forusight.data.fuentes import recepcion_de_pedidos, transito_de_pedidos


def usa_pedidos(inputs, params) -> bool:
    """El tránsito sale de los pedidos (si se leyeron y está activado)."""
    return bool(params.recepcion.transito_pedidos) and getattr(inputs, "pedidos", None) is not None


def aplicar_pedidos(inputs, params):
    """``stock_tienda.stock_transito`` = pedidos abiertos hacia la tienda, de todo el historial
    (tiendas × SKU sin stock pero con pedido en camino se agregan con stock 0)."""
    rec = params.recepcion
    tr = transito_de_pedidos(
        inputs.pedidos,
        rec.estados_transito,
        rec.clasificaciones_transito,
        rec.estados_transito_recientes,
        rec.dias_transito_recientes,
    )
    st = inputs.stock_tienda.drop(columns="stock_transito", errors="ignore")
    st = st.merge(tr, on=["tienda_id", "sku"], how="outer")
    st[["stock_disponible", "stock_transito"]] = st[["stock_disponible", "stock_transito"]].fillna(
        0.0
    )
    return replace(inputs, stock_tienda=st)


def recepcion_post_corte(inputs, params):
    """tienda_id, sku, recepcion_post_corte: lo recepcionado después del corte de stock_bi, que
    se suma al stock físico (None si está apagado o no hay pedidos)."""
    if not params.recepcion.sumar_recepcion_post_corte or not usa_pedidos(inputs, params):
        return None
    return recepcion_de_pedidos(inputs.pedidos, params.recepcion.clasificaciones_transito)


def transito_post_corte(inputs, params) -> float:
    """Unidades del tránsito que son pedidos creados después del corte de stock (normalmente,
    hoy): p. ej. el pedido de Neogística del día ya cargado en el sistema. Cuentan como
    tránsito para no enviar dos veces, y se informan aparte."""
    if not usa_pedidos(inputs, params):
        return 0.0
    p = inputs.pedidos
    if "dias_pedido" not in p:
        return 0.0
    nuevos = p.loc[pd.to_numeric(p["dias_pedido"], errors="coerce").fillna(0) < 0]
    rec = params.recepcion
    tr = transito_de_pedidos(
        nuevos,
        rec.estados_transito,
        rec.clasificaciones_transito,
        rec.estados_transito_recientes,
        rec.dias_transito_recientes,
    )
    return float(tr["stock_transito"].sum()) if len(tr) else 0.0
