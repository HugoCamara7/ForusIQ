-- Contrato: DimTiendaSchema.
SELECT
  tienda_id,
  nombre,
  cluster,
  formato,
  importancia_comercial,
  activa,
  max_unidades_corrida
FROM {mart}.mart_dim_tienda
