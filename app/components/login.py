"""Pantalla de acceso con el diseño de Catálogo / Repo Control Center."""

from __future__ import annotations

import streamlit as st

from app.components.ui import brand_strip, forus_logo_html, html, login_styles
from forusight import auth
from forusight.data.bq_client import leer_st_secrets


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
    if not auth.usuarios(secrets) and modo_demo:
        ss.authenticated, ss.auth_user, ss.auth_rol = True, "demo", auth.ROL_ADMIN
        ss.sin_login = True
        return True

    login_styles()
    with st.container(key="login_card"):
        html(f"""
        <div class="login-head">
            <div class="login-logo-row">
                <div class="login-forus-logo">{forus_logo_html()}</div>
                <div class="login-divider"></div>
                <div class="login-app-badge">👟</div>
            </div>
            <h1>Forusight</h1>
            <p>Reposición y distribución CD 320 → tiendas</p>
        </div>
        """)
        brand_strip()
        with st.container(key="login_form_area"), st.form("login_form"):
            usuario = st.text_input("Correo electrónico", placeholder="nombre.apellido@forus.pe")
            clave = st.text_input("Contraseña", type="password", placeholder="********")
            entrar = st.form_submit_button("Ingresar", type="primary", width="stretch")
        html('<div class="login-note">Sistema exclusivo para personal autorizado</div>')
    html("""
    <div class="login-foot">
        <strong>Reposición Azaleia</strong>
        CD 320 &bull; Tiendas &bull; Área de Producto
    </div>
    """)

    if not auth.usuarios(secrets):
        st.error(
            "No hay usuarios configurados. Pega el bloque [app_auth] de Catálogo Control "
            "Center en los secrets (Settings → Secrets)."
        )
        return False
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
