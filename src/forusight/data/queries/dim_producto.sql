-- Contrato: DimProductoSchema.
SELECT
  sku,
  modelo_id,
  modelo_color_id,
  color,
  talla,
  talla_orden,
  categoria,
  genero,
  rango_precio
FROM {mart}.mart_dim_producto
