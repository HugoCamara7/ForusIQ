"""Forusight · reposición y distribución CD 320 → tiendas (Azaleia / Forus)."""

import streamlit as st

st.set_page_config(page_title="Forusight", page_icon=":material/inventory_2:", layout="wide")

from app.components.estado import barra_lateral, inicializar  # noqa: E402

inicializar()
paginas = [
    st.Page(
        "app/pages/1_Dashboard.py", title="Dashboard", icon=":material/dashboard:", default=True
    ),
    st.Page("app/pages/2_Recomendaciones.py", title="Recomendaciones", icon=":material/list_alt:"),
    st.Page(
        "app/pages/3_Revision_Aprobacion.py",
        title="Revisión y aprobación",
        icon=":material/fact_check:",
    ),
    st.Page("app/pages/4_Exportacion.py", title="Exportación", icon=":material/download:"),
    st.Page("app/pages/5_Parametros.py", title="Parámetros", icon=":material/tune:"),
]
nav = st.navigation(paginas)
barra_lateral()
nav.run()
