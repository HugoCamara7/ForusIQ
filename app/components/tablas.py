"""Tablas de la UI (configuración de columnas común)."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from forusight.export.excel import columnas_display

COLUMNAS_TABLA = [
    "tienda_id",
    "modelo_color_id",
    "sku",
    "talla",
    "stock_tienda",
    "venta_4s",
    "venta_12s",
    "demanda_semanal",
    "stock_objetivo",
    "necesidad",
    "stock_cd_disponible",
    "cantidad",
    "motivo_codigo",
]


def config_columnas(cd_id: str = "320") -> dict:
    nombres = columnas_display(cd_id)
    cfg = {c: st.column_config.Column(n) for c, n in nombres.items()}
    cfg["demanda_semanal"] = st.column_config.NumberColumn(
        nombres["demanda_semanal"], format="%.2f"
    )
    cfg["cantidad"] = st.column_config.NumberColumn(nombres["cantidad"], format="%d")
    cfg["motivo_codigo"] = st.column_config.TextColumn("Código motivo")
    return cfg


def tabla_recomendaciones(df: pd.DataFrame, cd_id: str, key: str):
    """Tabla de sólo lectura con selección de una fila. Devuelve el evento de selección."""
    return st.dataframe(
        df[COLUMNAS_TABLA],
        column_config=config_columnas(cd_id),
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        key=key,
        height=460,
    )
