"""Configuración de la app (entorno/secrets) y parámetros del motor (params.yaml).

- ``AppSettings``: conexión y comportamiento de la app. Se lee de variables de entorno
  con prefijo ``FORUSIGHT_`` (o de ``st.secrets['forusight']`` vía ``data.bq_client``).
- ``EngineParams``: todos los parámetros de negocio. Fuente: ``params.yaml``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_PARAMS_PATH = Path(__file__).with_name("params.yaml")


class AppSettings(BaseSettings):
    """Ajustes de infraestructura. Nunca contiene credenciales."""

    model_config = SettingsConfigDict(env_prefix="FORUSIGHT_", extra="ignore")

    data_source: Literal["bigquery", "mart", "synthetic"] = "synthetic"
    gcp_project: str | None = None
    #: Región de los jobs. Vacío = BigQuery la infiere de las tablas (igual que Catálogo).
    bq_location: str | None = None
    dataset_mart: str = "forusight_mart"
    #: Dataset de BigQuery para guardar aprobaciones. Vacío = no se usa BigQuery (GitHub o descarga).
    dataset_app: str | None = None
    cd_id: str = "320"
    cache_ttl_seconds: int = 3600
    params_path: Path = DEFAULT_PARAMS_PATH
    #: Marcas a distribuir (valor de la marca en ARTI, en mayúsculas). PENDIENTE confirmar.
    marcas: list[str] = Field(default_factory=lambda: ["AZALEIA"])
    #: Códigos de tienda/bodega que nunca reciben (bodegas eComm, outlets…). PENDIENTE (j).
    tiendas_excluidas: list[str] = Field(default_factory=list)
    #: Semanas de historia a leer (12 de análisis + 1 de margen).
    semanas_historia: int = 13
    #: Reemplaza la matriz marca × cadena de config/cadenas.yaml (p. ej. para sumar AZALEIA).
    marcas_por_cadena: dict[str, list[str]] | None = None
    #: Proyecto/dataset opcional para guardar aprobaciones en GitHub (ver github_store).
    github_repository: str | None = None
    github_token: str | None = None
    github_branch: str | None = None


# --------------------------------------------------------------------------- params


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HorizonteParams(_Section):
    semanas_analisis: int = Field(12, ge=4, le=16)
    semanas_bloque_reciente: int = Field(4, ge=1, le=8)


class DisponibilidadParams(_Section):
    dias_min_exposicion: int = Field(14, ge=1)
    quiebre_fraccion_core: float = Field(0.5, gt=0, le=1)


class DemandaParams(_Section):
    #: Peso de la venta propia de la talla en su pronóstico (0 = sólo modelo × curva).
    peso_venta_propia_talla: float = Field(0.5, ge=0, le=1)
    exposicion_minima: float = Field(0.3, gt=0, le=1)
    tope_correccion: float = Field(2.0, ge=1)
    pesos_bloques: list[float] = Field(default_factory=lambda: [0.5, 0.3, 0.2])
    volumen_min_pesos: float = Field(8, ge=0)
    umbral_tendencia: float = Field(0.15, ge=0)
    tendencia_min: float = Field(0.85, gt=0)
    tendencia_max: float = Field(1.2, gt=0)
    aplicar_factor_tendencia: bool = True
    factor_sin_historia: float = Field(0.7, ge=0, le=1)
    indice_categoria_min: float = Field(0.5, gt=0)
    indice_categoria_max: float = Field(1.5, gt=0)

    @field_validator("pesos_bloques")
    @classmethod
    def _tres_pesos(cls, v: list[float]) -> list[float]:
        if len(v) != 3 or any(p < 0 for p in v) or sum(v) <= 0:
            raise ValueError("pesos_bloques debe tener 3 pesos no negativos (4S, 5-8S, 9-12S)")
        return v

    @model_validator(mode="after")
    def _rangos(self) -> DemandaParams:
        if self.tendencia_min > 1 or self.tendencia_max < 1:
            raise ValueError("tendencia_min <= 1 <= tendencia_max")
        if self.indice_categoria_min > self.indice_categoria_max:
            raise ValueError("indice_categoria_min <= indice_categoria_max")
        return self


class SimilitudParams(_Section):
    k_vecinos: int = Field(5, ge=1)


class CurvaParams(_Section):
    k_prior: float = Field(10, ge=0)
    n_min_nivel_prior: float = Field(20, ge=0)


class SeguridadSemanas(_Section):
    alta: float = Field(1.0, ge=0)
    media: float = Field(0.75, ge=0)
    baja: float = Field(0.5, ge=0)


class CoberturaCategoria(_Section):
    lead_time_semanas: float | None = Field(None, ge=0)
    ciclo_revision_semanas: float | None = Field(None, ge=0)
    seguridad_semanas: dict[Literal["alta", "media", "baja"], float] | None = None
    umbral_sobrestock_semanas: float | None = Field(None, gt=0)


class CoberturaParams(_Section):
    lead_time_semanas: float = Field(1.0, ge=0)
    ciclo_revision_semanas: float = Field(1.0, ge=0)
    seguridad_semanas: SeguridadSemanas = Field(default_factory=SeguridadSemanas)
    #: z del stock de seguridad por talla (0 = sin nivel por talla, sólo curva del MC).
    seguridad_z_talla: float = Field(0.5, ge=0, le=3)
    #: Redondeo del nivel máximo por talla: "arriba" (siempre sube) o "cercano" (al entero).
    redondeo_nivel: str = Field("cercano", pattern="^(arriba|cercano)$")
    #: Multiplica la demanda de la talla al fijar el nivel (calibración contra el reporte).
    factor_demanda_nivel: float = Field(1.0, gt=0, le=5)
    umbral_sobrestock_semanas: float = Field(12.0, gt=0)
    por_categoria: dict[str, CoberturaCategoria] = Field(default_factory=dict)

    def para(self, categoria: str, rotacion: str) -> tuple[float, float]:
        """(cobertura_semanas, umbral_sobrestock) para una categoría y rotación."""
        ov = self.por_categoria.get(categoria)
        lt = self.lead_time_semanas
        cr = self.ciclo_revision_semanas
        seg = getattr(self.seguridad_semanas, rotacion)
        umbral = self.umbral_sobrestock_semanas
        if ov is not None:
            lt = ov.lead_time_semanas if ov.lead_time_semanas is not None else lt
            cr = ov.ciclo_revision_semanas if ov.ciclo_revision_semanas is not None else cr
            if ov.seguridad_semanas and rotacion in ov.seguridad_semanas:
                seg = ov.seguridad_semanas[rotacion]
            if ov.umbral_sobrestock_semanas is not None:
                umbral = ov.umbral_sobrestock_semanas
        return lt + cr + seg, umbral


class RotacionParams(_Section):
    percentil_alta: float = Field(0.67, gt=0, lt=1)
    percentil_baja: float = Field(0.33, gt=0, lt=1)

    @model_validator(mode="after")
    def _orden(self) -> RotacionParams:
        if self.percentil_baja >= self.percentil_alta:
            raise ValueError("percentil_baja < percentil_alta")
        return self


class ExhibicionParams(_Section):
    minimo_por_talla_core: int = Field(1, ge=0)
    #: Reposición: cada talla de un modelo-color que la tienda vende mantiene este mínimo
    #: (reponer lo vendido: si una talla se vendió y quedó en 0, vuelve 1).
    minimo_por_talla_activa: int = Field(1, ge=0)
    #: Modelo agotado en la tienda (0 en todas las tallas) sin venta en 4 semanas: no se repone.
    no_reponer_modelo_agotado_sin_venta_reciente: bool = True
    #: Completar la curva como el reporte de distribución: una talla que la tienda nunca tuvo
    #: ni vendió, de un modelo-color que la tienda vende, recibe su mínimo (1).
    completar_curva: bool = True
    minimo_por_categoria: dict[str, int] = Field(default_factory=dict)
    tallas_core_default: list[str] = Field(default_factory=list)
    tallas_core_por_genero: dict[str, list[str]] = Field(default_factory=dict)
    tallas_core_por_categoria: dict[str, list[str]] = Field(default_factory=dict)
    tallas_minimas_sin_core: int = Field(3, ge=1)

    def tallas_core(self, categoria: str, genero: str) -> set[str]:
        """Resolución: categoría → género → default."""
        if categoria in self.tallas_core_por_categoria:
            return {str(t) for t in self.tallas_core_por_categoria[categoria]}
        if genero in self.tallas_core_por_genero:
            return {str(t) for t in self.tallas_core_por_genero[genero]}
        return {str(t) for t in self.tallas_core_default}

    def minimo(self, categoria: str) -> int:
        return int(self.minimo_por_categoria.get(categoria, self.minimo_por_talla_core))


class PrioridadTiendasParams(_Section):
    """Tiendas prioritarias (p. ej. Jockey): más cobertura y primeras cuando el CD no alcanza."""

    patrones: list[str] = Field(default_factory=lambda: ["JOCKEY"])
    factor_cobertura: float = Field(1.25, ge=1.0, le=4.0)
    importancia: float = Field(1.0, ge=0, le=1.0)
    #: Liquidadoras (outlets): sin introducciones, menos cobertura y últimas en el reparto.
    cadenas_liquidadoras: list[str] = Field(default_factory=lambda: ["DH", "SE", "FB"])
    factor_cobertura_liquidadora: float = Field(0.75, gt=0, le=1.0)
    importancia_liquidadora: float = Field(0.0, ge=0, le=1.0)
    #: Prioridad por venta de config/prioridad_tiendas.csv (A, B, C).
    niveles: dict[str, NivelPrioridad] = Field(
        default_factory=lambda: {
            "A": NivelPrioridad(factor_cobertura=1.25, importancia=1.0),
            "B": NivelPrioridad(factor_cobertura=1.0),
            "C": NivelPrioridad(factor_cobertura=0.9, importancia=0.25),
        }
    )


class CalendarioParams(_Section):
    """Días de reposición por tienda (config/calendario_tiendas.csv)."""

    aplicar: bool = True
    leadtime_dias_defecto: float = Field(3.0, ge=0, le=30)
    revision_dias_defecto: float = Field(2.3, gt=0, le=30)


class NivelPrioridad(_Section):
    factor_cobertura: float = Field(1.0, gt=0, le=4.0)
    importancia: float | None = Field(None, ge=0, le=1.0)  # None = según su venta


class StockCdParams(_Section):
    """Qué parte del stock del CD 320 se reparte a tiendas.

    ``disponible`` es la columna de stock_bi ya sin reservas; si la tabla no la trae se usa
    tiendas+bodega.
    """

    componentes: str = Field(
        "disponible",
        pattern="^(disponible|tiendas\\+bodega-reservas|tiendas\\+bodega|tiendas|bodega)$",
    )


class RecepcionParams(_Section):
    """Envíos aprobados que todavía no llegan a la tienda (no están en el corte de stock)."""

    dias_pendiente: int = Field(3, ge=0, le=14)
    #: El corte del CD en BigQuery ya descuenta lo despachado (98 % de los SKU el 28→30/09):
    #: restar otra vez las aprobaciones lo contaba dos veces.
    descontar_del_cd: bool = True
    #: En Lima el despacho llega al día siguiente (0,4 % sigue en tránsito); en provincia no.
    transito_solo_provincia: bool = False
    #: stock_bi.transito es la salida de la tienda ORIGEN (traspasos), no lo que viene del CD:
    #: no se suma a la posición.
    usar_transito_bigquery: bool = True
    #: Tránsito desde las tablas de pedidos (pedidos_header_table + pedidos_detail_table):
    #: reemplaza al de stock_bi y al de las aprobaciones anteriores de Forusight.
    transito_pedidos: bool = True
    #: 1 Aprobado, 2 en Picking, 3 Documentado, 6 en Transporte, 7 Prerecepcionado
    #: (0 Creado aún no se aprueba; 4 Recepcionado ya está en el stock de la tienda).
    estados_transito: list[int] = Field(default_factory=lambda: [1, 2, 3, 6, 7])
    #: 1 Reposición, 2 Llenado de canal, 3 Traspaso tiendas (4 Devolución CD va al CD).
    clasificaciones_transito: list[int] = Field(default_factory=lambda: [1, 2, 3])
    #: El stock es el cierre de ayer y los pedidos están al minuto: lo recepcionado desde la
    #: fecha del corte todavía no está en el stock de la tienda, así que cuenta como tránsito.
    recepcionados_post_corte: bool = True
    #: Apagado: la corrida se hace una vez en la mañana y se reparte el disponible del CD tal
    #: como viene en el stock. Encendido (varias corridas al día), resta del CD lo aprobado /
    #: en picking y lo documentado desde el corte, para no repartir dos veces lo mismo.
    descontar_pedidos_del_cd: bool = False
    #: Sale del CD el pedido con origen = cd_id (320). Si la cabecera no trae el origen, se usan
    #: estas clasificaciones: 1 Reposición, 2 Llenado de canal.
    clasificaciones_desde_cd: list[int] = Field(default_factory=lambda: [1, 2])


class ReposicionVentaParams(_Section):
    """Reponer lo vendido desde la ruta anterior del mall (venta diaria de BigQuery)."""

    reponer_venta_desde_ruta: bool = True
    #: Restar de la posición la venta posterior al corte de stock. El reporte de distribución
    #: no lo hace (posición = físico + tránsito): apagado para cuadrar con el reporte.
    descontar_venta_post_corte: bool = False
    dias_venta_diaria: int = Field(14, ge=7, le=31)
    #: Como el reporte: lo vendido se repone sólo si la talla llegó a su punto de reorden
    #: (el reporte nunca envía con posición > punto de reorden; 7 reportes, 12.106 u).
    solo_bajo_punto_reorden: bool = False


class ReferenciaParams(_Section):
    """Niveles del último reporte de distribución de cada tienda (Neogística)."""

    #: Usar el Nivel Máximo y el Punto de Reorden del reporte cuando la tienda×SKU está en él.
    usar_nivel_del_reporte: bool = True
    #: Reponer además lo vendido desde la ruta anterior en las filas con nivel del reporte (el
    #: reporte no lo hace: apagado para cuadrar con él).
    reponer_venta_con_nivel_del_reporte: bool = False
    #: Antigüedad máxima del reporte usado como referencia.
    dias_maximos: int = Field(10, ge=1, le=60)


class SurtidoParams(_Section):
    """Qué se puede reponer: temporadas comerciales activas y modelos bloqueados por tienda."""

    #: Sólo se reponen estas temporadas comerciales (vacío = todas). La temporada sale del
    #: maestro de temporadas (reportes de Neogística y de bloqueos), no de ARTI.
    temporadas_reponer: list[str] = Field(default_factory=lambda: ["INVIERNO 2026", "VERANO 2026"])
    #: Usar la temporada de ARTI cuando el modelo-color no está en el maestro de temporadas
    #: (ARTI no trae la temporada comercial: apagado).
    temporadas_en_bigquery: bool = False


class TopeTiendaParams(_Section):
    max_unidades_por_sku: int = Field(12, ge=0)
    max_unidades_por_tienda: int = Field(600, ge=0)


class PesosAfinidad(_Section):
    a1_historia_propia: float = Field(0.35, ge=0)
    a2_indice_tienda: float = Field(0.20, ge=0)
    a3_modelos_similares: float = Field(0.20, ge=0)
    a4_tiendas_similares: float = Field(0.15, ge=0)
    a5_presencia_historica: float = Field(0.10, ge=0)


class AfinidadParams(_Section):
    #: Introducir modelos que la tienda nunca tuvo. Apagado: como en el reporte de
    #: distribución, los modelos nuevos entran por carga manual ("Carga Pedidos").
    introducir_modelos_nuevos: bool = False
    umbral_introduccion: float = Field(0.45, ge=0, le=1)
    penalizacion_sin_venta: float = Field(0.2, ge=0, le=1)
    pesos: PesosAfinidad = Field(default_factory=PesosAfinidad)


class PesosPrioridad(_Section):
    riesgo_quiebre: float = Field(0.30, ge=0)
    velocidad: float = Field(0.20, ge=0)
    afinidad: float = Field(0.10, ge=0)
    tendencia: float = Field(0.05, ge=0)
    importancia_comercial: float = Field(0.25, ge=0)
    bono_curva_rota: float = Field(0.10, ge=0)


class AsignacionParams(_Section):
    multiplo_envio: int = Field(1, ge=1)
    pesos_prioridad: PesosPrioridad = Field(default_factory=PesosPrioridad)


class NivelNeoParams(_Section):
    """Nivel máximo y punto de reorden con la estructura del reporte (engine/nivel_neo.py)."""

    activo: bool = False  # params.yaml lo activa
    z: float = Field(0.84, ge=0, le=3)
    #: La clave de un reporte reciente no baja del nivel que tenía en ese reporte.
    nivel_reporte_como_piso: bool = False
    #: Antigüedad máxima del maestro de planificación (SMT/nivel por tienda × SKU).
    dias_maximos: int = Field(60, ge=1, le=365)
    #: Qué tienda × talla se evalúa: "venta" (stock/tránsito/venta de la talla y venta reciente
    #: del modelo en la tienda), "listada_reciente" (+ tallas de un reporte reciente si el modelo
    #: vendió) o "listada" (+ toda talla de un reporte reciente).
    universo: Literal["venta", "listada_reciente", "listada"] = "listada_reciente"
    #: Distancia nivel − punto de reorden de la clave en su último reporte (si no, 1).
    banda_reporte: bool = False
    #: Clave de un reporte de hace <= N días: nivel y reorden del reporte tal cual (0 = nunca).
    dias_nivel_reporte: int = Field(3, ge=0, le=30)


class EngineParams(_Section):
    """Contrato de parámetros del motor (versionable; se guarda con cada corrida)."""

    horizonte: HorizonteParams = Field(default_factory=HorizonteParams)
    disponibilidad: DisponibilidadParams = Field(default_factory=DisponibilidadParams)
    demanda: DemandaParams = Field(default_factory=DemandaParams)
    similitud: SimilitudParams = Field(default_factory=SimilitudParams)
    curva: CurvaParams = Field(default_factory=CurvaParams)
    cobertura: CoberturaParams = Field(default_factory=CoberturaParams)
    rotacion: RotacionParams = Field(default_factory=RotacionParams)
    exhibicion: ExhibicionParams = Field(default_factory=ExhibicionParams)
    tope_tienda: TopeTiendaParams = Field(default_factory=TopeTiendaParams)
    afinidad: AfinidadParams = Field(default_factory=AfinidadParams)
    asignacion: AsignacionParams = Field(default_factory=AsignacionParams)
    prioridad_tiendas: PrioridadTiendasParams = Field(default_factory=PrioridadTiendasParams)
    recepcion: RecepcionParams = Field(default_factory=RecepcionParams)
    calendario: CalendarioParams = Field(default_factory=CalendarioParams)
    stock_cd: StockCdParams = Field(default_factory=StockCdParams)
    reposicion_venta: ReposicionVentaParams = Field(default_factory=ReposicionVentaParams)
    surtido: SurtidoParams = Field(default_factory=SurtidoParams)
    referencia: ReferenciaParams = Field(default_factory=ReferenciaParams)
    nivel_neo: NivelNeoParams = Field(default_factory=NivelNeoParams)

    @model_validator(mode="after")
    def _bloques(self) -> EngineParams:
        h = self.horizonte
        if 2 * h.semanas_bloque_reciente >= h.semanas_analisis:
            raise ValueError("semanas_analisis debe superar 2 x semanas_bloque_reciente")
        return self


def load_params(path: str | Path | None = None) -> EngineParams:
    p = Path(path) if path else DEFAULT_PARAMS_PATH
    with p.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return EngineParams.model_validate(raw)


def dump_params(params: EngineParams) -> str:
    return yaml.safe_dump(
        params.model_dump(mode="json"), allow_unicode=True, sort_keys=False, default_flow_style=None
    )


def save_params(params: EngineParams, path: str | Path | None = None) -> Path:
    p = Path(path) if path else DEFAULT_PARAMS_PATH
    p.write_text(dump_params(params), encoding="utf-8")
    return p
