"""Estado de sesión, caché y acceso al motor/repositorio para la UI.

- Una sola lectura de datos por (fuente, fecha de corte): ``st.cache_data`` con TTL.
- El motor se cachea por (fecha, parámetros): filtrar o editar no recalcula nada.
"""

from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

from forusight.config.settings import EngineParams, load_params
from forusight.data.bq_client import cargar_settings
from forusight.data.repository import BigQueryRepository, SyntheticRepository
from forusight.engine.pipeline import EngineInputs, EngineResult, ejecutar

SETTINGS = cargar_settings()


@st.cache_resource(show_spinner=False)
def repositorio(fuente: str):
    if fuente == "bigquery":
        return BigQueryRepository(settings=SETTINGS)
    return SyntheticRepository()


@st.cache_data(ttl=SETTINGS.cache_ttl_seconds, show_spinner="Leyendo datos…", max_entries=4)
def cargar_entradas(fuente: str, fecha_corte: str) -> EngineInputs:
    return repositorio(fuente).cargar_entradas(pd.Timestamp(fecha_corte))


@st.cache_data(
    ttl=SETTINGS.cache_ttl_seconds, show_spinner="Calculando distribución…", max_entries=8
)
def correr_motor(fuente: str, fecha_corte: str, params_json: str) -> EngineResult:
    params = EngineParams.model_validate_json(params_json)
    inputs = cargar_entradas(fuente, fecha_corte)
    run_id = f"{fecha_corte.replace('-', '')}-{hashlib.sha1(params_json.encode()).hexdigest()[:8]}"
    return ejecutar(inputs, params, fecha_corte, run_id=run_id, cd_id=SETTINGS.cd_id)


def lunes_actual() -> pd.Timestamp:
    hoy = pd.Timestamp.today().normalize()
    return hoy - pd.Timedelta(days=hoy.weekday())


def inicializar() -> None:
    ss = st.session_state
    if "params" not in ss:
        ss.params = load_params(SETTINGS.params_path)
    ss.setdefault("fuente", SETTINGS.data_source)
    ss.setdefault("fecha_corte", lunes_actual().date())
    ss.setdefault("resultado", None)
    ss.setdefault("aprobacion", None)
    ss.setdefault("usuario", "")


def ejecutar_corrida() -> EngineResult:
    ss = st.session_state
    res = correr_motor(
        ss.fuente, pd.Timestamp(ss.fecha_corte).date().isoformat(), ss.params.model_dump_json()
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
        ss.fuente = (
            st.segmented_control(
                "Fuente de datos",
                ["synthetic", "bigquery"],
                default=ss.fuente,
                format_func={"synthetic": "Demo sintética", "bigquery": "BigQuery"}.get,
            )
            or ss.fuente
        )
        ss.fecha_corte = st.date_input(
            "Fecha de corte (lunes de la semana en curso)", value=ss.fecha_corte
        )
        ss.usuario = st.text_input("Usuario (auditoría)", value=ss.usuario)
        if st.button("Ejecutar corrida", type="primary", width="stretch"):
            try:
                ejecutar_corrida()
            except Exception as exc:  # errores de datos/credenciales visibles al usuario
                st.error(f"No se pudo ejecutar la corrida: {exc}")
        res = ss.resultado
        if res is not None:
            st.caption(f"Corrida `{res.run_id}` · corte {res.resumen['fecha_corte']}")
