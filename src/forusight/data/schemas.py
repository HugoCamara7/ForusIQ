"""Contratos canónicos internos (pandera).

El motor SOLO conoce estas columnas. El mapeo desde las tablas reales de Forus
se hace en ``data/queries/*.sql`` cuando se entregue el esquema; ninguna columna
de aquí presupone nombres de tablas fuente.

Llaves:
    tienda_id        código de tienda (texto)
    sku              modelo + color + talla (texto; codificación PENDIENTE (c))
    modelo_color_id  "modelo" a nivel de decisión (modelo-color)
    modelo_id        modelo sin color (para priors de curva y afinidad)
"""

from __future__ import annotations

import pandas as pd
import pandera.pandas as pa
from pandera.typing import Series

from forusight.config.settings import EngineParams

# El contrato de parámetros es el modelo pydantic EngineParams (params.yaml).
ParametrosContrato = EngineParams


class _Base(pa.DataFrameModel):
    class Config:
        coerce = True
        strict = False  # columnas extra se ignoran; el motor selecciona las suyas


# ------------------------------------------------------------------ entradas (MART)


class VentaSemanalSchema(_Base):
    """Venta semanal tienda×SKU (16 semanas) con días con stock.

    Una fila por (semana_inicio, tienda_id, sku). Una semana ausente equivale a
    0 unidades y 0 días con stock (sin exposición).
    """

    semana_inicio: Series[pd.Timestamp] = pa.Field(nullable=False)
    tienda_id: Series[str] = pa.Field(nullable=False)
    sku: Series[str] = pa.Field(nullable=False)
    unidades: Series[float] = pa.Field(nullable=False)  # venta neta (PENDIENTE (b))
    dias_con_stock: Series[int] = pa.Field(ge=0, le=7, nullable=False)

    class Config:
        unique = ["semana_inicio", "tienda_id", "sku"]


class StockTiendaSchema(_Base):
    """Stock actual y en tránsito hacia la tienda, por SKU."""

    tienda_id: Series[str] = pa.Field(nullable=False)
    sku: Series[str] = pa.Field(nullable=False)
    stock_disponible: Series[float] = pa.Field(ge=0, nullable=False)
    stock_transito: Series[float] = pa.Field(ge=0, nullable=False)

    class Config:
        unique = ["tienda_id", "sku"]


class StockCDSchema(_Base):
    """Stock del CD 320 por SKU. Disponible = físico − reservado − comprometido."""

    sku: Series[str] = pa.Field(nullable=False, unique=True)
    fisico: Series[float] = pa.Field(ge=0, nullable=False)
    reservado: Series[float] = pa.Field(ge=0, nullable=False)
    comprometido: Series[float] = pa.Field(ge=0, nullable=False)


class DimProductoSchema(_Base):
    """Dimensión producto. Un SKU existe sólo si el modelo fabrica esa talla."""

    sku: Series[str] = pa.Field(nullable=False, unique=True)
    modelo_id: Series[str] = pa.Field(nullable=False)
    modelo_color_id: Series[str] = pa.Field(nullable=False)
    color: Series[str] = pa.Field(nullable=True)
    talla: Series[str] = pa.Field(nullable=False)
    talla_orden: Series[float] = pa.Field(nullable=False)  # orden numérico de la talla
    categoria: Series[str] = pa.Field(nullable=False)
    genero: Series[str] = pa.Field(nullable=False)
    rango_precio: Series[str] = pa.Field(nullable=False)

    @pa.dataframe_check(error="modelo_color_id×talla debe ser único")
    @classmethod
    def _talla_unica(cls, df: pd.DataFrame) -> bool:
        return not df.duplicated(["modelo_color_id", "talla"]).any()


class DimTiendaSchema(_Base):
    """Dimensión tienda."""

    tienda_id: Series[str] = pa.Field(nullable=False, unique=True)
    nombre: Series[str] = pa.Field(nullable=True)
    cluster: Series[str] = pa.Field(nullable=True)  # PENDIENTE (f)
    formato: Series[str] = pa.Field(nullable=True)
    importancia_comercial: Series[float] = pa.Field(ge=0, le=1, nullable=False)
    activa: Series[bool] = pa.Field(nullable=False)
    max_unidades_corrida: Series[float] = pa.Field(ge=0, nullable=True)


# ------------------------------------------------------------------ salida del motor


ESTADOS = ["NUNCA_TUVO", "EXPOSICION_INSUFICIENTE", "TUVO_SIN_VENTA", "TUVO_Y_VENDE", "QUIEBRE"]


class DistribucionSchema(_Base):
    """Fila de salida: CD → Tienda → Modelo/SKU → Talla → Cantidad, con motivo."""

    run_id: Series[str]
    tienda_id: Series[str]
    modelo_color_id: Series[str]
    sku: Series[str]
    talla: Series[str]
    estado_mc: Series[str] = pa.Field(isin=ESTADOS)
    estado_sku: Series[str] = pa.Field(isin=ESTADOS)
    stock_tienda: Series[float] = pa.Field(ge=0)
    stock_transito: Series[float] = pa.Field(ge=0)
    venta_4s: Series[float] = pa.Field(ge=0)
    venta_12s: Series[float] = pa.Field(ge=0)
    demanda_semanal: Series[float] = pa.Field(ge=0)
    stock_objetivo: Series[int] = pa.Field(ge=0)
    necesidad: Series[int] = pa.Field(ge=0)
    stock_cd_disponible: Series[float] = pa.Field(ge=0)
    cantidad: Series[int] = pa.Field(ge=0)
    afinidad: Series[float] = pa.Field(ge=0, le=1)
    motivo_codigo: Series[str]
    motivo_texto: Series[str]

    @pa.dataframe_check(error="cantidad <= necesidad")
    @classmethod
    def _cantidad_le_necesidad(cls, df: pd.DataFrame) -> Series[bool]:
        return df["cantidad"] <= df["necesidad"]


ENTRADAS: dict[str, type[_Base]] = {
    "ventas": VentaSemanalSchema,
    "stock_tienda": StockTiendaSchema,
    "stock_cd": StockCDSchema,
    "dim_producto": DimProductoSchema,
    "dim_tienda": DimTiendaSchema,
}


def validar(nombre: str, df: pd.DataFrame) -> pd.DataFrame:
    """Valida y coerciona un DataFrame de entrada según su contrato."""
    return ENTRADAS[nombre].validate(df.reset_index(drop=True), lazy=True)
