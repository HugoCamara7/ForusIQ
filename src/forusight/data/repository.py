"""Repositorios: de dónde salen las entradas del motor y dónde se guardan las corridas.

- ``SyntheticRepository``: datos sintéticos en memoria (demo, desarrollo, tests).
- ``BigQueryRepository``: lee MART (una consulta por contrato, por corrida) y escribe APP
  con load jobs.
"""

from __future__ import annotations

import datetime as dt
import json
import re
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


def params_usados(sql: str, params: dict) -> dict:
    """Sólo los parámetros que la consulta referencia (@hasta no confunde a @hasta_foto)."""
    return {k: v for k, v in params.items() if re.search(rf"@{k}\b", sql)}


def leer_sql(nombre: str) -> str:
    return resources.files("forusight.data.queries").joinpath(nombre).read_text(encoding="utf-8")


class Repository(Protocol):
    def cargar_entradas(
        self,
        fecha_corte: pd.Timestamp,
        semanas: int = 16,
        stock_cd_archivo: pd.DataFrame | None = None,
    ) -> EngineInputs: ...

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
        self,
        fecha_corte: pd.Timestamp | None = None,
        semanas: int = 16,
        stock_cd_archivo: pd.DataFrame | None = None,
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
    """Lee el dataset MART propio (una consulta por contrato); escribe APP con load jobs."""

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

    def cargar_entradas(
        self,
        fecha_corte: pd.Timestamp,
        semanas: int = 16,
        stock_cd_archivo: pd.DataFrame | None = None,
    ) -> EngineInputs:
        corte = pd.Timestamp(fecha_corte).date()
        params = {
            "fecha_corte": corte,
            "fecha_desde": corte - dt.timedelta(weeks=semanas),
            "cd_id": self.settings.cd_id,
        }
        datos = {}
        for nombre, archivo in CONSULTAS.items():
            sql = self._sql(archivo)
            usados = params_usados(sql, params)
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


class FuentesRepository(BigQueryRepository):
    """Lee directo de las tablas fuente de Forus configuradas en [bigquery] de los secrets.

    Mismo esquema de secrets que Catálogo/Repo Control Center: `ventas_table`,
    `product_master_table` (ARTI) y `stock_table`. Las columnas se descubren con
    INFORMATION_SCHEMA y se mapean por alias (``data/mapeo.py``).
    """

    def __init__(
        self,
        client: BigQueryClient | None = None,
        settings: AppSettings | None = None,
        secrets=None,
    ) -> None:
        from forusight.data.bq_client import leer_st_secrets

        self.secrets = leer_st_secrets() if secrets is None else secrets
        super().__init__(
            client=client or BigQueryClient(settings, secrets=self.secrets), settings=settings
        )
        self.ultimo_diagnostico = None

    def tablas(self) -> dict[str, str]:
        from forusight.data.bq_client import tabla_configurada

        return {n: tabla_configurada(n, self.secrets) for n in ("ventas", "arti", "stock")}

    def mapeos(self, tablas: dict[str, str] | None = None) -> dict[str, tuple[dict, str, list]]:
        """fuente → (mapeo, origen, columnas de la tabla)."""
        from forusight.data import mapeo

        tablas = tablas or self.tablas()
        out = {}
        for fuente, tabla in tablas.items():
            cols = list(self.client.columnas(tabla)["column_name"])
            if not cols:
                raise ValueError(
                    f"INFORMATION_SCHEMA no devolvió columnas para {tabla}: revisa "
                    "el nombre exacto (distingue mayúsculas) y el permiso."
                )
            mapa, origen = mapeo.resolver(fuente, tabla, cols, self.secrets)
            out[fuente] = (mapa, origen, cols)
        return out

    def cargar_entradas(
        self,
        fecha_corte: pd.Timestamp,
        semanas: int | None = None,
        stock_cd_archivo: pd.DataFrame | None = None,
    ) -> EngineInputs:
        from forusight.data import fuentes as F
        from forusight.data import mapeo

        semanas = semanas or self.settings.semanas_historia
        tablas = self.tablas()
        mapas = self.mapeos(tablas)
        for fuente, (mapa, _, _) in mapas.items():
            faltan = mapeo.faltantes(fuente, mapa)
            if faltan:
                raise ValueError(
                    f"Mapeo incompleto de {fuente} ({tablas[fuente]}): falta "
                    f"{', '.join(faltan)}. Corrígelo en la página Conexión."
                )
        m_v, m_a, m_s = (mapas[k][0] for k in ("ventas", "arti", "stock"))
        marcas = [m.strip().upper() for m in self.settings.marcas if m.strip()]
        con_marcas = bool(marcas)
        base = {**F.ventana(fecha_corte, semanas), "marcas": marcas}
        diag = F.Diagnostico(mapeos={k: v[0] for k, v in mapas.items()})

        def q(nombre: str, sql: str, extra: dict | None = None) -> pd.DataFrame:
            params = params_usados(sql, {**base, **(extra or {})})
            df = self.client.query_df(sql, params, labels={"consulta": nombre})
            diag.filas[f"sql_{nombre}"] = len(df)
            return df

        arti = q("arti", F.sql_arti(tablas["arti"], m_a, con_marcas))
        if arti.empty:
            raise ValueError(
                f"ARTI no devolvió productos para las marcas {marcas}: revisa el "
                "valor exacto de la marca en `[forusight] marcas`."
            )
        ventas = q("ventas", F.sql_ventas(tablas["ventas"], m_v, tablas["arti"], m_a, con_marcas))
        cortes = q("cortes", F.sql_cortes(tablas["stock"], m_s))
        if cortes.empty:
            raise ValueError("La tabla de stock no tiene fotos en la ventana de análisis.")
        foto = pd.Timestamp(cortes["fecha_corte"].max()).date()
        diag.fecha_foto, diag.cortes_en_ventana = foto.isoformat(), int(len(cortes))
        dias = q(
            "dias_con_stock",
            F.sql_dias_con_stock(tablas["stock"], m_s, tablas["arti"], m_a, con_marcas),
        )
        stock = q(
            "stock_foto",
            F.sql_stock_foto(tablas["stock"], m_s, tablas["arti"], m_a, con_marcas),
            {"fecha_foto": foto},
        )
        excl = {F.codigo_tienda(t) for t in self.settings.tiendas_excluidas}
        entradas = F.construir_entradas(
            arti,
            ventas,
            dias,
            cortes,
            stock,
            self.settings.cd_id,
            excl,
            stock_cd_archivo,
            fecha_corte,
            diag,
        )
        diag.gb_leidos = round(self.client.gb_leidos, 3)
        self.ultimo_diagnostico = diag
        return entradas
