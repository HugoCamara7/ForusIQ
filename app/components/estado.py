"""Estado de sesión, caché y acceso al motor/repositorio para la UI.

- Una sola lectura de datos por (fuente, fecha, marcas, archivo CD): ``st.cache_data`` con TTL.
- El motor se cachea por (fecha, parámetros): filtrar o editar no recalcula nada.
"""

from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

from app.components.login import cerrar_sesion, puede, rol_actual, usuario_actual
from app.components.ui import app_styles, html, sidebar_brand
from forusight.config.settings import EngineParams, load_params
from forusight.data.bq_client import (
    bigquery_habilitado,
    cargar_settings,
    explicar_error,
    leer_st_secrets,
)
from forusight.data.fuentes import leer_stock_cd_archivo
from forusight.data.repository import BigQueryRepository, FuentesRepository, SyntheticRepository
from forusight.engine.pipeline import EngineInputs, EngineResult, ejecutar

SECRETS = leer_st_secrets() or {}
SETTINGS = cargar_settings(SECRETS)
FUENTES = {"synthetic": "Demo", "bigquery": "BigQuery", "mart": "MART"}


def fuente_por_defecto() -> str:
    if "data_source" in dict(SECRETS.get("forusight", {}) or {}):
        return SETTINGS.data_source
    return "bigquery" if bigquery_habilitado(SECRETS) else SETTINGS.data_source


def fuentes_disponibles() -> list[str]:
    out = ["synthetic"]
    if st.session_state.get("sin_login"):
        return out  # sin login nunca se leen datos reales
    if bigquery_habilitado(SECRETS):
        out.append("bigquery")
    if SETTINGS.data_source == "mart":
        out.append("mart")
    return out


@st.cache_resource(show_spinner=False)
def repositorio(fuente: str):
    if fuente == "bigquery":
        return FuentesRepository(settings=SETTINGS, secrets=SECRETS)
    if fuente == "mart":
        return BigQueryRepository(settings=SETTINGS)
    return SyntheticRepository()


@st.cache_data(ttl=SETTINGS.cache_ttl_seconds, show_spinner="Leyendo marcas de ARTI…")
def marcas_arti(fuente: str) -> pd.DataFrame:
    return repositorio(fuente).marcas_disponibles()


@st.cache_data(ttl=SETTINGS.cache_ttl_seconds, show_spinner="Leyendo datos…", max_entries=4)
def cargar_entradas(
    fuente: str,
    fecha_corte: str,
    cd_bytes: bytes | None = None,
    cd_nombre: str = "",
    marcas: tuple[str, ...] | None = None,
) -> tuple[EngineInputs, dict]:
    cd = leer_stock_cd_archivo(cd_bytes, cd_nombre) if cd_bytes else None
    repo = repositorio(fuente)
    inputs = repo.cargar_entradas(
        pd.Timestamp(fecha_corte),
        stock_cd_archivo=cd,
        marcas=list(marcas) if marcas is not None else None,
    )
    diag = getattr(repo, "ultimo_diagnostico", None)
    return inputs, (diag.__dict__ if diag is not None else {})


@st.cache_data(
    ttl=SETTINGS.cache_ttl_seconds, show_spinner="Calculando distribución…", max_entries=8
)
def correr_motor(
    fuente: str,
    fecha_corte: str,
    params_json: str,
    cd_bytes: bytes | None = None,
    cd_nombre: str = "",
    marcas: tuple[str, ...] | None = None,
) -> tuple[EngineResult, dict]:
    params = EngineParams.model_validate_json(params_json)
    inputs, diag = cargar_entradas(fuente, fecha_corte, cd_bytes, cd_nombre, marcas)
    huella = hashlib.sha1(
        "|".join([params_json, fuente, cd_nombre, ",".join(marcas or ())]).encode()
        + (cd_bytes or b"")
    ).hexdigest()[:8]
    run_id = f"{fecha_corte.replace('-', '')}-{huella}"
    return ejecutar(inputs, params, fecha_corte, run_id=run_id, cd_id=SETTINGS.cd_id), diag


def lunes_actual() -> pd.Timestamp:
    hoy = pd.Timestamp.today().normalize()
    return hoy - pd.Timedelta(days=hoy.weekday())


