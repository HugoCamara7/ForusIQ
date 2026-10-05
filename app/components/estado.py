"""Estado de sesión, caché y acceso al motor/repositorio para la UI.

- Una sola lectura de datos por (fuente, fecha, marcas, archivo CD): ``st.cache_data`` con TTL.
- El motor se cachea por (fecha, parámetros): filtrar o editar no recalcula nada.
"""

from __future__ import annotations

import contextlib
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
@st.cache_data(ttl=300, show_spinner=False, max_entries=4)
def _ultimas_cargas(fuente: str, huella: str, hoy: str) -> dict:
    """Última fecha cargada de stock y venta (se consulta cada 5 minutos como máximo)."""
    repo = _repositorio(fuente, huella)
    if not hasattr(repo, "ultimas_cargas"):
        return {}
    try:
        return repo.ultimas_cargas(pd.Timestamp(hoy).date())
    except Exception:  # sin la consulta se sigue con la caché normal
        return {}


def aviso_carga(diag: dict, dia: str | None = None) -> str | None:
    """Texto de alerta si la corrida se hizo antes de la carga diaria (stock o venta sin el
    cierre de ayer)."""
    hoy = pd.Timestamp.today().normalize()
    ref = min(pd.Timestamp(dia).normalize(), hoy) if dia else hoy  # reposición de otro día
    ayer = (ref - pd.Timedelta(days=1)).date()
    faltan = []
    for nombre, clave in (("stock", "fecha_foto"), ("venta", "venta_hasta")):
        f = diag.get(clave)
        if f and pd.Timestamp(f).date() < ayer:
            faltan.append(f"{nombre} al {pd.Timestamp(f):%d/%m}")
    if not faltan:
        return None
    return (
        f"La carga diaria aún no trae el cierre del {ayer:%d/%m} ({', '.join(faltan)}). "
        "Esta corrida se hizo antes de la carga: espera a que termine y vuelve a correr "
        "(la app detecta la carga nueva sola)."
    )


