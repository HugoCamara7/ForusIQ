"""Forusight · reposición y distribución CD 320 → tiendas (Azaleia / Forus)."""

import sys
from pathlib import Path

import streamlit as st

# Streamlit Cloud instala requirements.txt; si el paquete no quedó instalado, se usa src/.
_RAIZ = Path(__file__).resolve().parent
for _ruta in (_RAIZ, _RAIZ / "src"):
    if str(_ruta) not in sys.path:
        sys.path.insert(0, str(_ruta))

st.set_page_config(page_title="Forusight", page_icon=":material/inventory_2:", layout="wide")

from app.components.estado import barra_lateral, fuente_por_defecto, inicializar  # noqa: E402
from app.components.login import puede, requerir_login  # noqa: E402

if not requerir_login(modo_demo=fuente_por_defecto() == "synthetic"):
    st.stop()

inicializar()
paginas = [
    st.Page(
        "app/vistas/1_Dashboard.py", title="Dashboard", icon=":material/dashboard:", default=True
    ),
    st.Page("app/vistas/2_Recomendaciones.py", title="Recomendaciones", icon=":material/list_alt:"),
    st.Page(
        "app/vistas/3_Revision_Aprobacion.py",
        title="Revisión y aprobación",
        icon=":material/fact_check:",
    ),
    st.Page("app/vistas/4_Exportacion.py", title="Exportación", icon=":material/download:"),
    st.Page("app/vistas/5_Parametros.py", title="Parámetros", icon=":material/tune:"),
]
if puede("conexion"):
    paginas.append(
        st.Page("app/vistas/6_Conexion.py", title="Conexión", icon=":material/database:")
    )
nav = st.navigation(paginas)
barra_lateral()
nav.run()
