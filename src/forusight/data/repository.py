"""Repositorios: de dónde salen las entradas del motor y dónde se guardan las corridas.

- ``SyntheticRepository``: datos sintéticos en memoria (demo, desarrollo, tests).
- ``BigQueryRepository``: lee MART (una consulta por contrato, por corrida) y escribe APP
  con load jobs.
"""

from __future__ import annotations

import datetime as dt
import json
from importlib import resources
from typing import Protocol

import pandas as pd

from forusight.config.settings import AppSettings, EngineParams, dump_params
from forusight.data.bq_client import BigQueryClient, validar_identificador
from forusight.engine.pipeline import EngineInputs, EngineResult

CONSULTAS = {
    "ventas": "ventas_semanales.sql",
    "stock_tienda": "stock_tienda.sql",
    "stock_cd": "stock_cd.sql",
    "dim_producto": "dim_producto.sql",
    "dim_tienda": "dim_tienda.sql",
}

COLUMNAS_APROBACION = [
    "run_id",
    "tienda_id",
    "sku",
    "cantidad_propuesta",
    "cantidad_aprobada",
    "comentario",
]


def leer_sql(nombre: str) -> str:
    return resources.files("forusight.data.queries").joinpath(nombre).read_text(encoding="utf-8")


class Repository(Protocol):
    def cargar_entradas(self, fecha_corte: pd.Timestamp, semanas: int = 16) -> EngineInputs: ...

    def guardar_corrida(self, result: EngineResult, usuario: str) -> None: ...

    def guardar_aprobacion(self, run_id: str, aprobacion: pd.DataFrame, usuario: str) -> None: ...


def _ahora() -> pd.Timestamp:
    return pd.Timestamp(dt.datetime.now(dt.UTC))


def tabla_corrida(result: EngineResult, usuario: str) -> pd.DataFrame:
    r = result.resumen
    return pd.DataFrame(
        [
            {
                "run_id": result.run_id,
                "creado_en": _ahora(),
                "usuario": usuario,
                "fecha_corte": result.fecha_corte.date(),
                "cd_id": r.get("cd_id"),
                "estado": "PROPUESTA",
                "unidades_propuestas": r.get("unidades_a_distribuir"),
                "parametros_json": json.dumps(
                    result.params.model_dump(mode="json"), ensure_ascii=False
                ),
            }
        ]
    )


def tabla_auditoria(run_id: str, usuario: str, accion: str, detalle: dict) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "run_id": run_id,
                "evento_en": _ahora(),
                "usuario": usuario,
                "accion": accion,
                "detalle_json": json.dumps(detalle, ensure_ascii=False, default=str),
            }
        ]
    )


class SyntheticRepository:
    """Repositorio en memoria sobre datos sintéticos."""

    def __init__(self, seed: int = 7) -> None:
        self.seed = seed
        self.corridas: list[pd.DataFrame] = []
        self.propuestas: dict[str, pd.DataFrame] = {}
        self.aprobaciones: dict[str, pd.DataFrame] = {}
        self.auditoria: list[pd.DataFrame] = []

    def cargar_entradas(
        self, fecha_corte: pd.Timestamp | None = None, semanas: int = 16
    ) -> EngineInputs:
        from forusight.data.synthetic import ConfigSintetica, generar

        cfg = ConfigSintetica(seed=self.seed, semanas=semanas)
        if fecha_corte is not None:
            cfg.fecha_corte = pd.Timestamp(fecha_corte).date().isoformat()
        inputs, _ = generar(cfg)
        return inputs

    def guardar_corrida(self, result: EngineResult, usuario: str) -> None:
        self.corridas.append(tabla_corrida(result, usuario))
        self.propuestas[result.run_id] = result.detalle.copy()
        self.auditoria.append(tabla_auditoria(result.run_id, usuario, "CORRIDA", result.resumen))

    def guardar_aprobacion(self, run_id: str, aprobacion: pd.DataFrame, usuario: str) -> None:
        validar_aprobacion(aprobacion)
        self.aprobaciones[run_id] = aprobacion.assign(aprobado_por=usuario, aprobado_en=_ahora())
        self.auditoria.append(
            tabla_auditoria(run_id, usuario, "APROBACION", {"filas": len(aprobacion)})
        )


def validar_aprobacion(df: pd.DataFrame) -> None:
    faltan = set(COLUMNAS_APROBACION) - set(df.columns)
    if faltan:
        raise ValueError(f"Aprobación sin columnas: {sorted(faltan)}")
    if (df["cantidad_aprobada"] < 0).any():
        raise ValueError("cantidad_aprobada no puede ser negativa")


class BigQueryRepository:
    """Una consulta por contrato y corrida; escritura en APP con load jobs."""

    TABLAS_APP = {
        "corridas": "corridas",
        "propuesta": "distribucion_propuesta",
        "aprobada": "distribucion_aprobada",
        "auditoria": "auditoria",
    }

    def __init__(
        self, client: BigQueryClient | None = None, settings: AppSettings | None = None
    ) -> None:
        self.client = client or BigQueryClient(settings)
        self.settings = self.client.settings

    def _dataset(self, nombre: str) -> str:
        proyecto = validar_identificador(self.settings.gcp_project or self.client.client.project)
        return f"`{proyecto}.{validar_identificador(nombre)}`"

    def _sql(self, archivo: str) -> str:
        return leer_sql(archivo).format(
            mart=self._dataset(self.settings.dataset_mart),
            app=self._dataset(self.settings.dataset_app),
        )

    def cargar_entradas(self, fecha_corte: pd.Timestamp, semanas: int = 16) -> EngineInputs:
        corte = pd.Timestamp(fecha_corte).date()
        params = {
            "fecha_corte": corte,
            "fecha_desde": corte - dt.timedelta(weeks=semanas),
            "cd_id": self.settings.cd_id,
        }
        datos = {}
        for nombre, archivo in CONSULTAS.items():
            sql = self._sql(archivo)
            usados = {k: v for k, v in params.items() if f"@{k}" in sql}
            datos[nombre] = self.client.query_df(sql, usados, labels={"consulta": nombre})
        return EngineInputs(**datos)

    def _tabla_app(self, clave: str) -> str:
        return self.client.tabla(self.settings.dataset_app, self.TABLAS_APP[clave])

    def guardar_corrida(self, result: EngineResult, usuario: str) -> None:
        self.client.load_df(tabla_corrida(result, usuario), self._tabla_app("corridas"))
        det = result.detalle.copy()
        det["creado_en"] = _ahora()
        self.client.load_df(det, self._tabla_app("propuesta"))
        self.client.load_df(
            tabla_auditoria(result.run_id, usuario, "CORRIDA", result.resumen),
            self._tabla_app("auditoria"),
        )

    def guardar_aprobacion(self, run_id: str, aprobacion: pd.DataFrame, usuario: str) -> None:
        validar_aprobacion(aprobacion)
        df = aprobacion[COLUMNAS_APROBACION].assign(aprobado_por=usuario, aprobado_en=_ahora())
        self.client.load_df(df, self._tabla_app("aprobada"))
        self.client.load_df(
            tabla_auditoria(
                run_id,
                usuario,
                "APROBACION",
                {"filas": len(df), "unidades": int(df["cantidad_aprobada"].sum())},
            ),
            self._tabla_app("auditoria"),
        )


def parametros_snapshot(params: EngineParams) -> str:
    """YAML de parámetros para guardar junto a la corrida."""
    return dump_params(params)
