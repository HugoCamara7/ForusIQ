import io
import json

import pandas as pd
import pytest

from forusight.config.settings import AppSettings
from forusight.data import bq_client
from forusight.data.repository import (
    CONSULTAS,
    BigQueryRepository,
    SyntheticRepository,
    leer_sql,
)
from forusight.export.excel import a_csv, a_excel, columnas_display


def test_settings_desde_secrets_tienen_prioridad(monkeypatch):
    monkeypatch.setenv("FORUSIGHT_GCP_PROJECT", "desde-env")
    s = bq_client.cargar_settings({"forusight": {"gcp_project": "desde-secrets"}})
    assert s.gcp_project == "desde-secrets"
    assert bq_client.cargar_settings({}).gcp_project == "desde-env"


def test_credenciales_none_usa_adc(monkeypatch):
    monkeypatch.delenv("FORUSIGHT_GCP_SA_JSON", raising=False)
    assert bq_client.resolver_credenciales({}) is None


def test_credenciales_desde_entorno(monkeypatch):
    llamado = {}

    def falso(info, scopes):
        llamado["info"] = info
        return "CREDS"

    from google.oauth2 import service_account

    monkeypatch.setattr(service_account.Credentials, "from_service_account_info", falso)
    monkeypatch.setenv("FORUSIGHT_GCP_SA_JSON", json.dumps({"client_email": "x@y"}))
    assert bq_client.resolver_credenciales({}) == "CREDS"
    assert llamado["info"]["client_email"] == "x@y"
    # secrets tiene prioridad sobre el entorno
    assert bq_client.resolver_credenciales({"gcp_service_account": {"a": 1}}) == "CREDS"
    assert llamado["info"] == {"a": 1}


@pytest.mark.parametrize("malo", ["", "a.b", "x;DROP", "a b", "`x`"])
def test_identificadores_invalidos(malo):
    with pytest.raises(ValueError):
        bq_client.validar_identificador(malo)


def test_parametros_de_consulta_tipados():
    import datetime as dt

    ps = bq_client.a_query_parameters(
        {"d": dt.date(2026, 1, 5), "s": "320", "n": 3, "l": ["a", "b"]}
    )
    tipos = {p.name: getattr(p, "type_", None) or getattr(p, "array_type", None) for p in ps}
    assert tipos == {"d": "DATE", "s": "STRING", "n": "INT64", "l": "STRING"}


def test_sql_columnas_explicitas_y_filtro_de_particion():
    for archivo in CONSULTAS.values():
        sql = leer_sql(archivo)
        assert "SELECT *" not in sql.upper()
    assert "@fecha_desde" in leer_sql("ventas_semanales.sql")
    assert "@cd_id" in leer_sql("stock_cd.sql")


class _FakeClient:
    def __init__(self):
        self.settings = AppSettings(gcp_project="proj", dataset_mart="mart", dataset_app="app")
        self.consultas = []
        self.cargas = []

    def query_df(self, sql, params=None, labels=None):
        self.consultas.append((sql, params))
        return pd.DataFrame()

    def tabla(self, dataset, nombre):
        return f"`proj.{dataset}.{nombre}`"

    def load_df(self, df, tabla):
        self.cargas.append((tabla, len(df)))


def test_bigquery_repository_una_consulta_por_contrato_y_load_jobs(resultado_sintetico):
    fake = _FakeClient()
    repo = BigQueryRepository(client=fake)
    repo.cargar_entradas(pd.Timestamp("2026-09-21"))
    assert len(fake.consultas) == len(CONSULTAS)
    sql_ventas, p = fake.consultas[0]
    assert "`proj.mart`.mart_venta_semanal" in sql_ventas
    assert set(p) == {"fecha_desde", "fecha_corte"}
    repo.guardar_corrida(resultado_sintetico, "analista")
    assert [t for t, _ in fake.cargas] == [
        "`proj.app.corridas`",
        "`proj.app.distribucion_propuesta`",
        "`proj.app.auditoria`",
    ]


def test_repositorio_sintetico_y_export(resultado_sintetico):
    repo = SyntheticRepository()
    repo.guardar_corrida(resultado_sintetico, "u")
    d = resultado_sintetico.propuesta
    aprob = pd.DataFrame(
        {
            "run_id": d["run_id"],
            "tienda_id": d["tienda_id"],
            "sku": d["sku"],
            "cantidad_propuesta": d["cantidad"],
            "cantidad_aprobada": d["cantidad"],
            "comentario": "",
        }
    )
    repo.guardar_aprobacion("RUN-TEST", aprob, "u")
    assert "RUN-TEST" in repo.aprobaciones

    xls = a_excel(resultado_sintetico.detalle, resumen=resultado_sintetico.resumen)
    hojas = pd.read_excel(io.BytesIO(xls), sheet_name=None)
    assert list(hojas) == ["Distribucion", "No enviados", "Resumen"]
    assert list(hojas["Distribucion"].columns) == list(columnas_display().values())
    assert "Stock CD 320" in hojas["Distribucion"].columns
    assert hojas["Distribucion"]["Cantidad a distribuir"].sum() == d["cantidad"].sum()
    assert a_csv(resultado_sintetico.detalle).startswith("﻿Tienda".encode())
