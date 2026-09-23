-- =====================================================================================
-- DDL del dataset APP de Forusight (única escritura de la app, vía load jobs).
-- Reemplazar ${PROYECTO} y ${DATASET_APP} antes de ejecutar (PENDIENTE (n)).
-- =====================================================================================

CREATE SCHEMA IF NOT EXISTS `${PROYECTO}.${DATASET_APP}`
OPTIONS (location = 'US', description = 'Forusight: corridas, propuestas, aprobaciones, auditoría');

CREATE TABLE IF NOT EXISTS `${PROYECTO}.${DATASET_APP}.corridas` (
  run_id               STRING NOT NULL,
  creado_en            TIMESTAMP NOT NULL,
  usuario              STRING,
  fecha_corte          DATE NOT NULL,
  cd_id                STRING NOT NULL,
  estado               STRING NOT NULL,  -- PROPUESTA | APROBADA | EXPORTADA
  unidades_propuestas  INT64,
  parametros_json      STRING           -- snapshot completo de params.yaml
)
PARTITION BY DATE(creado_en);

CREATE TABLE IF NOT EXISTS `${PROYECTO}.${DATASET_APP}.distribucion_propuesta` (
  run_id STRING NOT NULL, creado_en TIMESTAMP NOT NULL,
  tienda_id STRING NOT NULL, modelo_color_id STRING NOT NULL, sku STRING NOT NULL, talla STRING,
  estado_mc STRING, estado_sku STRING,
  stock_tienda FLOAT64, stock_transito FLOAT64, venta_4s FLOAT64, venta_12s FLOAT64,
  demanda_semanal FLOAT64, stock_objetivo INT64, necesidad INT64, stock_cd_disponible FLOAT64,
  cantidad INT64, afinidad FLOAT64, motivo_codigo STRING, motivo_texto STRING,
  modelo_id STRING, color STRING, categoria STRING, genero STRING, rango_precio STRING,
  talla_orden FLOAT64, cluster STRING, es_core BOOL, share_talla FLOAT64, prior_talla FLOAT64,
  nivel_prior STRING, demanda_mc FLOAT64, fuente_demanda STRING, factor_tendencia FLOAT64,
  rotacion STRING, cobertura_semanas FLOAT64, cobertura_actual FLOAT64, sobrestock BOOL,
  curva_rota BOOL, talla_core_faltante BOOL, es_introduccion BOOL, necesidad_bruta INT64,
  etapa INT64, prioridad_inicial FLOAT64, motivo_parcial STRING
)
PARTITION BY DATE(creado_en)
CLUSTER BY run_id, tienda_id, sku;

CREATE TABLE IF NOT EXISTS `${PROYECTO}.${DATASET_APP}.distribucion_aprobada` (
  run_id STRING NOT NULL, tienda_id STRING NOT NULL, sku STRING NOT NULL,
  cantidad_propuesta INT64, cantidad_aprobada INT64 NOT NULL, comentario STRING,
  aprobado_por STRING, aprobado_en TIMESTAMP NOT NULL
)
PARTITION BY DATE(aprobado_en)
CLUSTER BY run_id, tienda_id, sku;

CREATE TABLE IF NOT EXISTS `${PROYECTO}.${DATASET_APP}.auditoria` (
  run_id STRING, evento_en TIMESTAMP NOT NULL, usuario STRING, accion STRING NOT NULL,
  detalle_json STRING
)
PARTITION BY DATE(evento_en);
