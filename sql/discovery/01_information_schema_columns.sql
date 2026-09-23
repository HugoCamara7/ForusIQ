-- =====================================================================================
-- Descubrimiento del esquema fuente de Forus (sólo lectura, sin costo de escaneo de datos).
-- Reemplazar:
--   PROYECTO_FUENTE  → proyecto GCP donde están las tablas de Forus
--   region-us        → región de los datasets (p. ej. region-us, region-southamerica-west1)
--   lista de datasets en el WHERE (o quitar el filtro para ver todos)
-- Exportar el resultado a CSV/Sheets y compartirlo.
-- =====================================================================================
SELECT
  c.table_schema                              AS dataset,
  c.table_name                                AS tabla,
  t.table_type                                AS tipo_tabla,
  c.ordinal_position                          AS posicion,
  f.field_path                                AS columna,   -- incluye campos anidados (STRUCT/ARRAY)
  f.data_type                                 AS tipo_dato,
  c.is_nullable                               AS admite_nulos,
  c.is_partitioning_column                    AS es_particion,
  c.clustering_ordinal_position               AS orden_cluster,
  f.description                               AS descripcion
FROM `PROYECTO_FUENTE.region-us.INFORMATION_SCHEMA.COLUMNS` AS c
JOIN `PROYECTO_FUENTE.region-us.INFORMATION_SCHEMA.TABLES` AS t
  USING (table_catalog, table_schema, table_name)
JOIN `PROYECTO_FUENTE.region-us.INFORMATION_SCHEMA.COLUMN_FIELD_PATHS` AS f
  ON  f.table_catalog = c.table_catalog
  AND f.table_schema  = c.table_schema
  AND f.table_name    = c.table_name
  AND f.column_name   = c.column_name
WHERE c.table_schema IN ('DATASET_VENTAS', 'DATASET_STOCK', 'DATASET_MAESTROS')  -- ajustar
ORDER BY dataset, tabla, posicion, columna;
