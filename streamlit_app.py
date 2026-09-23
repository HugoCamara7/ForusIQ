"""Forusight · reposición y distribución CD 320 → tiendas (Azaleia / Forus)."""

import streamlit as st

st.set_page_config(page_title="Forusight", page_icon=":material/inventory_2:", layout="wide")

from app.components.estado import barra_lateral, fuente_por_defecto, inicializar  # noqa: E402
from app.components.login import puede, requerir_login  # noqa: E402

if not requerir_login(modo_demo=fuente_por_defecto() == "synthetic"):
    st.stop()

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
if puede("conexion"):
    paginas.append(st.Page("app/pages/6_Conexion.py", title="Conexión", icon=":material/database:"))
nav = st.navigation(paginas)
barra_lateral()
nav.run()
