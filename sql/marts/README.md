# sql/marts — carga del dataset MART

1. `00_ddl_mart.sql`: crea las tablas destino (propias de Forusight), con partición por
   semana/fecha y clustering por tienda y SKU.
2. `10_*.sql` … `50_*.sql` (**pendientes**): scheduled queries `MERGE` desde las tablas
   fuente de Forus hacia cada `mart_*`. Se escriben **sólo** cuando se entregue el esquema
   real (salida de la consulta INFORMATION_SCHEMA del README). Ninguna tabla o columna
   fuente se asume antes de eso.

Frecuencia prevista: diaria, antes de la corrida de la app (PENDIENTE (h)).
