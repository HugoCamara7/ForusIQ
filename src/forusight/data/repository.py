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

from forusight.config.settings import AppSettings
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
        marcas: list[str] | None = None,
    ) -> EngineInputs: ...

    def guardar_corrida(self, result: EngineResult, usuario: str) -> None: ...

    def guardar_aprobacion(self, run_id: str, aprobacion: pd.DataFrame, usuario: str) -> str: ...


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
        marcas: list[str] | None = None,
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

    def guardar_aprobacion(self, run_id: str, aprobacion: pd.DataFrame, usuario: str) -> str:
        validar_aprobacion(aprobacion)
        self.aprobaciones[run_id] = aprobacion.assign(aprobado_por=usuario, aprobado_en=_ahora())
        self.auditoria.append(
            tabla_auditoria(run_id, usuario, "APROBACION", {"filas": len(aprobacion)})
        )
        return "sesión (demo)"


class SinAlmacenamiento(RuntimeError):
    """No hay dataset ni GitHub configurados: la aprobación sólo puede descargarse."""


def guardar_aprobacion_github(run_id: str, df: pd.DataFrame, usuario: str, secrets=None) -> str:
    from forusight.data.bq_client import leer_st_secrets
    from forusight.data.github_store import GitHubStore, config_github

    cfg = config_github(leer_st_secrets() if secrets is None else secrets)
    if cfg is None:
        raise SinAlmacenamiento(
            "No hay dónde guardar la aprobación (sin dataset de BigQuery ni GitHub). Descárgala "
            "con el botón de abajo, o pega en los secrets el bloque [ticketing] de Catálogo "
            "Control Center para guardarla en GitHub."
        )
    store = GitHubStore(cfg["repository"], cfg["token"], cfg["branch"], cfg["prefix"])
    fecha = _ahora().strftime("%Y%m%d_%H%M%S")
    ruta = store.guardar(
        f"aprobaciones/{run_id}_{fecha}.csv",
        df.to_csv(index=False).encode("utf-8-sig"),
        f"forusight: aprobación {run_id} por {usuario}",
    )
    return f"GitHub ({cfg['repository']}, rama {cfg['branch']}: {ruta})"


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
            app=self._dataset(self.settings.dataset_app) if self.settings.dataset_app else "",
        )

    def cargar_entradas(
        self,
        fecha_corte: pd.Timestamp,
        semanas: int = 16,
        stock_cd_archivo: pd.DataFrame | None = None,
        marcas: list[str] | None = None,
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

    def guardar_aprobacion(self, run_id: str, aprobacion: pd.DataFrame, usuario: str) -> str:
        """Guarda la aprobación y devuelve dónde quedó.

        Destino: dataset de BigQuery si `[forusight] dataset_app` está configurado; si no,
        GitHub (bloque [ticketing] del Catálogo o [forusight] github_*); si no hay ninguno,
        lanza SinAlmacenamiento para que la UI ofrezca la descarga.
        """
        validar_aprobacion(aprobacion)
        df = aprobacion[COLUMNAS_APROBACION].assign(aprobado_por=usuario, aprobado_en=_ahora())
        if self.settings.dataset_app:
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
            return f"BigQuery ({self.settings.dataset_app})"
        return guardar_aprobacion_github(run_id, df, usuario, getattr(self, "secrets", None))


class FuentesRepository(BigQueryRepository):
    """Lee directo de las tablas de Forus con los secrets de Catálogo Control Center.

    - ARTI: `product_master_table` / `table` (por defecto stg_pe_central_arti).
    - Stock: `stock_table` (por defecto stg_pe_central_stock_bi).
    - Venta: `ventas_table`, opcional. Sin ella, o si no responde, se estima por consumo.
    Las columnas se descubren con INFORMATION_SCHEMA y se mapean por alias.
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
        self.aviso_ventas = ""
        self._arti_resuelta: str | None = None

    def tablas(self) -> dict[str, str | None]:
        from forusight.data import fuentes as F
        from forusight.data.bq_client import tabla_configurada

        return {
            "arti": self._tabla_arti(),
            "stock": tabla_configurada("stock", self.secrets, F.TABLA_STOCK),
            "ventas": tabla_configurada("ventas", self.secrets),
            "tiendas": tabla_configurada("tiendas", self.secrets),
            "cadena": tabla_configurada("cadena", self.secrets),
        }

    def _tabla_arti(self) -> str:
        """Primera candidata (`table`, `product_master_table`, por defecto) que tenga
        producto, modelo-color y talla. Se resuelve una vez y se recuerda."""
        from forusight.data import fuentes as F
        from forusight.data import mapeo
        from forusight.data.bq_client import tablas_candidatas

        if getattr(self, "_arti_resuelta", None):
            return self._arti_resuelta
        candidatas = tablas_candidatas("arti", self.secrets, F.TABLA_ARTI)
        descartes = []
        for tabla in candidatas:
            try:
                cols, _ = self.columnas(tabla)
            except Exception as exc:
                descartes.append(f"{tabla}: {type(exc).__name__}")
                continue
            mapa, _ = mapeo.resolver("arti", tabla, cols, self.secrets)
            faltan = mapeo.faltantes("arti", mapa)
            if not faltan:
                self._arti_resuelta = tabla
                return tabla
            descartes.append(f"{tabla}: no trae {', '.join(faltan)}")
        raise ValueError(
            "Ninguna tabla sirve como maestro de productos (ARTI). Probadas: "
            + "; ".join(descartes)
            + ". Pon la tabla ARTI en `table` dentro de "
            "[bigquery] (la misma que usa Catálogo)."
        )

    def columnas(self, tabla: str) -> tuple[list[str], str]:
        """(columnas, origen). Si INFORMATION_SCHEMA falla, usa el esquema conocido."""
        from forusight.data import fuentes as F

        try:
            cols = list(self.client.columnas(tabla)["column_name"])
            if cols:
                return cols, "information_schema"
        except Exception:
            if tabla not in F.COLUMNAS_CONOCIDAS:
                raise
        if tabla in F.COLUMNAS_CONOCIDAS:
            return list(F.COLUMNAS_CONOCIDAS[tabla]), "esquema_conocido"
        raise ValueError(
            f"INFORMATION_SCHEMA no devolvió columnas para {tabla}: revisa el "
            "nombre exacto (distingue mayúsculas) y el permiso de la cuenta."
        )

    def mapeos(self, tablas: dict | None = None) -> dict[str, tuple[dict, str, list]]:
        """fuente → (mapeo, origen, columnas). La venta se omite si no está configurada."""
        from forusight.data import mapeo

        tablas = tablas or self.tablas()
        out = {}
        self.aviso_ventas = ""
        for fuente, tabla in tablas.items():
            if not tabla:
                continue
            try:
                cols, _ = self.columnas(tabla)
            except Exception as exc:
                if fuente in ("tiendas", "cadena"):
                    continue  # maestros opcionales: se informa en el diagnóstico
                if fuente != "ventas":
                    raise
                # La venta es opcional: nunca bloquea la corrida.
                self.aviso_ventas = self._explicar_tabla_ventas(tabla, exc)
                continue
            mapa, origen = mapeo.resolver(fuente, tabla, cols, self.secrets)
            out[fuente] = (mapa, origen, cols)
        return out

    def _explicar_tabla_ventas(self, tabla: str, exc: Exception) -> str:
        from forusight.data.bq_client import explicar_error

        texto = (
            f"No se pudo leer `ventas_table` ({tabla}): se usa la venta estimada por "
            f"consumo de stock. {explicar_error(exc)}"
        )
        try:
            proyecto, dataset, _ = tabla.split(".")
            parecidas = self.client.tablas_del_dataset(proyecto, dataset, "vent")
            if parecidas:
                texto += f"\n\nTablas con 'vent' en `{proyecto}.{dataset}`: " + ", ".join(
                    parecidas[:15]
                )
        except Exception:
            pass
        return texto

    def marcas_disponibles(self) -> pd.DataFrame:
        """Marcas del maestro ARTI con su cantidad de SKU."""
        from forusight.data import fuentes as F

        tabla = self.tablas()["arti"]
        mapa, _, cols = self.mapeos({"arti": tabla})["arti"]
        if "marca" not in mapa:
            raise ValueError(
                f"ARTI ({tabla}) no tiene una columna de marca reconocible. "
                f"Columnas: {', '.join(map(str, cols[:30]))}"
            )
        df = self.client.query_df(F.sql_marcas(tabla, mapa), {}, labels={"consulta": "marcas"})
        df.attrs["columna_marca"] = mapa["marca"]
        return df

    def cargar_entradas(
        self,
        fecha_corte: pd.Timestamp,
        semanas: int | None = None,
        stock_cd_archivo: pd.DataFrame | None = None,
        marcas: list[str] | None = None,
    ) -> EngineInputs:
        from forusight.data import fuentes as F
        from forusight.data import mapeo
        from forusight.data.bq_client import explicar_error

        semanas = semanas or self.settings.semanas_historia
        tablas = self.tablas()
        if not tablas["ventas"]:
            raise ValueError(
                "Falta `ventas_table` en [bigquery]: la venta real es obligatoria "
                "(el stock sólo trae el último corte, no hay historial)."
            )
        mapas = self.mapeos(tablas)
        if "ventas" not in mapas:
            raise ValueError(self.aviso_ventas or f"No se pudo leer {tablas['ventas']}.")
        for fuente in ("arti", "stock", "ventas"):
            faltan = mapeo.faltantes(fuente, mapas[fuente][0])
            if faltan:
                raise ValueError(
                    f"Mapeo incompleto de {fuente} ({tablas[fuente]}): falta "
                    f"{', '.join(faltan)}. Corrígelo en la página Conexión."
                )
        m_a, m_s, m_v = mapas["arti"][0], mapas["stock"][0], mapas["ventas"][0]
        marcas = [
            m.strip().upper()
            for m in (marcas if marcas is not None else self.settings.marcas)
            if str(m).strip()
        ]
        con_marcas = bool(marcas) and "marca" in m_a
        base = {**F.ventana(fecha_corte, semanas), "marcas": marcas}
        diag = F.Diagnostico(
            mapeos={k: v[0] for k, v in mapas.items()},
            marcas=marcas,
            tablas={k: v for k, v in tablas.items() if v},
        )

        def q(nombre: str, sql: str, extra: dict | None = None) -> pd.DataFrame:
            params = params_usados(sql, {**base, **(extra or {})})
            df = self.client.query_df(sql, params, labels={"consulta": nombre})
            diag.filas[f"sql_{nombre}"] = len(df)
            return df

        arti = q("arti", F.sql_arti(tablas["arti"], m_a, con_marcas))
        if arti.empty:
            raise ValueError(
                f"ARTI no devolvió productos para las marcas {marcas}. Elige la "
                "marca en la barra lateral (se listan las que existen en ARTI)."
            )
        cortes = q("cortes", F.sql_cortes(tablas["stock"], m_s))
        if cortes.empty:
            raise ValueError("La tabla de stock no tiene cortes en las últimas 3 semanas.")
        foto = pd.to_datetime(cortes["fecha_corte"]).max().date()
        diag.fecha_foto = foto.isoformat()
        stock = q(
            "stock_foto",
            F.sql_stock_foto(tablas["stock"], m_s, tablas["arti"], m_a, con_marcas),
            {"fecha_foto": foto},
        )
        ventas = q("ventas", F.sql_ventas(tablas["ventas"], m_v, tablas["arti"], m_a, con_marcas))

        maestros = {}
        for fuente in ("tiendas", "cadena"):
            if fuente not in mapas:
                if tablas[fuente]:
                    diag.notas.append(f"No se pudo leer el maestro de {fuente} ({tablas[fuente]}).")
                continue
            mapa = mapas[fuente][0]
            faltan = mapeo.faltantes(fuente, mapa)
            if faltan:
                diag.notas.append(f"Maestro de {fuente} sin {', '.join(faltan)}: no se usa.")
                continue
            try:
                maestros[fuente] = q(f"maestro_{fuente}", F.sql_maestro(tablas[fuente], mapa))
            except Exception as exc:
                diag.notas.append(f"No se pudo leer el maestro de {fuente}: {explicar_error(exc)}")

        excl = {F.codigo_tienda(t) for t in self.settings.tiendas_excluidas}
        entradas = F.construir_entradas(
            arti,
            ventas,
            stock,
            self.settings.cd_id,
            excl,
            stock_cd_archivo,
            fecha_corte,
            diag,
            semanas=semanas,
            tiendas_m=maestros.get("tiendas"),
            cadena_m=maestros.get("cadena"),
        )
        diag.gb_leidos = round(self.client.gb_leidos, 3)
        self.ultimo_diagnostico = diag
        return entradas

    def buscar_tablas(self, patrones: tuple[str, ...] = ("venta", "vta", "sales")) -> pd.DataFrame:
        """Tablas del proyecto del datalake cuyo nombre sugiere venta (INFORMATION_SCHEMA)."""
        from forusight.data.bq_client import validar_identificador

        proyecto = validar_identificador(self.tablas()["stock"].split(".")[0])
        region = validar_identificador(f"region-{(self.settings.bq_location or 'us').lower()}")
        cond = " OR ".join(f"LOWER(table_name) LIKE @p{i}" for i in range(len(patrones)))
        sql = (
            f"SELECT table_schema AS dataset, table_name AS tabla, table_type AS tipo\n"
            f"FROM `{proyecto}.{region}.INFORMATION_SCHEMA.TABLES`\nWHERE {cond}\n"
            "ORDER BY dataset, tabla LIMIT 200"
        )
        params = {f"p{i}": f"%{p}%" for i, p in enumerate(patrones)}
        df = self.client.query_df(sql, params, labels={"consulta": "buscar_tablas"})
        df["ruta"] = proyecto + "." + df["dataset"] + "." + df["tabla"]
        return df
