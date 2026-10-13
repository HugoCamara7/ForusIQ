"""Tránsito hacia cada tienda, desde las tablas de pedidos del sistema.

Tránsito de una tienda × SKU = pedidos hacia la tienda (``pedidos_header_table`` +
``pedidos_detail_table``) en los estados y clasificaciones de ``params.recepcion``
(ver ``fuentes.sql_pedidos`` y ``fuentes.transito_de_pedidos``). Un pedido ya despachado deja
de ser tránsito cuando supera el plazo de entrega de su tienda, medido con sus propias
recepciones (``fuentes.plazos_entrega``). Reemplaza al ``stock_transito`` de la foto de stock,
que viene en 0: ``stock_bi.transito`` es la salida de la tienda origen, no lo que llega.

Sólo cuentan los pedidos creados hasta el día del cierre del stock (``solo_hasta_cierre_stock``):
el tránsito queda en la misma foto que el stock, como el reporte de distribución. Se separa en
tránsito **en el CD** (aprobado, en picking) y **despachado** (documentado, en transporte,
prerecepcionado).
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from forusight.data.fuentes import recepcion_de_pedidos, transito_de_pedidos, ventana_transito

KEY = ["tienda_id", "sku"]


def usa_pedidos(inputs, params) -> bool:
    """El tránsito sale de los pedidos (si se leyeron y está activado)."""
    return bool(params.recepcion.transito_pedidos) and getattr(inputs, "pedidos", None) is not None


def post_cierre(pedidos: pd.DataFrame) -> pd.Series:
    """Pedidos creados después del día del cierre del stock (``dias_pedido`` < 0)."""
    if "dias_pedido" not in pedidos:
        return pd.Series(False, index=pedidos.index)
    return pd.to_numeric(pedidos["dias_pedido"], errors="coerce").fillna(0) < 0


def _transito(pedidos: pd.DataFrame, inputs, params, estados, recientes) -> pd.DataFrame:
    rec = params.recepcion
    return transito_de_pedidos(
        pedidos,
        estados,
        rec.clasificaciones_transito,
        recientes,
        rec.dias_transito_recientes,
        ventana_transito(getattr(inputs, "plazos_entrega", None), rec),
    )


def _total(pedidos: pd.DataFrame, inputs, params) -> float:
    rec = params.recepcion
    tr = _transito(pedidos, inputs, params, rec.estados_transito, rec.estados_transito_recientes)
    return float(tr["stock_transito"].sum()) if len(tr) else 0.0


def aplicar_pedidos(inputs, params):
    """``stock_tienda.stock_transito`` = pedidos abiertos hacia la tienda (tiendas × SKU sin
    stock pero con pedido en camino se agregan con stock 0), con su detalle
    ``transito_en_cd`` (aprobado, en picking) y ``transito_despachado`` (documentado, en
    transporte, prerecepcionado, dentro del plazo de la tienda)."""
    rec = params.recepcion
    p = inputs.pedidos
    if rec.solo_hasta_cierre_stock:
        p = p.loc[~post_cierre(p)]
    tr = _transito(p, inputs, params, rec.estados_transito, rec.estados_transito_recientes)
    en_cd = _transito(p, inputs, params, rec.estados_transito, [])
    desp = _transito(p, inputs, params, [], rec.estados_transito_recientes)
    tr = tr.merge(en_cd.rename(columns={"stock_transito": "transito_en_cd"}), on=KEY, how="left")
    tr = tr.merge(
        desp.rename(columns={"stock_transito": "transito_despachado"}), on=KEY, how="left"
    )
    st = inputs.stock_tienda.drop(
        columns=["stock_transito", "transito_en_cd", "transito_despachado"], errors="ignore"
    )
    st = st.merge(tr, on=KEY, how="outer")
    cols = ["stock_disponible", "stock_transito", "transito_en_cd", "transito_despachado"]
    st[cols] = st[cols].fillna(0.0)
    return replace(inputs, stock_tienda=st)


def transito_post_cierre(inputs, params) -> dict:
    """Pedidos creados después del cierre del stock (normalmente, hoy): unidades que serían
    tránsito y si se dejaron fuera (``solo_hasta_cierre_stock``)."""
    if not usa_pedidos(inputs, params):
        return {"unidades": 0, "excluidos": False}
    p = inputs.pedidos
    return {
        "unidades": int(_total(p.loc[post_cierre(p)], inputs, params)),
        "excluidos": bool(params.recepcion.solo_hasta_cierre_stock),
    }


def recepcion_post_corte(inputs, params):
    """tienda_id, sku, recepcion_post_corte: lo recepcionado después del corte de stock_bi, que
    se suma al stock físico (None si está apagado o no hay pedidos)."""
    if not params.recepcion.sumar_recepcion_post_corte or not usa_pedidos(inputs, params):
        return None
    return recepcion_de_pedidos(inputs.pedidos, params.recepcion.clasificaciones_transito)
