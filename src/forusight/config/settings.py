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


class RecepcionParams(_Section):
    """Envíos aprobados que todavía no llegan a la tienda (no están en el corte de stock)."""

    dias_pendiente: int = Field(3, ge=0, le=14)


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
