"""Pantalla de acceso (mismo esquema que Catálogo/Repo Control Center)."""

from __future__ import annotations

import base64
from pathlib import Path

import streamlit as st

from forusight import auth
from forusight.data.bq_client import leer_st_secrets

LOGO = Path(__file__).resolve().parents[2] / "assets" / "forus_logo.png"


def _logo_html() -> str:
    try:
        b64 = base64.b64encode(LOGO.read_bytes()).decode()
        return f'<img src="data:image/png;base64,{b64}" alt="FORUS" style="height:42px">'
    except OSError:
        return "<strong>FORUS</strong>"


def usuario_actual() -> str:
    return st.session_state.get("auth_user", "")


def rol_actual() -> str:
    return st.session_state.get("auth_rol", auth.ROL_ANALISTA)


def puede(accion: str) -> bool:
    if st.session_state.get("sin_login") and accion == "conexion":
        return False  # el modo demo nunca toca BigQuery
    return auth.puede(rol_actual(), accion)


def requerir_login(modo_demo: bool) -> bool:
    """True si hay sesión. Sin usuarios configurados sólo se entra en modo demo."""
    ss = st.session_state
    if ss.get("authenticated"):
        return True
    secrets = leer_st_secrets() or {}
    if not auth.usuarios(secrets):
        if modo_demo:
            ss.authenticated, ss.auth_user, ss.auth_rol = True, "demo", auth.ROL_ADMIN
            ss.sin_login = True
            return True
        st.error(
            "No hay usuarios configurados. Agrega [app_auth] o [app_auth.users] a los secrets "
            "(Streamlit Cloud: Settings → Secrets). La app no trae usuarios por defecto."
        )
        return False

    _, centro, _ = st.columns([1, 1.2, 1])
    with centro, st.container(border=True):
        st.markdown(
            f'<div style="text-align:center">{_logo_html()}<h2 style="margin:.4rem 0 0">'
            "Forusight</h2><p style='opacity:.7'>Reposición y distribución CD 320 → tiendas</p>"
            "</div>",
            unsafe_allow_html=True,
        )
        with st.form("login_form"):
            usuario = st.text_input("Correo electrónico", placeholder="nombre.apellido@forus.pe")
            clave = st.text_input("Contraseña", type="password")
            entrar = st.form_submit_button("Ingresar", type="primary", width="stretch")
        st.caption("Sistema exclusivo para personal autorizado")
    if entrar:
        if auth.verificar(usuario, clave, secrets):
            ss.authenticated = True
            ss.auth_user = auth.normalizar(usuario)
            ss.auth_rol = auth.rol(usuario, secrets)
            st.rerun()
        st.error("Usuario o contraseña incorrectos.")
    return False


def cerrar_sesion() -> None:
    for k in ("authenticated", "auth_user", "auth_rol", "sin_login"):
        st.session_state.pop(k, None)
    st.rerun()
