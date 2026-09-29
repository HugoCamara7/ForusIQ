"""Forusight · reposición y distribución CD 320 → tiendas (Azaleia / Forus)."""

import sys
from pathlib import Path

import streamlit as st

# Streamlit Cloud instala requirements.txt; si el paquete no quedó instalado, se usa src/.
_RAIZ = Path(__file__).resolve().parent
for _ruta in (_RAIZ, _RAIZ / "src"):
    if str(_ruta) not in sys.path:
        sys.path.insert(0, str(_ruta))

st.set_page_config(
    page_title="Forusight", page_icon=str(_RAIZ / "assets" / "forus_icon.png"), layout="wide"
)

from app.components.estado import barra_lateral, fuente_por_defecto, inicializar  # noqa: E402
from app.components.login import puede, requerir_login  # noqa: E402


def _acceso() -> None:
    """Página vacía: sin sesión la navegación sólo tiene el acceso (menú oculto)."""


if not requerir_login(modo_demo=fuente_por_defecto() == "synthetic"):
    # Sin esto Streamlit sigue mostrando el menú de la ejecución anterior en el login.
    st.navigation([st.Page(_acceso, title="Acceso", url_path="acceso")], position="hidden").run()
    st.stop()

inicializar()
# Una sola vista (generar, aprobar y descargar). Parámetros y Conexión quedan en «Más opciones».
paginas = [
    st.Page(
        "app/vistas/1_Dashboard.py", title="Forusight", icon=":material/dashboard:", default=True
    ),
    st.Page("app/vistas/5_Parametros.py", title="Parámetros", icon=":material/tune:"),
]
if puede("conexion"):
    paginas.append(
        st.Page("app/vistas/6_Conexion.py", title="Conexión", icon=":material/database:")
    )
nav = st.navigation(paginas, position="hidden")
barra_lateral(paginas)
nav.run()
