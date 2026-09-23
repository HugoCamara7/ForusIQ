-- Contrato: StockTiendaSchema. Foto más reciente (stock en mano + tránsito).
SELECT
  tienda_id,
  sku,
  stock_disponible,
  stock_transito
FROM {mart}.mart_stock_tienda
WHERE fecha_foto = (SELECT MAX(fecha_foto) FROM {mart}.mart_stock_tienda WHERE fecha_foto <= @fecha_corte)
