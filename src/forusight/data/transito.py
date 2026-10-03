"""Tránsito hacia cada tienda, desde las tablas de pedidos del sistema.

Tránsito de una tienda × SKU = pedidos hacia la tienda (``pedidos_header_table`` +
``pedidos_detail_table``) en los estados y clasificaciones de ``params.recepcion``
(ver ``fuentes.sql_pedidos`` y ``fuentes.transito_de_pedidos``). Reemplaza al
``stock_transito`` de la foto de stock, que viene en 0: ``stock_bi.transito`` es la salida
de la tienda origen, no lo que llega.
"""

from __future__ import annotations

from dataclasses import replace

from forusight.data.fuentes import transito_de_pedidos


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
        rec.recepcionados_post_corte,
    )
    st = inputs.stock_tienda.drop(columns="stock_transito", errors="ignore")
    st = st.merge(tr, on=["tienda_id", "sku"], how="outer")
    st[["stock_disponible", "stock_transito"]] = st[["stock_disponible", "stock_transito"]].fillna(
        0.0
    )
    return replace(inputs, stock_tienda=st)