def inicializar() -> None:
    ss = st.session_state
    if "params" not in ss:
        ss.params = load_params(SETTINGS.params_path)
    ss.setdefault("fuente", fuente_por_defecto())
    ss.setdefault("fecha_corte", lunes_actual().date())
    ss.setdefault("resultado", None)
    ss.setdefault("diagnostico", {})
    ss.setdefault("aprobacion", None)
    ss.setdefault("cd_archivo", None)
    ss.setdefault("marcas", None)
    app_styles()


def ejecutar_corrida() -> EngineResult:
    ss = st.session_state
    cd = ss.get("cd_archivo") or (None, "")
    marcas = tuple(ss.marcas) if ss.fuente == "bigquery" and ss.marcas else None
    res, diag = correr_motor(
        ss.fuente,
        pd.Timestamp(ss.fecha_corte).date().isoformat(),
        ss.params.model_dump_json(),
        cd[0],
        cd[1],
        marcas,
    )
    if ss.resultado is None or ss.resultado.run_id != res.run_id:
        ss.aprobacion = None
    ss.resultado, ss.diagnostico = res, diag
    return res


def resultado_o_aviso() -> EngineResult | None:
    res = st.session_state.get("resultado")
    if res is None:
        st.info(
            "Todavía no hay una corrida. Elige la marca y la fecha en la barra lateral y "
            "presiona **Ejecutar corrida**."
        )
    return res


def _selector_marcas() -> None:
    """Marcas reales de ARTI; por defecto las de `[forusight] marcas` (AZALEIA)."""
    ss = st.session_state
    try:
        df = marcas_arti(ss.fuente)
    except Exception as exc:
        st.warning(f"No se pudieron leer las marcas de ARTI.\n\n{explicar_error(exc)}")
        return
    opciones = [str(m) for m in df["marca"].dropna()]
    if not opciones:
        return
    if ss.marcas is None:
        pedidas = [m.upper() for m in SETTINGS.marcas]
        ss.marcas = [m for m in opciones if m in pedidas] or [
            m for m in opciones if any(p[:5] in m for p in pedidas)
        ][:1]
    ss.marcas = st.multiselect(
        "Marca",
        opciones,
        default=[m for m in ss.marcas if m in opciones],
        help="Marcas del maestro ARTI (MARCA_MA)",
    )


def barra_lateral() -> None:
    ss = st.session_state
    with st.sidebar:
        sidebar_brand(f"Reposición CD {SETTINGS.cd_id}")
        html(f'<div class="sb-user"><b>{usuario_actual()}</b> · {rol_actual()}</div>', sidebar=True)
        opciones = fuentes_disponibles()
        if ss.fuente not in opciones:
            ss.fuente = opciones[0]
        if len(opciones) > 1:
            ss.fuente = (
                st.segmented_control("Datos", opciones, default=ss.fuente, format_func=FUENTES.get)
                or ss.fuente
            )
        if ss.fuente == "bigquery":
            _selector_marcas()
        ss.fecha_corte = st.date_input("Semana de corte (lunes)", value=ss.fecha_corte)
        if ss.fuente == "bigquery":
            with st.expander("Stock CD con reservas (opcional)"):
                archivo = st.file_uploader(
                    "Archivo STOCK CD",
                    type=["xlsx", "xls", "csv"],
                    key="cd_uploader",
                    label_visibility="collapsed",
                )
            ss.cd_archivo = (archivo.getvalue(), archivo.name) if archivo else None
        if st.button(
            "Ejecutar corrida", type="primary", width="stretch", disabled=not puede("ejecutar")
        ):
            try:
                ejecutar_corrida()
            except Exception as exc:  # errores de datos/credenciales visibles al usuario
                st.error(f"No se pudo ejecutar la corrida.\n\n{explicar_error(exc)}")
        res = ss.resultado
        if res is not None:
            diag = ss.get("diagnostico") or {}
            texto = f"Corrida `{res.run_id}`"
            if diag.get("fecha_foto"):
                texto += f" · stock al {diag['fecha_foto']} · venta: {diag.get('fuente_venta')}"
            st.caption(texto)
        if ss.get("sin_login"):
            st.caption(":material/lock_open: Modo demo (datos sintéticos)")
        elif st.button("Cerrar sesión", width="stretch"):
            cerrar_sesion()
