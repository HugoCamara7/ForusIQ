"""Estado de sesión, caché y acceso al motor/repositorio para la UI.

- Una sola lectura de datos por (fuente, fecha, marcas, archivo CD): ``st.cache_data`` con TTL.
- El motor se cachea por (fecha, parámetros): filtrar o editar no recalcula nada.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from app.components.icons import icon
from app.components.login import cerrar_sesion, puede, rol_actual, usuario_actual
from app.components.ui import app_styles, html
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

FUENTES = {"synthetic": "Demo", "bigquery": "BigQuery", "mart": "MART"}
_TTL = cargar_settings(leer_st_secrets() or {}).cache_ttl_seconds


def secretos() -> dict:
    """Se leen en cada ejecución: un cambio en los secrets se toma sin reiniciar."""
    return leer_st_secrets() or {}


def ajustes():
    return cargar_settings(secretos())


def __getattr__(nombre: str):
    # `from app.components.estado import SETTINGS` en las páginas: siempre valor fresco.
    if nombre == "SECRETS":
        return secretos()
    if nombre == "SETTINGS":
        return ajustes()
    raise AttributeError(nombre)


def huella_config() -> str:
    """Huella de la configuración que afecta a los datos (sin credenciales).

    Va en la clave de caché: si cambian tablas, mapeos, marcas o la cuenta, la caché se
    invalida sola en vez de servir datos de la configuración anterior.
    """
    s = secretos()
    cuenta = dict(s.get("gcp_service_account", {}) or {})
    relevante = {
        "bigquery": s.get("bigquery", {}),
        "forusight": s.get("forusight", {}),
        "cuenta": cuenta.get("client_email", ""),
    }
    return hashlib.sha1(json.dumps(relevante, sort_keys=True, default=str).encode()).hexdigest()[
        :12
    ]


def fuente_por_defecto() -> str:
    s, cfg = secretos(), ajustes()
    if "data_source" in dict(s.get("forusight", {}) or {}):
        return cfg.data_source
    return "bigquery" if bigquery_habilitado(s) else cfg.data_source


def fuentes_disponibles() -> list[str]:
    out = ["synthetic"]
    if st.session_state.get("sin_login"):
        return out  # sin login nunca se leen datos reales
    if bigquery_habilitado(secretos()):
        out.append("bigquery")
    if ajustes().data_source == "mart":
        out.append("mart")
    return out


@st.cache_resource(show_spinner=False, ttl=_TTL, max_entries=4)
def _repositorio(fuente: str, huella: str):
    s, cfg = secretos(), ajustes()
    if fuente == "bigquery":
        return FuentesRepository(settings=cfg, secrets=s)
    if fuente == "mart":
        return BigQueryRepository(settings=cfg)
    return SyntheticRepository()


def repositorio(fuente: str):
    return _repositorio(fuente, huella_config())


@st.cache_data(ttl=_TTL, show_spinner="Leyendo marcas de ARTI…", max_entries=8)
def _marcas_arti(fuente: str, huella: str) -> pd.DataFrame:
    return _repositorio(fuente, huella).marcas_disponibles()


def marcas_arti(fuente: str) -> pd.DataFrame:
    return _marcas_arti(fuente, huella_config())


# cache_resource: los DataFrames se comparten sin copiarse (mucho más rápido que
# cache_data, que los serializa en cada lectura). Las páginas nunca los modifican en sitio.
@st.cache_resource(ttl=_TTL, show_spinner="Leyendo datos…", max_entries=4)
def _cargar_entradas(
    fuente: str,
    huella: str,
    fecha_corte: str,
    cd_bytes: bytes | None,
    cd_nombre: str,
    marcas: tuple[str, ...] | None,
) -> tuple[EngineInputs, dict]:
    cd = leer_stock_cd_archivo(cd_bytes, cd_nombre) if cd_bytes else None
    repo = _repositorio(fuente, huella)
    inputs = repo.cargar_entradas(
        pd.Timestamp(fecha_corte),
        stock_cd_archivo=cd,
        marcas=list(marcas) if marcas is not None else None,
    )
    diag = getattr(repo, "ultimo_diagnostico", None)
    return inputs, (dict(diag.__dict__) if diag is not None else {})


def cargar_entradas(
    fuente: str,
    fecha_corte: str,
    cd_bytes: bytes | None = None,
    cd_nombre: str = "",
    marcas: tuple[str, ...] | None = None,
):
    return _cargar_entradas(fuente, huella_config(), fecha_corte, cd_bytes, cd_nombre, marcas)


@st.cache_resource(ttl=_TTL, show_spinner="Calculando distribución…", max_entries=8)
def _correr_motor(
    fuente: str,
    huella: str,
    fecha_corte: str,
    params_json: str,
    cd_bytes: bytes | None,
    cd_nombre: str,
    marcas: tuple[str, ...] | None,
) -> tuple[EngineResult, dict]:
    params = EngineParams.model_validate_json(params_json)
    inputs, diag = _cargar_entradas(fuente, huella, fecha_corte, cd_bytes, cd_nombre, marcas)
    firma = hashlib.sha1(
        "|".join([params_json, fuente, huella, cd_nombre, ",".join(marcas or ())]).encode()
        + (cd_bytes or b"")
    ).hexdigest()[:8]
    run_id = f"{fecha_corte.replace('-', '')}-{firma}"
    return ejecutar(inputs, params, fecha_corte, run_id=run_id, cd_id=ajustes().cd_id), diag


def correr_motor(
    fuente: str,
    fecha_corte: str,
    params_json: str,
    cd_bytes: bytes | None = None,
    cd_nombre: str = "",
    marcas: tuple[str, ...] | None = None,
):
    return _correr_motor(
        fuente, huella_config(), fecha_corte, params_json, cd_bytes, cd_nombre, marcas
    )


def limpiar_cache() -> None:
    """Botón «Actualizar datos»: vuelve a leer BigQuery en la próxima corrida."""
    for f in (_cargar_entradas, _correr_motor, _marcas_arti, _repositorio):
        f.clear()


def lunes_actual() -> pd.Timestamp:
    hoy = pd.Timestamp.today().normalize()
    return hoy - pd.Timedelta(days=hoy.weekday())


def inicializar() -> None:
    ss = st.session_state
    if "params" not in ss:
        ss.params = load_params(ajustes().params_path)
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
    if ss.fuente == "bigquery" and ss.get("marcas") is not None and not ss.marcas:
        raise ValueError("Elige al menos una marca en la barra lateral (p. ej. HUSH PUPPIES).")
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
    ss.ultima_carga = (
        ss.fuente,
        pd.Timestamp(ss.fecha_corte).date().isoformat(),
        cd[0],
        cd[1],
        marcas,
    )
    return res


def entradas_de_la_corrida() -> EngineInputs | None:
    """Entradas de la última corrida (desde la caché: no vuelve a leer BigQuery)."""
    carga = st.session_state.get("ultima_carga")
    return cargar_entradas(*carga)[0] if carga else None


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
        st.warning("ARTI no devolvió marcas: revisa la tabla en la página Conexión.")
        _marcas_arti.clear()  # no dejar el vacío en caché
        return
    if ss.marcas is None:
        pedidas = [m.upper() for m in ajustes().marcas]
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
    """Barra lateral mínima: logo arriba, páginas, y sólo marca + semana + ejecutar."""
    ss = st.session_state
    raiz = Path(__file__).resolve().parents[2] / "assets"
    st.logo(str(raiz / "forus_logo.png"), size="large", icon_image=str(raiz / "forus_icon.png"))
    with st.sidebar:
        html('<div class="sb-sec">Nueva corrida</div>', sidebar=True)
        opciones = fuentes_disponibles()
        if ss.fuente not in opciones:
            ss.fuente = opciones[0]
        if ss.fuente == "bigquery":
            _selector_marcas()
        ss.fecha_corte = st.date_input(
            "Semana (lunes)",
            value=ss.fecha_corte,
            format="DD/MM/YYYY",
            help="Venta de las 12 semanas previas; el stock es el último corte disponible.",
        )
        if st.button(
            "Ejecutar corrida",
            type="primary",
            width="stretch",
            icon=":material/play_arrow:",
            disabled=not puede("ejecutar"),
        ):
            try:
                ejecutar_corrida()
            except Exception as exc:  # errores de datos/credenciales visibles al usuario
                st.error(f"No se pudo ejecutar la corrida.\n\n{explicar_error(exc)}")

        res = ss.resultado
        if res is not None:
            diag = ss.get("diagnostico") or {}
            filas = [
                ("Corrida", res.run_id.split("-")[-1]),
                ("Stock al", _ddmm(diag.get("fecha_foto"))),
                ("Unidades", f"{res.resumen['unidades_a_distribuir']:,}"),
            ]
            html(
                '<div class="sb-run">'
                + "".join(f"<div><span>{k}</span><b>{v}</b></div>" for k, v in filas if v)
                + "</div>",
                sidebar=True,
            )

        with st.expander("Más opciones", icon=":material/tune:"):
            if len(opciones) > 1:
                ss.fuente = (
                    st.segmented_control(
                        "Datos", opciones, default=ss.fuente, format_func=FUENTES.get
                    )
                    or ss.fuente
                )
            if ss.fuente == "bigquery":
                archivo = st.file_uploader(
                    "Stock CD con reservas (opcional)",
                    type=["xlsx", "xls", "csv"],
                    key="cd_uploader",
                )
                ss.cd_archivo = (archivo.getvalue(), archivo.name) if archivo else None
            if ss.fuente != "synthetic" and st.button(
                "Volver a leer BigQuery",
                width="stretch",
                icon=":material/refresh:",
                help="Ignora la caché y trae los datos de nuevo",
            ):
                limpiar_cache()
                try:
                    ejecutar_corrida()
                except Exception as exc:
                    st.error(f"No se pudo ejecutar la corrida.\n\n{explicar_error(exc)}")

        html(
            f'<div class="sb-user">{icon("shield", 14)}<span><b>{usuario_actual()}</b>'
            f"<br>{rol_actual()}</span></div>",
            sidebar=True,
        )
        if ss.get("sin_login"):
            st.caption(":material/lock_open: Modo demo (datos de ejemplo)")
        elif st.button("Cerrar sesión", width="stretch", icon=":material/logout:", type="tertiary"):
            cerrar_sesion()


def _ddmm(fecha) -> str:
    return pd.Timestamp(fecha).strftime("%d/%m/%Y") if fecha else ""
