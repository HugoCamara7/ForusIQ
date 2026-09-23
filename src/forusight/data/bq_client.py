"""Cliente BigQuery. Credenciales SOLO desde st.secrets o variables de entorno.

Usa el mismo esquema de secrets que Catálogo Control Center y Repo Control Center, así
que los bloques se pueden pegar tal cual:

```toml
[bigquery]
enabled = true
project_id = "..."            # proyecto de las tablas (o por defecto de la cuenta)
job_project_id = "..."        # proyecto donde corren (y se facturan) los jobs
location = "US"
ventas_table = "proyecto.dataset.tabla"
product_master_table = "proyecto.dataset.tabla"   # ARTI (alias heredado: `table`)
stock_table = "proyecto.dataset.tabla"            # stock por fecha de corte
max_gb = 20                                       # tope por consulta (dry run)

[gcp_service_account]
...JSON de la cuenta de servicio...
```

Las rutas de las tablas viven sólo en los secrets: el código no trae valores por defecto.

Orden de resolución de credenciales:
  1. JSON de la cuenta de servicio en los secrets ([gcp_service_account], o anidado en
     [bigquery], [connections], [gcp], [service_account], [google_service_account]).
  2. ``FORUSIGHT_GCP_SA_JSON``: el JSON en una variable de entorno.
  3. Application Default Credentials (Cloud Run, gcloud auth application-default login).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from collections.abc import Mapping
from typing import Any

import pandas as pd

from forusight.config.settings import AppSettings

SCOPES = ["https://www.googleapis.com/auth/bigquery"]
MAX_GB_POR_DEFECTO = 20.0
_IDENT = re.compile(r"^[A-Za-z0-9_\-]+$")
_COLUMNA = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PLACEHOLDERS = {"proyecto", "dataset", "tabla", "proy", "tu_project_id", "..."}

UBICACIONES_CUENTA = (
    ("gcp_service_account",),
    ("bigquery", "gcp_service_account"),
    ("connections", "gcp_service_account"),
    ("gcp",),
    ("service_account",),
    ("google_service_account",),
)
CAMPOS_CUENTA = ("client_email", "private_key")

#: claves de [bigquery] → nombre lógico de la tabla (primera que exista gana)
CLAVES_TABLA = {
    "ventas": ("ventas_table", "tabla_ventas"),
    "arti": ("product_master_table", "table"),
    "stock": ("stock_table", "tabla_stock"),
}


# ------------------------------------------------------------------ secrets


def leer_st_secrets() -> Mapping[str, Any] | None:
    """Devuelve st.secrets como dict, o None si no hay Streamlit o no hay secrets."""
    try:
        import streamlit as st

        return st.secrets.to_dict() if len(st.secrets) else None
    except Exception:  # sin streamlit, sin secrets.toml, o fuera de una sesión
        return None


def _secrets(secrets: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return (leer_st_secrets() if secrets is None else secrets) or {}


def buscar_cuenta(secrets: Mapping[str, Any] | None) -> dict | None:
    """JSON de la cuenta de servicio, esté donde esté dentro de los secrets."""
    s = _secrets(secrets)
    for ruta in UBICACIONES_CUENTA:
        nodo: Any = s
        try:
            for paso in ruta:
                nodo = nodo.get(paso) if hasattr(nodo, "get") else None
                if nodo is None:
                    break
            if nodo is None:
                continue
            candidato = dict(nodo)
        except Exception:
            continue
        if all(str(candidato.get(c, "")).strip() for c in CAMPOS_CUENTA):
            return candidato
    return None


def config_bigquery(secrets: Mapping[str, Any] | None = None) -> dict:
    """Bloque [bigquery] sin la cuenta de servicio (que se resuelve aparte)."""
    s = _secrets(secrets)
    try:
        cfg = {
            k: v
            for k, v in dict(s.get("bigquery", {}) or {}).items()
            if k not in ("gcp_service_account", "service_account")
        }
    except Exception:
        cfg = {}
    return cfg


def cargar_settings(secrets: Mapping[str, Any] | None = None) -> AppSettings:
    """[forusight] manda; proyecto/región salen de [bigquery] si no están; luego FORUSIGHT_*."""
    s = _secrets(secrets)
    seccion = dict(s.get("forusight", {}) or {})
    bq = config_bigquery(s)
    proyecto = bq.get("job_project_id") or bq.get("project_id")
    if proyecto and "gcp_project" not in seccion:
        seccion["gcp_project"] = str(proyecto)
    if bq.get("location") and "bq_location" not in seccion:
        seccion["bq_location"] = str(bq["location"])
    return AppSettings(**seccion)


def bigquery_habilitado(secrets: Mapping[str, Any] | None = None) -> bool:
    cfg = config_bigquery(secrets)
    if not cfg:
        return False
    return str(cfg.get("enabled", "true")).strip().lower() not in ("0", "false", "no", "off")


def tabla_configurada(nombre: str, secrets: Mapping[str, Any] | None = None) -> str:
    """Ruta proyecto.dataset.tabla de los secrets, validada. Error claro si falta."""
    cfg = config_bigquery(secrets)
    claves = CLAVES_TABLA[nombre]
    valor = next((str(cfg[k]).strip() for k in claves if str(cfg.get(k, "")).strip()), "")
    if not valor:
        raise ValueError(
            f"Falta la tabla de {nombre} en los secrets: agrega `{claves[0]} = "
            '"proyecto.dataset.tabla"` dentro del bloque [bigquery] (sin repetir el encabezado).'
        )
    return validar_tabla(valor)


def validar_tabla(ruta: str) -> str:
    partes = [p.strip().strip("`") for p in str(ruta).split(".")]
    if len(partes) != 3 or not all(partes):
        raise ValueError(f"La tabla debe venir como proyecto.dataset.tabla y llegó {ruta!r}")
    if any(p.lower() in _PLACEHOLDERS for p in partes):
        raise ValueError(f"La tabla {ruta!r} quedó con el texto de ejemplo de la plantilla")
    for p in partes:
        validar_identificador(p)
    return ".".join(partes)


def diagnostico_secrets(secrets: Mapping[str, Any] | None = None) -> list[str]:
    """Qué bloques y claves ve la app. Sólo NOMBRES: nunca se muestran valores."""
    s = _secrets(secrets)
    lineas = ["Bloques: " + (", ".join(sorted(map(str, s.keys()))) or "ninguno")]
    bq = config_bigquery(s)
    if bq:
        lineas.append("Claves en [bigquery]: " + ", ".join(sorted(map(str, bq.keys()))))
    cuenta = buscar_cuenta(s)
    lineas.append(
        f"Cuenta de servicio: encontrada ({cuenta.get('client_email', '?')})"
        if cuenta
        else "Cuenta de servicio: NO encontrada (se buscó [gcp_service_account] y variantes)"
    )
    return lineas


def resolver_credenciales(secrets: Mapping[str, Any] | None = None):
    """Credenciales de cuenta de servicio o ``None`` para usar ADC."""
    from google.oauth2 import service_account

    info = buscar_cuenta(secrets)
    if info:
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    crudo = config_bigquery(secrets).get("service_account_json") or os.environ.get(
        "FORUSIGHT_GCP_SA_JSON"
    )
    if crudo:
        return service_account.Credentials.from_service_account_info(
            json.loads(crudo), scopes=SCOPES
        )
    return None


# ------------------------------------------------------------------ errores

_PISTAS = (
    ("invalid jwt signature", "firma"),
    ("invalid_grant", "firma"),
    ("could not deserialize key", "formato"),
    ("no key could be detected", "formato"),
    ("access denied", "permiso"),
    ("permission denied", "permiso"),
    ("user does not have", "permiso"),
    ("not found: dataset", "ruta"),
    ("not found: table", "ruta"),
    ("metadata.google.internal", "sin_cuenta"),
    ("default credentials", "sin_cuenta"),
    ("leería", "tope"),
)
_GUIA = {
    "firma": "La private_key no valida contra su client_email. En TOML va en UNA línea, entre "
    "comillas dobles, con los saltos como \\n; revisa que no esté rotada.",
    "formato": "La private_key no se pudo leer: pégala completa, con BEGIN y END.",
    "permiso": "La cuenta autenticó pero no tiene permiso: roles/bigquery.dataViewer sobre el "
    "dataset y roles/bigquery.jobUser sobre el proyecto de los jobs.",
    "ruta": "El dataset o la tabla no existen con ese nombre (distingue mayúsculas).",
    "sin_cuenta": "Falta [gcp_service_account] en los secrets: sin ella se intenta autenticar "
    "como si la app corriera dentro de Google Cloud.",
    "tope": "La consulta supera el tope de GB: acorta el rango o sube `max_gb` en [bigquery].",
}


def explicar_error(exc: Exception) -> str:
    """Traduce el error de Google a algo accionable, sin ocultar el original."""
    crudo = f"{type(exc).__name__}: {exc}"
    texto = str(exc).lower()
    for aguja, clave in _PISTAS:
        if aguja in texto:
            return f"{_GUIA[clave]}\n\nError original: {crudo}"
    return crudo


# ------------------------------------------------------------------ parámetros


def validar_identificador(nombre: str) -> str:
    """Proyectos/datasets/tablas no se pueden parametrizar: se validan antes de formatear."""
    if not nombre or not _IDENT.match(nombre):
        raise ValueError(f"Identificador BigQuery inválido: {nombre!r}")
    return nombre


def validar_columna(nombre: str) -> str:
    if not nombre or not _COLUMNA.match(str(nombre)):
        raise ValueError(f"Nombre de columna inválido: {nombre!r}")
    return str(nombre)


def _tipo(v: Any) -> str:
    if isinstance(v, bool):
        return "BOOL"
    if isinstance(v, int):
        return "INT64"
    if isinstance(v, float):
        return "FLOAT64"
    if isinstance(v, pd.Timestamp | dt.datetime):
        return "TIMESTAMP"
    if isinstance(v, dt.date):
        return "DATE"
    return "STRING"


def a_query_parameters(params: Mapping[str, Any] | None) -> list:
    """dict → parámetros de consulta (escalares o arrays). Nunca se interpola SQL."""
    from google.cloud import bigquery

    out = []
    for nombre, valor in (params or {}).items():
        if isinstance(valor, list | tuple | set):
            valores = list(valor)
            tipo = _tipo(valores[0]) if valores else "STRING"
            out.append(bigquery.ArrayQueryParameter(nombre, tipo, valores))
        else:
            out.append(bigquery.ScalarQueryParameter(nombre, _tipo(valor), valor))
    return out


# ------------------------------------------------------------------ cliente


class BigQueryClient:
    """Envoltorio delgado sobre google.cloud.bigquery.Client con control de costo."""

    def __init__(
        self,
        settings: AppSettings | None = None,
        credentials=None,
        client=None,
        max_gb: float | None = None,
        secrets: Mapping[str, Any] | None = None,
    ) -> None:
        self._secrets = secrets
        self.settings = settings or cargar_settings(secrets)
        self._credentials = credentials
        self._client = client
        cfg_max = config_bigquery(secrets).get("max_gb")
        self.max_gb = float(max_gb if max_gb is not None else cfg_max or MAX_GB_POR_DEFECTO)
        self.gb_leidos = 0.0

    @property
    def client(self):
        if self._client is None:
            from google.cloud import bigquery

            creds = (
                self._credentials
                if self._credentials is not None
                else resolver_credenciales(self._secrets)
            )
            project = self.settings.gcp_project or getattr(creds, "project_id", None)
            self._client = bigquery.Client(
                project=project, credentials=creds, location=self.settings.bq_location
            )
        return self._client

    def tabla(self, dataset: str, nombre: str) -> str:
        proyecto = validar_identificador(self.settings.gcp_project or self.client.project)
        return f"`{proyecto}.{validar_identificador(dataset)}.{validar_identificador(nombre)}`"

    def _config(self, params, labels, dry_run: bool = False):
        from google.cloud import bigquery

        return bigquery.QueryJobConfig(
            query_parameters=a_query_parameters(params),
            labels={"app": "forusight", **(labels or {})},
            dry_run=dry_run,
            use_query_cache=not dry_run,
            maximum_bytes_billed=None
            if dry_run
            else int(self.max_gb * 1e9)
            if self.max_gb
            else None,
        )

    def estimar_gb(self, sql: str, params: Mapping[str, Any] | None = None) -> float:
        """Dry run: cuánto se leería, sin leer ni cobrar."""
        job = self.client.query(sql, job_config=self._config(params, None, dry_run=True))
        return (job.total_bytes_processed or 0) / 1e9

    def query_df(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
        labels: Mapping[str, str] | None = None,
    ) -> pd.DataFrame:
        gb = self.estimar_gb(sql, params)
        if self.max_gb and gb > self.max_gb:
            raise RuntimeError(
                f"La consulta leería {gb:.1f} GB y el tope es {self.max_gb:.0f} GB. "
                "Acorta el rango o sube `max_gb` en [bigquery]."
            )
        df = self.client.query(sql, job_config=self._config(params, labels)).to_dataframe()
        self.gb_leidos += gb
        return df

    def columnas(self, tabla: str) -> pd.DataFrame:
        """Esquema de una tabla vía INFORMATION_SCHEMA (gratis, no toca los datos)."""
        proyecto, dataset, nombre = validar_tabla(tabla).split(".")
        sql = (
            f"SELECT column_name, data_type FROM `{proyecto}.{dataset}.INFORMATION_SCHEMA.COLUMNS` "
            "WHERE table_name = @tabla ORDER BY ordinal_position"
        )
        return self.client.query(
            sql, job_config=self._config({"tabla": nombre}, None)
        ).to_dataframe()

    def load_df(
        self, df: pd.DataFrame, tabla_id: str, write_disposition: str = "WRITE_APPEND"
    ) -> Any:
        """Load job (no streaming inserts): atómico y sin costo de inserción."""
        from google.cloud import bigquery

        cfg = bigquery.LoadJobConfig(write_disposition=write_disposition)
        job = self.client.load_table_from_dataframe(df, tabla_id.strip("`"), job_config=cfg)
        return job.result()
