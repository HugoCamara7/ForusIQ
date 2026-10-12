"""Tránsito hacia cada tienda, desde las tablas de pedidos del sistema.

Tránsito de una tienda × SKU = pedidos hacia la tienda (``pedidos_header_table`` +
``pedidos_detail_table``) en los estados y clasificaciones de ``params.recepcion``
(ver ``fuentes.sql_pedidos`` y ``fuentes.transito_de_pedidos``). Un pedido ya despachado deja
de ser tránsito cuando supera el plazo de entrega de su tienda, medido con sus propias
recepciones (``fuentes.plazos_entrega``). Reemplaza al ``stock_transito`` de la foto de stock,
que viene en 0: ``stock_bi.transito`` es la salida de la tienda origen, no lo que llega.

Se separa en tránsito **en el CD** (aprobado, en picking) y **despachado** (documentado, en
transporte, prerecepcionado). Desde las 8:00, la corrida del día no cuenta los pedidos creados
hoy antes de las 7:00 (los de Neogística): ``excluir_madrugada``.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from forusight.data.fuentes import recepcion_de_pedidos, transito_de_pedidos, ventana_transito

KEY = ["tienda_id", "sku"]


def usa_pedidos(inputs, params) -> bool:
    """El tránsito sale de los pedidos (si se leyeron y está activado)."""
    return bool(params.recepcion.transito_pedidos) and getattr(inputs, "pedidos", None) is not None


def con_hora(pedidos: pd.DataFrame) -> bool:
    """La cabecera trae la hora de creación: algún pedido de hoy tiene hora distinta de 00:00
    (si todas son 00:00 la columna es sólo fecha y no se puede separar la madrugada)."""
    if pedidos is None or "minuto_creacion_hoy" not in pedidos:
        return False
    m = pd.to_numeric(pedidos["minuto_creacion_hoy"], errors="coerce")
    return bool((m > 0).any())


def madrugada(pedidos: pd.DataFrame, params) -> pd.Series:
    """Filas creadas hoy antes de ``hora_corte_pedidos`` (False si no hay hora)."""
    if not con_hora(pedidos):
        return pd.Series(False, index=pedidos.index if pedidos is not None else None)
    m = pd.to_numeric(pedidos["minuto_creacion_hoy"], errors="coerce")
    return (m < params.recepcion.hora_corte_pedidos * 60).fillna(False)


def pedidos_vigentes(inputs, params, excluir_madrugada: bool = False) -> pd.DataFrame:
    p = inputs.pedidos
    if excluir_madrugada and params.recepcion.excluir_pedidos_madrugada:
        p = p.loc[~madrugada(p, params)]
    return p


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


def aplicar_pedidos(inputs, params, excluir_madrugada: bool = False):
    """``stock_tienda.stock_transito`` = pedidos abiertos hacia la tienda (tiendas × SKU sin
    stock pero con pedido en camino se agregan con stock 0), con su detalle
    ``transito_en_cd`` (aprobado, en picking) y ``transito_despachado`` (documentado, en
    transporte, prerecepcionado, dentro del plazo de la tienda)."""
    rec = params.recepcion
    p = pedidos_vigentes(inputs, params, excluir_madrugada)
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


def resumen_madrugada(inputs, params) -> dict:
    """Pedidos creados hoy antes de la hora de corte: unidades que contarían como tránsito
    y si la cabecera trae la hora para separarlos."""
    if not usa_pedidos(inputs, params):
        return {"con_hora": False, "unidades": 0}
    p = inputs.pedidos
    rec = params.recepcion
    m = madrugada(p, params)
    u = _transito(p.loc[m], inputs, params, rec.estados_transito, rec.estados_transito_recientes)
    return {
        "con_hora": con_hora(p),
        "unidades": int(u["stock_transito"].sum()) if len(u) else 0,
        "hora_corte": rec.hora_corte_pedidos,
    }


def recepcion_post_corte(inputs, params):
    """tienda_id, sku, recepcion_post_corte: lo recepcionado después del corte de stock_bi, que
    se suma al stock físico (None si está apagado o no hay pedidos)."""
    if not params.recepcion.sumar_recepcion_post_corte or not usa_pedidos(inputs, params):
        return None
    return recepcion_de_pedidos(inputs.pedidos, params.recepcion.clasificaciones_transito)


def transito_post_corte(inputs, params, excluir_madrugada: bool = False) -> float:
    """Unidades del tránsito que son pedidos creados después del corte de stock (normalmente,
    hoy): cuentan como tránsito para no enviar dos veces, y se informan aparte."""
    if not usa_pedidos(inputs, params):
        return 0.0
    p = pedidos_vigentes(inputs, params, excluir_madrugada)
    if "dias_pedido" not in p:
        return 0.0
    nuevos = p.loc[pd.to_numeric(p["dias_pedido"], errors="coerce").fillna(0) < 0]
    rec = params.recepcion
    tr = _transito(nuevos, inputs, params, rec.estados_transito, rec.estados_transito_recientes)
    return float(tr["stock_transito"].sum()) if len(tr) else 0.0
