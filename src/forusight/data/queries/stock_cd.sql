-- Contrato: StockCDSchema. Sólo el CD configurado (320).
SELECT
  sku,
  fisico,
  reservado,
  comprometido
FROM {mart}.mart_stock_cd
WHERE cd_id = @cd_id
  AND fecha_foto = (SELECT MAX(fecha_foto) FROM {mart}.mart_stock_cd WHERE cd_id = @cd_id AND fecha_foto <= @fecha_corte)
