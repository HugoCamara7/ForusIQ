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
from forusight.data import corridas as C
from forusight.data.bq_client import BigQueryClient, validar_identificador
from forusight.engine.pipeline import EngineInputs, EngineResult

CONSULTAS = {
    "ventas": "ventas_semanales.sql",
    "stock_tienda": "stock_tienda.sql",
    "stock_cd": "stock_cd.sql",
    "dim_producto": "dim_producto.sql",
    "dim_tienda": "dim_tienda.sql",
}


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


class BigQueryRepository:
    """Lee el dataset MART propio (una consulta por contrato); escribe APP con load jobs."""

    TABLAS_APP = {
        "corridas": "corridas",
        "propuesta": "distribucion_propuesta",
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


#: Días máximos entre la última venta y el corte de stock para que el sugerido tenga sentido.
DIAS_MAX_SIN_VENTA = 7


def corte_por_venta(ventas: pd.DataFrame, foto: dt.date, diag) -> dt.date | None:
    """Si la tabla de ventas está atrasada respecto al stock, devuelve el lunes de la semana
    de la última venta: la ventana de 12 semanas termina ahí (semanas completas con venta)
    en vez de contar como 0 las semanas que la tabla todavía no trae."""
    if "ultima_venta" not in ventas or ventas.empty:
        return None
    ultima = pd.to_datetime(ventas["ultima_venta"]).max()
    if pd.isna(ultima):
        return None
    diag.venta_hasta = ultima.date().isoformat()
    if ultima.date() >= foto - dt.timedelta(days=DIAS_MAX_SIN_VENTA):
        return None
    return (ultima - pd.Timedelta(days=ultima.weekday())).date()


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
            "pedidos": tabla_configurada("pedidos", self.secrets),
            "pedidos_detalle": tabla_configurada("pedidos_detalle", self.secrets),
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
                if fuente in ("tiendas", "cadena", "pedidos", "pedidos_detalle"):
                    continue  # opcionales: se informa en el diagnóstico
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
            fuente_venta=tablas["ventas"],
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
                "marca en Inicio (se listan las que existen en ARTI)."
            )
        cortes = q("cortes", F.sql_cortes(tablas["stock"], m_s))
        # Último corte = cierre de ayer de este año (el del año pasado queda fuera).
        foto = pd.to_datetime(cortes["fecha_corte"]).max().date() if len(cortes) else None
        if foto is None or foto < pd.Timestamp(fecha_corte).date() - dt.timedelta(days=21):
            raise ValueError(
                "La tabla de stock no tiene un corte de este año cerca de la semana elegida"
                + (f" (el último es {foto:%d/%m/%Y})." if foto else ".")
            )
        diag.fecha_foto = foto.isoformat()
        stock = q(
            "stock_foto",
            F.sql_stock_foto(tablas["stock"], m_s, tablas["arti"], m_a, con_marcas),
            {"fecha_foto": foto},
        )
        sql_v = F.sql_ventas(tablas["ventas"], m_v, tablas["arti"], m_a, con_marcas)
        ventas = q("ventas", sql_v)
        corte_v = corte_por_venta(ventas, foto, diag)
        if corte_v is not None:  # venta atrasada: 12 semanas completas hasta la última venta
            fecha_corte = pd.Timestamp(corte_v)
            base.update(F.ventana(fecha_corte, semanas))
            base["hasta_foto"] = corte_v
            diag.corte_venta = corte_v.isoformat()
            ventas = q("ventas", sql_v)

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
            marcas_por_cadena=self.settings.marcas_por_cadena,
        )
        # Nombre del modelo: si ARTI no trae descripción, se toma de la venta (columna modelo).
        dp = entradas.dim_producto
        if (
            "nombre_modelo" in m_v
            and "id_producto" in m_v
            and dp["descripcion"].isna().mean() > 0.2
        ):
            try:
                nm = q(
                    "nombres_modelo",
                    F.sql_nombres_modelo(tablas["ventas"], m_v, tablas["arti"], m_a, con_marcas),
                )
                nombres = pd.Series(
                    F.texto(nm["nombre_modelo"]).str.upper().to_numpy(),
                    index=F.sku_canonico(nm["id_producto"]).to_numpy(),
                )
                nombres = nombres[~nombres.index.duplicated()]
                por_sku = dp["sku"].map(nombres)
                # un nombre por modelo-color: completa las tallas que no vendieron
                por_mc = por_sku.groupby(dp["modelo_color_id"]).transform("first")
                dp["descripcion"] = dp["descripcion"].fillna(por_sku).fillna(por_mc)
            except Exception as exc:
                diag.notas.append(f"No se pudo leer el nombre del modelo: {explicar_error(exc)}")
        # Venta diaria reciente: reponer lo vendido desde la ruta anterior de cada mall.
        if "id_producto" in m_v:
            try:
                dias = 14
                vd = q(
                    "venta_diaria",
                    F.sql_venta_diaria(tablas["ventas"], m_v, tablas["arti"], m_a, con_marcas),
                    {"desde_diaria": foto - dt.timedelta(days=dias)},
                )
                entradas.venta_diaria = F.a_venta_diaria(
                    vd,
                    set(entradas.dim_producto["sku"]),
                    excl | {F.codigo_tienda(self.settings.cd_id)},
                )
            except Exception as exc:
                diag.notas.append(f"No se pudo leer la venta diaria: {explicar_error(exc)}")
        # Pedidos (cabecera + detalle): el tránsito hacia cada tienda sale de aquí.
        if tablas["pedidos"] or tablas["pedidos_detalle"]:
            entradas.pedidos = self._leer_pedidos(
                q, tablas, mapas, m_a, con_marcas, foto, entradas, excl, diag
            )
        else:
            diag.notas.append(
                "Tránsito en 0: faltan `pedidos_header_table` y `pedidos_detail_table` en "
                "[bigquery] (el tránsito sale de los pedidos del sistema)."
            )
        diag.gb_leidos = round(self.client.gb_leidos, 3)
        self.ultimo_diagnostico = diag
        return entradas

    def _leer_pedidos(self, q, tablas, mapas, m_a, con_marcas, foto, entradas, excl, diag):
        """Pedidos por tienda destino × SKU × estado × clasificación (None si no se pudo)."""
        from forusight.data import fuentes as F
        from forusight.data import mapeo
        from forusight.data.bq_client import explicar_error

        faltan = [f for f in ("pedidos", "pedidos_detalle") if f not in mapas]
        if faltan:
            diag.notas.append(
                "Tránsito: no se pudo leer "
                + " ni ".join(f"`{tablas[f] or f}`" for f in faltan)
                + " (revisa `pedidos_header_table` y `pedidos_detail_table`)."
            )
            return None
        m_h, m_d = mapas["pedidos"][0], mapas["pedidos_detalle"][0]
        for fuente, mapa in (("pedidos", m_h), ("pedidos_detalle", m_d)):
            falta = mapeo.faltantes(fuente, mapa)
            if falta:
                diag.notas.append(
                    f"Tránsito: mapeo incompleto de {tablas[fuente]} (falta "
                    f"{', '.join(falta)}). Corrígelo en la página Conexión."
                )
                return None
        try:
            crudo = q(
                "pedidos",
                F.sql_pedidos(
                    tablas["pedidos"],
                    m_h,
                    tablas["pedidos_detalle"],
                    m_d,
                    tablas["arti"],
                    m_a,
                    con_marcas,
                ),
                {"fecha_foto": foto},
            )
        except Exception as exc:
            diag.notas.append(f"Tránsito: no se pudieron leer los pedidos: {explicar_error(exc)}")
            return None
        pedidos = F.a_pedidos(
            crudo, set(entradas.dim_producto["sku"]), excl | {F.codigo_tienda(self.settings.cd_id)}
        )
        if "clasificacion" not in m_h:
            diag.notas.append(
                "Tránsito: la cabecera de pedidos no tiene clasificación mapeada; se cuentan "
                "todas las clasificaciones."
            )
        diag.filas["pedidos_tienda_sku"] = len(pedidos)
        return pedidos

    def revisar_venta(self, marcas: list[str]) -> tuple[dict, pd.DataFrame]:
        """Última fecha de venta de la tabla y de la marca, y filas posteriores (si hay)."""
        from forusight.data import fuentes as F

        tablas = self.tablas()
        mapas = self.mapeos(tablas)
        if "ventas" not in mapas or "arti" not in mapas:
            raise ValueError("Falta el mapeo de ventas o de ARTI (revísalo arriba).")
        m_v, m_a = mapas["ventas"][0], mapas["arti"][0]
        resumen, muestra = F.sql_revisar_venta(tablas["ventas"], m_v, tablas["arti"], m_a)
        marcas = [m.strip().upper() for m in marcas if str(m).strip()]
        params = params_usados(resumen, {"marcas": marcas})
        r = self.client.query_df(resumen, params, labels={"consulta": "revisar_venta"})
        info = r.iloc[0].to_dict() if len(r) else {}
        filas = pd.DataFrame()
        if info.get("ultima_marca") is not None and not pd.isna(info.get("ultima_marca")):
            filas = self.client.query_df(
                muestra,
                {"ultima_marca": pd.Timestamp(info["ultima_marca"]).date()},
                labels={"consulta": "revisar_venta_muestra"},
            )
        return info, filas

    def ultimas_cargas(self, hoy: dt.date | None = None) -> dict[str, str | None]:
        """Última fecha cargada del stock (fecha_corte) y de la venta: la carga diaria terminó
        cuando las dos llegan a ayer. Lectura barata (sólo la columna de fecha, 10 días)."""
        from forusight.data import fuentes as F

        hoy = hoy or C.hoy_lima().date()
        tablas = self.tablas()
        mapas = self.mapeos({k: tablas[k] for k in ("stock", "ventas") if tablas[k]})
        out: dict[str, str | None] = {"stock": None, "ventas": None}
        for k in out:
            if k not in mapas or "fecha" not in mapas[k][0]:
                continue
            df = self.client.query_df(
                F.sql_ultima_fecha(tablas[k], mapas[k][0]),
                {"desde_carga": hoy - dt.timedelta(days=10), "hasta_carga": hoy},
                labels={"consulta": f"ultima_carga_{k}"},
            )
            if len(df) and pd.notna(df["ultima"].iloc[0]):
                out[k] = pd.Timestamp(df["ultima"].iloc[0]).date().isoformat()
        return out

    def revisar_transito(
        self, tiendas, recepcion, fecha_foto: dt.date | None = None
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """(líneas de pedido hacia ``tiendas`` con su regla, pedidos por local destino).

        Cada línea dice si cuenta como tránsito con las reglas de ``recepcion`` (estados y
        clasificaciones; todo el historial) y cuántas veces aparece en las tablas (``filas`` > 1: repetida).
        """
        from forusight.data import fuentes as F
        from forusight.data import mapeo

        tablas = self.tablas()
        mapas = self.mapeos({k: tablas[k] for k in ("pedidos", "pedidos_detalle")})
        for fuente in ("pedidos", "pedidos_detalle"):
            if fuente not in mapas:
                raise ValueError(f"No se pudo leer la tabla de {fuente} ({tablas[fuente]}).")
            falta = mapeo.faltantes(fuente, mapas[fuente][0])
            if falta:
                raise ValueError(f"Mapeo de {fuente} incompleto: falta {', '.join(falta)}.")
        m_h, m_d = mapas["pedidos"][0], mapas["pedidos_detalle"][0]
        hoy = fecha_foto or C.hoy_lima().date()
        lineas = self.client.query_df(
            F.sql_revisar_transito(tablas["pedidos"], m_h, tablas["pedidos_detalle"], m_d),
            {
                "tiendas": [
                    F.codigo_tienda(t)
                    for t in ([tiendas] if isinstance(tiendas, str) else list(tiendas))
                ],
            },
            labels={"consulta": "revisar_transito"},
        )
        locales = self.client.query_df(
            F.sql_locales_pedidos(tablas["pedidos"], m_h, tablas["pedidos_detalle"], m_d),
            {"desde_pedidos": hoy - dt.timedelta(days=3)},
            labels={"consulta": "locales_pedidos"},
        )
        if len(lineas):
            est = F.codigo_pedido(lineas["estado"], F._NOMBRES_ESTADO)
            cla = F.codigo_pedido(lineas["clasificacion"], F._NOMBRES_CLASIFICACION)
            rec = pd.to_datetime(lineas["fecha_recepcion"], errors="coerce")
            abierto = est.isin(recepcion.estados_transito).fillna(False)
            # 6/7 (en transporte, prerecepcionado): sólo si el pedido es reciente.
            ped = pd.to_datetime(lineas["fecha_pedido"], errors="coerce")
            dias = (pd.Timestamp(hoy) - ped).dt.days.fillna(0)
            abierto |= est.isin(recepcion.estados_transito_recientes).fillna(False) & (
                dias <= recepcion.dias_transito_recientes
            )
            clase_ok = cla.isin(recepcion.clasificaciones_transito).fillna(False) | cla.isna()
            # Ya recibido: con fecha de recepción o línea en estado 4 (está en el stock).
            lineas["recepcionado"] = F.recepcionado(
                lineas.assign(con_recepcion=rec.notna())
            ).to_numpy()
            abierto = abierto & ~lineas["recepcionado"]
            desp = lineas["cantidad_despachada"].where(lineas["cantidad_despachada"] > 0)
            lineas["unidades"] = desp.fillna(lineas["cantidad_pedida"]).fillna(0)
            lineas["cuenta_transito"] = abierto & clase_ok
            lineas["repetida"] = lineas["filas"] > 1
        cat = set(self._codigos_catalogo())
        if len(locales):
            locales["tienda_id"] = locales["tienda_cod"].map(F.codigo_tienda)
            locales["en_catalogo"] = locales["tienda_id"].isin(cat)
        return lineas, locales

    def _codigos_catalogo(self) -> list[str]:
        from forusight.data import cadenas as CAD

        return list(CAD.catalogo_tiendas()["codigo_tienda"].astype(str))

    def explorar_tabla(self, tabla: str, n: int = 20) -> tuple[pd.DataFrame, pd.DataFrame]:
        """(columnas con tipo, muestra de filas) de cualquier tabla, sin costo de consulta."""
        cols = self.client.columnas(tabla)
        try:
            muestra = self.client.muestra(tabla, n)
        except Exception:  # vistas: la API de lectura no las sirve
            muestra = pd.DataFrame()
        return cols, muestra

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
