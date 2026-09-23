"""Indicadores de la corrida."""

from __future__ import annotations

import pandas as pd
import streamlit as st


def mostrar_kpis(resumen: dict, detalle: pd.DataFrame) -> None:
    quiebres = detalle.loc[detalle["estado_mc"] == "QUIEBRE", ["tienda_id", "modelo_color_id"]]
    c = st.columns(5)
    c[0].metric("Unidades a distribuir", f"{resumen['unidades_a_distribuir']:,}")
    c[1].metric("Tiendas con envío", resumen["tiendas_con_envio"])
    c[2].metric(
        "Cobertura de necesidad",
        f"{resumen['fill_rate']:.0%}",
        help="Unidades asignadas / necesidad total",
    )
    c[3].metric(f"Stock CD {resumen['cd_id']} disponible", f"{resumen['stock_cd_disponible']:,}")
    c[4].metric("Modelo-color en quiebre", f"{len(quiebres.drop_duplicates()):,}")
