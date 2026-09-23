"""Filtros en memoria sobre el detalle de la corrida (sin volver a BigQuery ni al motor)."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from forusight.engine.reasons import DESCRIPCION_CODIGOS


def filtros_detalle(df: pd.DataFrame, key: str) -> pd.DataFrame:
    c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
    tiendas = c1.multiselect("Tienda", sorted(df["tienda_id"].unique()), key=f"{key}_t")
    categorias = c2.multiselect("Categoría", sorted(df["categoria"].unique()), key=f"{key}_c")
    motivos = c3.multiselect(
        "Motivo",
        sorted(df["motivo_codigo"].unique()),
        key=f"{key}_m",
        format_func=lambda c: DESCRIPCION_CODIGOS.get(c, c),
    )
    solo_envio = c4.toggle("Sólo con envío", value=True, key=f"{key}_e")
    texto = st.text_input("Buscar modelo o SKU", key=f"{key}_q", placeholder="p. ej. M003-NEG")

    mask = pd.Series(True, index=df.index)
    if tiendas:
        mask &= df["tienda_id"].isin(tiendas)
    if categorias:
        mask &= df["categoria"].isin(categorias)
    if motivos:
        mask &= df["motivo_codigo"].isin(motivos)
    if solo_envio:
        mask &= df["cantidad"] > 0
    if texto:
        t = texto.strip().upper()
        mask &= df["sku"].str.upper().str.contains(t, regex=False) | df[
            "modelo_color_id"
        ].str.upper().str.contains(t, regex=False)
    return df.loc[mask]
