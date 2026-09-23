"""Panel de motivo: explica una fila de la distribución."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from forusight.engine.reasons import DESCRIPCION_CODIGOS


def panel_motivo(fila: pd.Series) -> None:
    envia = fila["cantidad"] > 0
    with st.container(border=True):
        st.markdown(f"**{fila['tienda_id']} · {fila['sku']}** (talla {fila['talla']})")
        (st.success if envia else st.warning)(fila["motivo_texto"])
        c = st.columns(4)
        c[0].metric("Estado modelo-color", fila["estado_mc"].replace("_", " ").title())
        c[1].metric("Afinidad", f"{fila['afinidad']:.2f}")
        c[2].metric("Cobertura objetivo", f"{fila['cobertura_semanas']:.1f} sem")
        c[3].metric("Fuente demanda", str(fila["fuente_demanda"]).title())
        st.caption(
            f"{DESCRIPCION_CODIGOS.get(fila['motivo_codigo'], fila['motivo_codigo'])} · "
            f"curva: share {fila['share_talla']:.1%} (prior {fila['nivel_prior'].lower()}) · "
            f"rotación {fila['rotacion']} · tendencia ×{fila['factor_tendencia']:.2f}"
        )
