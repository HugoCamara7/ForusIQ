-- Contrato: VentaSemanalSchema. Partición: semana_inicio. Cluster: tienda_id, sku.
SELECT
  semana_inicio,
  tienda_id,
  sku,
  unidades,
  dias_con_stock
FROM {mart}.mart_venta_semanal
WHERE semana_inicio >= @fecha_desde
  AND semana_inicio < @fecha_corte
