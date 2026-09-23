"""Cliente BigQuery. Credenciales SOLO desde st.secrets o variables de entorno.

Orden de resolución de credenciales:
  1. ``st.secrets["gcp_service_account"]`` (Streamlit Cloud / secrets.toml local).
  2. ``FORUSIGHT_GCP_SA_JSON``: JSON de la cuenta de servicio en una variable de entorno.
  3. Application Default Credentials (GOOGLE_APPLICATION_CREDENTIALS, gcloud auth
     application-default login, o workload identity en Cloud Run/GKE).

Este módulo no conoce tablas reales: ejecuta SQL parametrizado y hace load jobs.
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
_IDENT = re.compile(r"^[A-Za-z0-9_\-]+$")


def leer_st_secrets() -> Mapping[str, Any] | None:
    """Devuelve st.secrets como dict, o None si no hay Streamlit o no hay secrets."""
    try:
        import streamlit as st

        return st.secrets.to_dict() if len(st.secrets) else None
    except Exception:  # sin streamlit, sin secrets.toml, o fuera de una sesión
        return None


def cargar_settings(secrets: Mapping[str, Any] | None = None) -> AppSettings:
    """``[forusight]`` de secrets tiene prioridad; el resto sale de FORUSIGHT_* del entorno."""
    secrets = leer_st_secrets() if secrets is None else secrets
    seccion = dict((secrets or {}).get("forusight", {}))
    return AppSettings(**seccion)


def resolver_credenciales(secrets: Mapping[str, Any] | None = None):
    """Credenciales de cuenta de servicio o ``None`` para usar ADC."""
    from google.oauth2 import service_account

    secrets = leer_st_secrets() if secrets is None else secrets
    info = (secrets or {}).get("gcp_service_account")
    if info:
        return service_account.Credentials.from_service_account_info(dict(info), scopes=SCOPES)
    raw = os.environ.get("FORUSIGHT_GCP_SA_JSON")
    if raw:
        return service_account.Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
    return None


def validar_identificador(nombre: str) -> str:
    """Proyectos/datasets/tablas no se pueden parametrizar: se validan antes de formatear."""
    if not nombre or not _IDENT.match(nombre):
        raise ValueError(f"Identificador BigQuery inválido: {nombre!r}")
    return nombre


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


class BigQueryClient:
    """Envoltorio delgado sobre google.cloud.bigquery.Client."""

    def __init__(
        self,
        settings: AppSettings | None = None,
        credentials=None,
        client=None,
        max_bytes_billed: int | None = 50 * 1024**3,
    ) -> None:
        self.settings = settings or cargar_settings()
        self._credentials = credentials
        self._client = client
        self.max_bytes_billed = max_bytes_billed

    @property
    def client(self):
        if self._client is None:
            from google.cloud import bigquery

            creds = self._credentials if self._credentials is not None else resolver_credenciales()
            project = self.settings.gcp_project or getattr(creds, "project_id", None)
            self._client = bigquery.Client(
                project=project, credentials=creds, location=self.settings.bq_location
            )
        return self._client

    def tabla(self, dataset: str, nombre: str) -> str:
        proyecto = validar_identificador(self.settings.gcp_project or self.client.project)
        return f"`{proyecto}.{validar_identificador(dataset)}.{validar_identificador(nombre)}`"

    def query_df(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
        labels: Mapping[str, str] | None = None,
    ) -> pd.DataFrame:
        from google.cloud import bigquery

        cfg = bigquery.QueryJobConfig(
            query_parameters=a_query_parameters(params),
            maximum_bytes_billed=self.max_bytes_billed,
            labels={"app": "forusight", **(labels or {})},
            use_query_cache=True,
        )
        return self.client.query(sql, job_config=cfg).to_dataframe(create_bqstorage_client=True)

    def load_df(
        self, df: pd.DataFrame, tabla_id: str, write_disposition: str = "WRITE_APPEND"
    ) -> Any:
        """Load job (no streaming inserts): atómico y sin costo de inserción."""
        from google.cloud import bigquery

        cfg = bigquery.LoadJobConfig(write_disposition=write_disposition)
        job = self.client.load_table_from_dataframe(df, tabla_id.strip("`"), job_config=cfg)
        return job.result()
