"""Estado de sesión, caché y acceso al motor/repositorio para la UI.

- Una sola lectura de datos por (fuente, fecha de corte, archivo CD): ``st.cache_data`` con TTL.
- El motor se cachea por (fecha, parámetros): filtrar o editar no recalcula nada.
"""

from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

from app.components.login import cerrar_sesion, puede, rol_actual, usuario_actual
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
FUENTES = {
    "synthetic": "Demo sintética",
    "bigquery": "BigQuery (tablas Forus)",
    "mart": "BigQuery (MART)",
}


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


@st.cache_data(ttl=SETTINGS.cache_ttl_seconds, show_spinner="Leyendo datos…", max_entries=4)
def cargar_entradas(
    fuente: str, fecha_corte: str, cd_bytes: bytes | None = None, cd_nombre: str = ""
) -> tuple[EngineInputs, dict]:
    cd = leer_stock_cd_archivo(cd_bytes, cd_nombre) if cd_bytes else None
    repo = repositorio(fuente)
    inputs = repo.cargar_entradas(pd.Timestamp(fecha_corte), stock_cd_archivo=cd)
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
) -> EngineResult:
    params = EngineParams.model_validate_json(params_json)
    inputs, _ = cargar_entradas(fuente, fecha_corte, cd_bytes, cd_nombre)
    huella = hashlib.sha1(
        (params_json + fuente + (cd_nombre or "")).encode() + (cd_bytes or b"")
    ).hexdigest()[:8]
    run_id = f"{fecha_corte.replace('-', '')}-{huella}"
    return ejecutar(inputs, params, fecha_corte, run_id=run_id, cd_id=SETTINGS.cd_id)


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
    ss.setdefault("aprobacion", None)
    ss.setdefault("cd_archivo", None)


def ejecutar_corrida() -> EngineResult:
    ss = st.session_state
    cd = ss.get("cd_archivo") or (None, "")
    res = correr_motor(
        ss.fuente,
        pd.Timestamp(ss.fecha_corte).date().isoformat(),
        ss.params.model_dump_json(),
        cd[0],
        cd[1],
    )
    if ss.resultado is None or ss.resultado.run_id != res.run_id:
        ss.aprobacion = None
    ss.resultado = res
    return res


def resultado_o_aviso() -> EngineResult | None:
    res = st.session_state.get("resultado")
    if res is None:
        st.info("Todavía no hay una corrida. Usa **Ejecutar corrida** en la barra lateral.")
    return res


def barra_lateral() -> None:
    ss = st.session_state
    with st.sidebar:
        st.markdown(f"### Forusight · CD {SETTINGS.cd_id}")
        st.caption(f"{usuario_actual()} · {rol_actual()}")
        opciones = fuentes_disponibles()
        if ss.fuente not in opciones:
            ss.fuente = opciones[0]
        ss.fuente = (
            st.segmented_control(
                "Fuente de datos",
                opciones,
                default=ss.fuente,
                format_func=FUENTES.get,
            )
            or ss.fuente
        )
        ss.fecha_corte = st.date_input(
            "Fecha de corte (lunes de la semana en curso)", value=ss.fecha_corte
        )
        if ss.fuente == "bigquery":
            archivo = st.file_uploader(
                "STOCK CD (opcional: disponible y reservas)",
                type=["xlsx", "xls", "csv"],
                key="cd_uploader",
            )
            ss.cd_archivo = (archivo.getvalue(), archivo.name) if archivo else None
        puede_ejecutar = puede("ejecutar")
        if st.button(
            "Ejecutar corrida", type="primary", width="stretch", disabled=not puede_ejecutar
        ):
            try:
                ejecutar_corrida()
            except Exception as exc:  # errores de datos/credenciales visibles al usuario
                st.error(f"No se pudo ejecutar la corrida:\n\n{explicar_error(exc)}")
        res = ss.resultado
        if res is not None:
            st.caption(f"Corrida `{res.run_id}` · corte {res.resumen['fecha_corte']}")
        if ss.get("sin_login"):
            st.caption(":material/lock_open: Sin login (modo demo, no hay [app_auth])")
        elif st.button("Cerrar sesión", width="stretch"):
            cerrar_sesion()