@st.cache_resource(ttl=_TTL, show_spinner="Leyendo datos…", max_entries=4)
def _cargar_entradas(
    fuente: str,
    huella: str,
    fecha_corte: str,
    cd_bytes: bytes | None,
    cd_nombre: str,
    marcas: tuple[str, ...] | None,
    cargas: str = "",  # última carga de stock y venta: si llega una nueva, se vuelve a leer
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
    huella = huella_config()
    return _cargar_entradas(
        fuente, huella, fecha_corte, cd_bytes, cd_nombre, marcas, clave_cargas(fuente, huella)
    )


def clave_cargas(fuente: str, huella: str) -> str:
    """Última carga de stock y venta: cambia cuando termina la carga diaria."""
    cargas = _ultimas_cargas(fuente, huella, pd.Timestamp.today().date().isoformat())
    return "|".join(f"{k}={v}" for k, v in sorted(cargas.items()))


@st.cache_resource(ttl=_TTL, show_spinner="Calculando distribución…", max_entries=8)
def _correr_motor(
    fuente: str,
    huella: str,
    fecha_corte: str,
    params_json: str,
    cd_bytes: bytes | None,
    cd_nombre: str,
    marcas: tuple[str, ...] | None,
    dia_reposicion: str = "",
    bloq_clave: str = "",
    niveles_clave: str = "",
    plan_huella: str = "",  # cambia si cambia el maestro de planificación (GitHub / repo)
    cargas: str = "",  # última carga de stock y venta (clave_cargas)
) -> tuple[EngineResult, dict]:
    from dataclasses import replace

    from forusight.data import calendario as CAL
    from forusight.data import transito as TR

    params = EngineParams.model_validate_json(params_json)
    inputs, diag = _cargar_entradas(
        fuente, huella, fecha_corte, cd_bytes, cd_nombre, marcas, cargas
    )
    # Tránsito: pedidos del sistema hacia la tienda (data.transito).
    transito_pedidos = TR.usa_pedidos(inputs, params)
    if transito_pedidos:  # sin tablas de pedidos legibles el tránsito queda en 0 (diagnóstico)
        inputs = TR.aplicar_pedidos(inputs, params)
    dia = dia_reposicion or pd.Timestamp.today().date().isoformat()
    dt = CAL.aplicar(inputs.dim_tienda, dia, params, list(marcas or []))
    from forusight.engine import venta_reciente as VR

    vr = VR.resumir(getattr(inputs, "venta_diaria", None), dt, dia, diag.get("fecha_foto"))
    # Recepcionado después del corte de stock_bi: se suma al stock físico (data.transito).
    recep = TR.recepcion_post_corte(inputs, params)
    if recep is not None and len(recep):
        vr = vr.merge(recep, on=["tienda_id", "sku"], how="outer")
        vr[["venta_desde_ruta", "venta_post_corte", "recepcion_post_corte"]] = vr[
            ["venta_desde_ruta", "venta_post_corte", "recepcion_post_corte"]
        ].fillna(0.0)
    bloq = bloqueos_de(bloq_clave)
    from forusight.data import temporadas as TEMP

    maestro = TEMP.combinar(
        TEMP.por_defecto(),
        _TEMPORADAS.get(bloq_clave.partition("|")[0]),
        _TEMPORADAS.get(niveles_clave),
    )
    inputs = replace(
        inputs,
        dim_producto=TEMP.aplicar(
            inputs.dim_producto, maestro, params.surtido.temporadas_en_bigquery
        ),
    )
    from forusight.data import planificacion as PLAN

    plan = maestro_planificacion(niveles_clave)
    plan_vig = PLAN.vigente(plan, dia, params.nivel_neo.dias_maximos)
    # Tiendas que reponen hoy sin claves de planificación: su nivel sale de otras tiendas.
    hoy = dt.loc[dt["activa"] & dt["recibe_hoy"]]
    con_plan = set(plan_vig.claves["tienda_id"].astype(str))
    sin_plan = hoy.loc[~hoy["tienda_id"].astype(str).isin(con_plan)]
    niveles = None
    inputs = replace(
        inputs,
        dim_tienda=dt,
        venta_reciente=vr,
        bloqueos=bloq,
        niveles_ref=niveles,
        planificacion=plan_vig,
    )
    diag = {
        **diag,
        "transito_pedidos": (
            {
                "unidades": int(inputs.stock_tienda["stock_transito"].sum()),
                "tiendas": int(
                    inputs.stock_tienda.loc[
                        inputs.stock_tienda["stock_transito"] > 0, "tienda_id"
                    ].nunique()
                ),
            }
            if transito_pedidos
            else None
        ),
        "dia_reposicion": dia,
        "aviso_carga": aviso_carga(diag, dia),
        "tiendas_hoy": int(dt.loc[dt["activa"] & dt["recibe_hoy"], "tienda_id"].nunique()),
        "tiendas_total": int(dt.loc[dt["activa"], "tienda_id"].nunique()),
        "venta_desde_ruta": int(vr["venta_desde_ruta"].sum()) if len(vr) else 0,
        "venta_post_corte": int(vr["venta_post_corte"].sum()) if len(vr) else 0,
        "recepcion_post_corte": (
            int(vr["recepcion_post_corte"].sum()) if "recepcion_post_corte" in vr else 0
        ),
        "malls_hoy": sorted({m for m in dt.loc[dt["activa"] & dt["recibe_hoy"], "mall"] if m}),
        "bloqueos": int(len(bloq)) if bloq is not None else 0,
        "niveles_ref": 0,
        "planificacion": resumen_planificacion(plan, dia),
        "tiendas_sin_planificacion": [
            f"{t} {n}" for t, n in zip(sin_plan["tienda_id"], sin_plan["nombre"], strict=True)
        ],
    }
    firma = hashlib.sha1(
        "|".join(
            [
                params_json,
                fuente,
                huella,
                cd_nombre,
                ",".join(marcas or ()),
                dia,
                bloq_clave,
                niveles_clave,
            ]
        ).encode()
        + (cd_bytes or b"")
    ).hexdigest()[:8]
    run_id = f"{fecha_corte.replace('-', '')}-{firma}"
    corte = diag.get("corte_venta") or fecha_corte  # venta atrasada: su última semana completa
    return ejecutar(inputs, params, corte, run_id=run_id, cd_id=ajustes().cd_id), diag


def correr_motor(
    fuente: str,
    fecha_corte: str,
    params_json: str,
    cd_bytes: bytes | None = None,
    cd_nombre: str = "",
    marcas: tuple[str, ...] | None = None,
    dia_reposicion: str = "",
    bloq_clave: str = "",
    niveles_clave: str = "",
):
    return _correr_motor(
        fuente,
        huella_config(),
        fecha_corte,
        params_json,
        cd_bytes,
        cd_nombre,
        marcas,
        dia_reposicion,
        bloq_clave,
        niveles_clave,
        huella_planificacion(niveles_clave),
        clave_cargas(fuente, huella_config()),
    )


#: Bloqueos subidos (reporte de bloqueos), por huella del contenido. Se pasa sólo la huella a las
#: funciones en caché para no volver a hashear archivos de varios MB en cada interacción.
_BLOQUEOS: dict[str, pd.DataFrame] = {}


@st.cache_data(max_entries=16, show_spinner="Leyendo reporte de bloqueos…")
def _leer_bloqueo(contenido: bytes) -> pd.DataFrame:
    from forusight.data import bloqueos as B

    return B.leer(contenido)


def clave_bloqueos() -> str:
    """Clave de bloqueos para la corrida: archivo base + huella del mantenedor."""
    base = st.session_state.get("bloq_clave", "defecto") or "defecto"
    h = huella_manuales()
    return f"{base}|{h}" if h else base


def registrar_bloqueos(archivos: list[bytes]) -> str:
    """Une los archivos de bloqueos y devuelve su huella ('' si no hay)."""
    from forusight.data import bloqueos as B

    if not archivos:
        return ""
    clave = hashlib.sha1(b"".join(hashlib.sha1(a).digest() for a in archivos)).hexdigest()[:12]
    if clave not in _BLOQUEOS:
        partes = [_leer_bloqueo(a) for a in archivos]
        _BLOQUEOS[clave] = B.unir(partes)
        t = pd.concat([p[["modelo_color_id", "temporada"]] for p in partes]).dropna()
        _TEMPORADAS[clave] = pd.Series(
            t["temporada"].to_numpy(), index=t["modelo_color_id"].to_numpy()
        ).pipe(lambda x: x[~x.index.duplicated()])
    return clave


#: Temporadas comerciales aprendidas de los archivos subidos (modelo-color → temporada).
_TEMPORADAS: dict[str, pd.Series] = {}


#: Maestros de planificación de los reportes subidos en la sesión, por huella del contenido.
_PLANIF: dict[str, object] = {}
CARPETA_PLAN = "planificacion"


def registrar_planificacion(archivos: list[tuple[bytes, str]]) -> str:
    """Reportes de distribución (Neogística) → maestro de planificación (SMT, nivel, reorden y
    UE por tienda × SKU; k por categoría) y temporadas. Se guarda en GitHub si está
    configurado. Devuelve la huella ('' si no hay archivos)."""
    from forusight.data import planificacion as PLAN
    from forusight.data import reporte as R
    from forusight.data import temporadas as TEMP

    if not archivos:
        return ""
    clave = hashlib.sha1(b"".join(hashlib.sha1(a).digest() for a, _ in archivos)).hexdigest()[:12]
    if clave not in _PLANIF:
        partes = []
        for contenido, nombre in archivos:
            df, fecha = _leer_reporte(contenido)
            fecha = fecha if fecha is not None else R.fecha_de_nombre(nombre)
            if fecha is None:
                raise ValueError(f"{nombre}: no se encontró la fecha del reporte.")
            partes.append((df, pd.Timestamp(fecha)))
        partes.sort(key=lambda p: p[1])
        ms = [PLAN.desde_reporte(df, f) for df, f in partes]
        _PLANIF[clave] = PLAN.combinar(*ms)
        _TEMPORADAS[clave] = TEMP.combinar(*[TEMP.desde_reporte(df) for df, _ in partes])
        store = _store_bloqueos()
        if store is not None:
            for m, (_, f) in zip(ms, partes, strict=True):
                contenido = PLAN.a_bytes(m)
                huella = hashlib.sha1(contenido).hexdigest()[:8]
                with contextlib.suppress(Exception):  # sin GitHub se usa igual en esta sesión
                    store.guardar(  # un archivo por reporte (varias rutas/marcas en un día)
                        f"{CARPETA_PLAN}/{f:%Y%m%d}_{huella}.csv.gz",
                        contenido,
                        f"forusight: maestro de planificación del {f:%d/%m/%Y}",
                    )
            _planif_github.clear()
    return clave


@st.cache_data(ttl=_TTL, show_spinner="Leyendo maestro de planificación…", max_entries=2)
def _planif_github(huella: str) -> list[bytes]:
    store = _store_bloqueos()
    if store is None:
        return []
    try:
        archivos = sorted(store.listar(CARPETA_PLAN), key=lambda f: f.get("name", ""))[-90:]
        return [c for f in archivos if (c := store.leer(f["path"]))]
    except Exception:
        return []


def huella_planificacion(clave: str = "") -> str:
    """Huella del maestro vigente (repo + GitHub + sesión), para la caché de la corrida."""
    from forusight.data import planificacion as PLAN

    h = hashlib.sha1(clave.encode())
    if PLAN.RUTA_CLAVES.exists():
        h.update(str(PLAN.RUTA_CLAVES.stat().st_mtime_ns).encode())
    for b in _planif_github(huella_config()):
        h.update(hashlib.sha1(b).digest())
    return h.hexdigest()[:12]


def maestro_planificacion(clave: str = ""):
    """Maestro vigente: el guardado en el repositorio + GitHub + los reportes de la sesión."""
    from forusight.data import planificacion as PLAN

    gh = []
    for b in _planif_github(huella_config()):
        with contextlib.suppress(Exception):  # un archivo dañado no frena la corrida
            gh.append(PLAN.desde_bytes(b))
    return PLAN.combinar(PLAN.por_defecto(), *gh, _PLANIF.get(clave))


def resumen_planificacion(m, dia) -> dict:
    if m is None or m.claves.empty:
        return {"claves": 0}
    f = pd.to_datetime(m.claves["fecha"])
    return {
        "claves": int(len(m.claves)),
        "ultimo_reporte": f.max().date().isoformat(),
        "dias": int((pd.Timestamp(dia) - f.max()).days),
    }


def _bloqueos_base(clave: str) -> pd.DataFrame | None:
    if clave == "defecto":
        from forusight.data import bloqueos as B

        return B.por_defecto()
    return _BLOQUEOS.get(clave) if clave else None


def bloqueos_de(clave: str) -> pd.DataFrame | None:
    """Bloqueos efectivos: reporte de bloqueos (guardado o subido) + mantenedor (bloquear/desbloquear).

    ``clave`` = «base» o «base|huella de los manuales» (la huella cambia la caché)."""
    from forusight.data import bloqueos as B

    base, _, _ = (clave or "").partition("|")
    return B.aplicar_manuales(_bloqueos_base(base), bloqueos_manuales())


# ------------------------------------------------------------------ mantenedor de bloqueos


def _store_bloqueos():
    from forusight.data.github_store import GitHubStore, config_github

    cfg = config_github(secretos())
    if cfg is None:
        return None
    return GitHubStore(cfg["repository"], cfg["token"], cfg["branch"], cfg["prefix"])


def bloqueos_manuales() -> pd.DataFrame:
    """Historial de bloqueos/desbloqueos manuales (GitHub; si no hay, sólo esta sesión)."""
    from forusight.data import bloqueos as B

    ss = st.session_state
    if "bloqueos_manuales" not in ss:
        store = _store_bloqueos()
        try:
            contenido = store.leer(f"{store.prefix}/{B.RUTA_MANUALES}") if store else None
            ss.bloqueos_manuales = B.leer_manuales(contenido)
        except Exception:
            ss.bloqueos_manuales = B.manuales_vacio()
    return ss.bloqueos_manuales


def huella_manuales() -> str:
    m = bloqueos_manuales()
    if m.empty:
        return ""
    return hashlib.sha1(m.to_csv(index=False).encode()).hexdigest()[:10]


def guardar_bloqueos_manuales(nuevos: pd.DataFrame, usuario: str) -> str:
    """Agrega movimientos al historial y lo guarda. Devuelve dónde quedó guardado."""
    from forusight.data import bloqueos as B

    ss = st.session_state
    todo = pd.concat([bloqueos_manuales(), nuevos], ignore_index=True)
    ss.bloqueos_manuales = todo
    store = _store_bloqueos()
    if store is None:
        return "esta sesión (sin GitHub configurado: descarga el historial para no perderlo)"
    ruta = store.guardar(
        B.RUTA_MANUALES,
        todo.to_csv(index=False).encode("utf-8-sig"),
        f"forusight: bloqueos manuales por {usuario}",
    )
    return f"GitHub ({store.repository}: {ruta})"


# ------------------------------------------------------------------ reporte del día como base


@st.cache_resource(show_spinner="Leyendo el reporte…", max_entries=2)
def _leer_reporte(contenido: bytes) -> tuple[pd.DataFrame, pd.Timestamp | None]:
    from forusight.data import reporte as R

    return R.leer_reporte(contenido)


def marcas_reporte(contenido: bytes) -> list[str]:
    df, _ = _leer_reporte(contenido)
    return sorted(df["Marca"].dropna().astype(str).str.strip().unique()) if "Marca" in df else []


@st.cache_resource(show_spinner="Preparando datos…", max_entries=4)
def _entradas_reporte(
    contenido: bytes, marcas: tuple[str, ...] | None
) -> tuple[EngineInputs, dict]:
    from forusight.data import reporte as R

    df, fecha = _leer_reporte(contenido)
    df = R.solo_revision(R.filtrar_marcas(df, list(marcas) if marcas else None))
    if df.empty:
        raise ValueError("El reporte no tiene filas para las marcas elegidas.")
    wk = R.semanas(df)
    diag = {
        "fecha_foto": (fecha or pd.Timestamp.today()).date().isoformat(),
        "fuente_venta": "reporte del día",
        "venta_hasta": (pd.Timestamp(max(wk)) + pd.Timedelta(days=6)).date().isoformat()
        if wk
        else None,
        "marcas": list(marcas or []),
        "tiendas": [],
        "notas": [],
    }
    inp = R.entradas_desde_reporte(df)
    diag["tiendas"] = inp.dim_tienda[
        ["tienda_id", "nombre", "cadena", "centro_comercial", "zona", "origen_tienda", "activa"]
    ].to_dict("records")
    return inp, diag


@st.cache_resource(show_spinner="Calculando distribución…", max_entries=6)
def _correr_reporte(
    contenido: bytes,
    marcas: tuple[str, ...] | None,
    criterio: str,
    params_json: str,
    bloq_clave: str = "",
) -> tuple[EngineResult, dict]:
    from forusight.data import reporte as R

    params = EngineParams.model_validate_json(params_json)
    inp, diag = _entradas_reporte(contenido, marcas)
    # El tránsito es el que trae el reporte (Stock Trán. Int. + Prov.).
    base, n_bloq = R.aplicar_surtido(
        inp.reporte, bloqueos_de(bloq_clave), params.surtido.temporadas_reponer
    )
    diag = {**diag, "bloqueadas": n_bloq}
    dist = R.distribuir(
        base, params.prioridad_tiendas.patrones, criterio, params.exhibicion.completar_curva
    )
    firma = hashlib.sha1(
        contenido[:4096]
        + len(contenido).to_bytes(8, "big")
        + criterio.encode()
        + ",".join(marcas or ()).encode()
        + params_json.encode()
        + bloq_clave.encode()
    ).hexdigest()[:8]
    corte = R.corte(inp.reporte)
    run_id = f"{corte:%Y%m%d}-{firma}"
    return R.resultado_desde_reporte(dist, run_id, corte, ajustes().cd_id, params), diag


def limpiar_cache() -> None:
    """Botón «Actualizar datos»: vuelve a leer BigQuery en la próxima corrida."""
    for f in (
        _cargar_entradas,
        _ultimas_cargas,
        _correr_motor,
        _marcas_arti,
        _repositorio,
        _entradas_reporte,
        _correr_reporte,
    ):
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
    ss.setdefault("reporte", None)  # (bytes, nombre) del reporte de distribución del día
    ss.setdefault("criterio", "reporte")
    app_styles()


def ejecutar_corrida() -> EngineResult:
    ss = st.session_state
    if ss.get("reporte"):
        marcas = tuple(ss.get("marcas_reporte") or ()) or None
        res, diag = _correr_reporte(
            ss.reporte[0],
            marcas,
            ss.criterio,
            ss.params.model_dump_json(),
            clave_bloqueos(),
        )
        if ss.resultado is None or ss.resultado.run_id != res.run_id:
            ss.aprobacion = None
        ss.resultado, ss.diagnostico = res, diag
        ss.ultima_carga = ("reporte", ss.reporte[0], marcas)
        ss.entradas_corrida = _entradas_reporte(ss.reporte[0], marcas)[0]
        return res
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
        pd.Timestamp(ss.get("dia_reposicion") or pd.Timestamp.today()).date().isoformat(),
        clave_bloqueos(),
        ss.get("niveles_clave", ""),
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
    # Se guarda la referencia (sin copiar): si la caché vence (TTL 1 h) o desaloja la entrada
    # (max_entries=4, compartida entre usuarios), un rerun cualquiera NO vuelve a leer BigQuery
    # y el Excel se arma con las mismas entradas que produjeron `res`.
    ss.entradas_corrida = cargar_entradas(*ss.ultima_carga)[0]
    return res


def entradas_de_la_corrida() -> EngineInputs | None:
    """Entradas de la última corrida (desde la caché: no vuelve a leer BigQuery)."""
    if (fijas := st.session_state.get("entradas_corrida")) is not None:
        return fijas
    carga = st.session_state.get("ultima_carga")
    if not carga:
        return None
    if carga[0] == "reporte":
        return _entradas_reporte(carga[1], carga[2])[0]
    return cargar_entradas(*carga)[0]


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
    # Con `key` la identidad del widget no depende de `default`: si se pasa default=ss.marcas,
    # cada cambio crea un widget NUEVO y la siguiente selección se pierde (y se cierra la lista).
    # El estado del widget se crea una vez desde ss.marcas (valor persistente entre páginas).
    if "sb_marcas" not in ss or any(m not in opciones for m in ss.sb_marcas):
        ss.sb_marcas = [m for m in (ss.marcas or []) if m in opciones]
    st.multiselect("Marca", opciones, key="sb_marcas", help="Marcas del maestro ARTI (MARCA_MA)")
    ss.marcas = list(ss.sb_marcas)


def _selector_marcas_reporte() -> None:
    ss = st.session_state
    try:
        opciones = marcas_reporte(ss.reporte[0])
    except Exception as exc:
        st.error(f"No se pudo leer el reporte.\n\n{exc}")
        ss.reporte = None
        return
    pedidas = [m.upper() for m in ajustes().marcas]
    previas = [m for m in (ss.get("marcas_reporte") or []) if m in opciones]
    ss.marcas_reporte = st.multiselect(
        "Marca",
        opciones,
        default=previas or [m for m in opciones if m.upper() in pedidas],
        placeholder="Todas las marcas",
        help="Vacío = todas las marcas del reporte",
    )


def _semana_a_lunes() -> None:
    """El motor arma las semanas desde el corte: un día que no es lunes descuadra la venta."""
    ss = st.session_state
    d = pd.Timestamp(ss.sb_semana)
    ss.sb_semana = (d - pd.Timedelta(days=d.weekday())).date()


def _cambiar_fuente() -> None:
    ss = st.session_state
    ss.fuente = ss.sb_fuente or ss.fuente


def barra_lateral(paginas: list | None = None) -> None:
    """Barra lateral mínima: logo arriba, páginas, y sólo marca + semana + ejecutar."""
    ss = st.session_state
    raiz = Path(__file__).resolve().parents[2] / "assets"
    st.logo(
        str(raiz / "forusight_logo.png"), size="large", icon_image=str(raiz / "forusight_icon.png")
    )
    with st.sidebar:
        html('<div class="sb-sec">Nueva corrida</div>', sidebar=True)
        opciones = fuentes_disponibles()
        if ss.fuente not in opciones:
            ss.fuente = opciones[0]
        # Sin reporte del día: la distribución sale de BigQuery (el reporte no se usa como base).
        ss.reporte = None
        if ss.fuente == "bigquery":
            _selector_marcas()
        if "sb_semana" not in ss:
            ss.sb_semana = ss.fecha_corte
        st.date_input(
            "Semana (lunes)",
            key="sb_semana",
            format="DD/MM/YYYY",
            on_change=_semana_a_lunes,
            help="Venta de las 12 semanas previas (se ajusta al lunes); el stock es el último "
            "corte disponible.",
        )
        ss.fecha_corte = ss.sb_semana
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
                (
                    "Stock al cierre",
                    _ddmm(pd.Timestamp(diag["fecha_foto"])) if diag.get("fecha_foto") else "",
                ),
                ("Unidades", f"{res.resumen['unidades_a_distribuir']:,}"),
            ]
            html(
                '<div class="sb-run">'
                + "".join(f"<div><span>{k}</span><b>{v}</b></div>" for k, v in filas if v)
                + "</div>",
                sidebar=True,
            )
            if diag.get("aviso_carga"):
                st.sidebar.error(diag["aviso_carga"])
            from app.components.archivo import boton_archivo

            boton_archivo("archivo_barra", en_barra=True)

        with st.expander("Más opciones", icon=":material/tune:"):
            if len(opciones) > 1:
                if ss.get("sb_fuente") != ss.fuente:
                    ss.sb_fuente = ss.fuente
                # on_change corre ANTES del script: la barra ya se dibuja con la fuente nueva
                st.segmented_control(
                    "Datos",
                    opciones,
                    key="sb_fuente",
                    format_func=FUENTES.get,
                    required=True,
                    on_change=_cambiar_fuente,
                )
            if not ss.get("reporte"):
                if "sb_dia" not in ss:
                    ss.sb_dia = ss.get("dia_reposicion") or pd.Timestamp.today().date()
                st.date_input(
                    "Día de reposición",
                    key="sb_dia",
                    format="DD/MM/YYYY",
                    help="Sólo reciben las tiendas que reponen ese día (calendario por tienda).",
                )
                ss.dia_reposicion = ss.sb_dia
            st.caption("Tránsito: pedidos del sistema aprobados, en picking o documentados.")
            archivos_b = st.file_uploader(
                "Reporte de bloqueos",
                type=["xlsx"],
                accept_multiple_files=True,
                key="bloq_uploader",
                help="Modelos bloqueados por tienda (todas sus partes): no se reponen ahí.",
            )
            try:
                from forusight.data import bloqueos as B

                # sin archivo nuevo se usan los bloqueos guardados (reporte de bloqueos del 28/09)
                ss.bloq_clave = (
                    registrar_bloqueos([a.getvalue() for a in archivos_b or []]) or "defecto"
                )
                origen = "subidos" if ss.bloq_clave != "defecto" else f"del {B.FECHA_POR_DEFECTO}"
                st.caption(
                    f"Bloqueos {origen}: {len(bloqueos_de(ss.bloq_clave)):,} modelo-color × tienda"
                )
            except Exception as exc:
                st.error(f"No se pudo leer el reporte de bloqueos: {exc}")
                ss.bloq_clave = "defecto"
            archivos_n = st.file_uploader(
                "Reportes de distribución (Neogística)",
                type=["xlsx"],
                accept_multiple_files=True,
                key="plan_uploader",
                help="Actualizan el maestro de planificación (stock mínimo, nivel, reorden y "
                "empaque por tienda × SKU; factor de pronóstico por categoría). Sube el más "
                "reciente de cada ruta: con un reporte de hasta 3 días la tienda cuadra ~90 %.",
            )
            try:
                ss.niveles_clave = registrar_planificacion(
                    [(a.getvalue(), a.name) for a in archivos_n or []]
                )
                info = resumen_planificacion(
                    maestro_planificacion(ss.niveles_clave),
                    ss.get("dia_reposicion") or pd.Timestamp.today(),
                )
                if info.get("claves"):
                    st.caption(
                        f"Planificación: {info['claves']:,} tienda × SKU · último reporte del "
                        f"{_ddmm(info['ultimo_reporte'])} (hace {info['dias']} días)"
                    )
            except Exception as exc:
                st.error(f"No se pudo leer el reporte de distribución: {exc}")
                ss.niveles_clave = ""
            if ss.fuente == "bigquery":
                archivo = st.file_uploader(
                    "Stock CD con reservas (opcional)",
                    type=["xlsx", "xls", "csv"],
                    key="cd_uploader",
                )
                ss.cd_archivo = (archivo.getvalue(), archivo.name) if archivo else None
            if paginas and len(paginas) > 1:
                st.caption("Configuración")
                for pg in paginas:
                    st.page_link(pg)
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
