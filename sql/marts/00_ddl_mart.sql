-- =====================================================================================
-- DDL del dataset MART de Forusight (tablas PROPIAS; no son tablas fuente de Forus).
-- Columnas = contratos canónicos de src/forusight/data/schemas.py.
-- Reemplazar ${PROYECTO} y ${DATASET_MART} antes de ejecutar (PENDIENTE (n)).
-- =====================================================================================

CREATE SCHEMA IF NOT EXISTS `${PROYECTO}.${DATASET_MART}`
OPTIONS (location = 'US', description = 'Forusight: agregados precalculados (scheduled queries)');

-- Venta semanal tienda×SKU con días con stock (16 semanas móviles; se retiene más historia).
CREATE TABLE IF NOT EXISTS `${PROYECTO}.${DATASET_MART}.mart_venta_semanal` (
  semana_inicio   DATE    NOT NULL OPTIONS (description = 'Lunes de la semana'),
  tienda_id       STRING  NOT NULL,
  sku             STRING  NOT NULL,
  unidades        FLOAT64 NOT NULL OPTIONS (description = 'Venta neta de devoluciones (PENDIENTE b)'),
  dias_con_stock  INT64   NOT NULL OPTIONS (description = '0..7 días con stock > 0 al cierre'),
  actualizado_en  TIMESTAMP
)
PARTITION BY semana_inicio
CLUSTER BY tienda_id, sku
OPTIONS (require_partition_filter = TRUE, partition_expiration_days = 400);

-- Foto de stock en tienda + tránsito hacia la tienda.
CREATE TABLE IF NOT EXISTS `${PROYECTO}.${DATASET_MART}.mart_stock_tienda` (
  fecha_foto        DATE    NOT NULL,
  tienda_id         STRING  NOT NULL,
  sku               STRING  NOT NULL,
  stock_disponible  FLOAT64 NOT NULL,
  stock_transito    FLOAT64 NOT NULL
)
PARTITION BY fecha_foto
CLUSTER BY tienda_id, sku
OPTIONS (partition_expiration_days = 60);

-- Foto de stock del CD (físico, reservado, comprometido).
CREATE TABLE IF NOT EXISTS `${PROYECTO}.${DATASET_MART}.mart_stock_cd` (
  fecha_foto    DATE    NOT NULL,
  cd_id         STRING  NOT NULL,
  sku           STRING  NOT NULL,
  fisico        FLOAT64 NOT NULL,
  reservado     FLOAT64 NOT NULL,
  comprometido  FLOAT64 NOT NULL
)
PARTITION BY fecha_foto
CLUSTER BY cd_id, sku
OPTIONS (partition_expiration_days = 60);

CREATE TABLE IF NOT EXISTS `${PROYECTO}.${DATASET_MART}.mart_dim_producto` (
  sku              STRING  NOT NULL,
  modelo_id        STRING  NOT NULL,
  modelo_color_id  STRING  NOT NULL,
  color            STRING,
  talla            STRING  NOT NULL,
  talla_orden      FLOAT64 NOT NULL,
  categoria        STRING  NOT NULL,
  genero           STRING  NOT NULL,
  rango_precio     STRING  NOT NULL
)
CLUSTER BY modelo_color_id, sku;

CREATE TABLE IF NOT EXISTS `${PROYECTO}.${DATASET_MART}.mart_dim_tienda` (
  tienda_id              STRING  NOT NULL,
  nombre                 STRING,
  cluster                STRING,
  formato                STRING,
  importancia_comercial  FLOAT64 NOT NULL,
  activa                 BOOL    NOT NULL,
  max_unidades_corrida   FLOAT64
);
