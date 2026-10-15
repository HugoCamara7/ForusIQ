"""Pantalla de acceso con el diseño de Catálogo / Repo Control Center."""

from __future__ import annotations

import streamlit as st

from app.components.ui import (
    brand_strip,
    forus_logo_html,
    forusight_logo_html,
    html,
    login_styles,
)
from forusight import auth
from forusight.data.bq_client import leer_st_secrets


def usuario_actual() -> str:
    return st.session_state.get("auth_user", "")


def rol_actual() -> str:
    return st.session_state.get("auth_rol", auth.ROL_ANALISTA)


def puede(accion: str) -> bool:
    if st.session_state.get("sin_login") and accion == "conexion":
        return False  # el modo demo nunca toca BigQuery
    if accion in auth.RESTRINGIDOS:
        return acceso_modulo(accion)
    return auth.puede(rol_actual(), accion)


def acceso_modulo(modulo: str) -> bool:
    """Módulo restringido a la lista de [app_auth.permisos] (p. ej. Parámetros).

    Se valida el usuario autenticado de la sesión contra los secrets en cada llamada: quitar
    a alguien de la lista le corta el acceso en la siguiente interacción. El modo demo (sin
    usuarios configurados, datos sintéticos y sin GitHub) puede usarlo: no hay nada real que
    cambiar."""
    ss = st.session_state
    if not ss.get("authenticated"):
        return False
    if ss.get("sin_login"):
        return True
    return auth.acceso_restringido(usuario_actual(), modulo, leer_st_secrets() or {})


def exigir(modulo: str) -> None:
    """Corta la página si el usuario no tiene acceso (además de no listarla en el menú)."""
    if not acceso_modulo(modulo):
        st.error("No tienes acceso a este módulo.")
        st.stop()


def requerir_login(modo_demo: bool, al_ingresar=None) -> bool:
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
            </div>
            <div class="login-forusight-logo">{forusight_logo_html("forusight_logo_blanco.png")}</div>
            <p>Reposición y distribución CD 320 → tiendas</p>
        </div>
        """)
        brand_strip()
        with st.container(key="login_form_area"):
            with st.form("login_form"):
                usuario = st.text_input(
                    "Correo electrónico", placeholder="nombre.apellido@forus.pe"
                )
                clave = st.text_input("Contraseña", type="password", placeholder="********")
                entrar = st.form_submit_button("Ingresar", type="primary", width="stretch")
            aviso = st.empty()  # spinner de la precarga, dentro de la tarjeta
        html('<div class="login-note">Sistema exclusivo para personal autorizado</div>')
    with st.container(key="login_foot"):
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
            if (
                al_ingresar is not None
            ):  # p. ej. leer las marcas de ARTI con el login aún en pantalla
                with aviso.container(), st.spinner("Ingresando…"):
                    al_ingresar()
            ss.authenticated = True
            ss.auth_user = auth.normalizar(usuario)
            ss.auth_rol = auth.rol(usuario, secrets)
            st.rerun()
        st.error("Usuario o contraseña incorrectos.")
    return False


def cerrar_sesion() -> None:
    # corrida, aprobación, marcas y bloqueos del usuario anterior no deben verse al reingresar
    st.session_state.clear()
    st.rerun()
