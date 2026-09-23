-- Volumetría por tabla (filas y tamaño). TABLE_STORAGE es una vista a nivel de región.
SELECT
  table_schema                                   AS dataset,
  table_name                                     AS tabla,
  total_rows                                     AS filas,
  ROUND(total_logical_bytes / POW(1024, 3), 2)   AS gb_logicos,
  creation_time                                  AS creada_en
FROM `PROYECTO_FUENTE.region-us.INFORMATION_SCHEMA.TABLE_STORAGE`
WHERE table_schema IN ('DATASET_VENTAS', 'DATASET_STOCK', 'DATASET_MAESTROS')  -- ajustar
  AND NOT deleted
ORDER BY filas DESC;

-- Particiones (la vista PARTITIONS sólo existe por dataset: repetir por cada uno).
-- SELECT table_name, COUNT(*) AS particiones, MIN(partition_id) AS primera, MAX(partition_id) AS ultima,
--        SUM(total_rows) AS filas
-- FROM `PROYECTO_FUENTE.DATASET_VENTAS.INFORMATION_SCHEMA.PARTITIONS`
-- WHERE partition_id NOT IN ('__NULL__', '__UNPARTITIONED__')
-- GROUP BY table_name ORDER BY filas DESC;
