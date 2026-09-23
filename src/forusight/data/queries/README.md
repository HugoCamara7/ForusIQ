# Consultas de lectura (MART → contratos canónicos)

Cada archivo devuelve **exactamente** las columnas del contrato pandera
correspondiente en `data/schemas.py`, leyendo del dataset MART de Forusight.

- Placeholders `{mart}` / `{app}`: se reemplazan por `proyecto.dataset` validados
  (los identificadores no se pueden parametrizar en BigQuery).
- Filtros: siempre con parámetros de consulta (`@fecha_desde`, `@fecha_corte`, `@cd_id`),
  nunca por interpolación. Las tablas de venta filtran por la columna de partición.
- Columnas explícitas, nunca `SELECT *`.

Las tablas `mart_*` son **propias de Forusight** (se crean con `sql/marts/`). El mapeo
desde las tablas fuente de Forus se escribirá en `sql/marts/*.sql` cuando se entregue
el esquema real (INFORMATION_SCHEMA).
