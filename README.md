# Forusight

Reposición y distribución de productos **Azaleia (Forus)** desde el **CD 320** (operado por
Neogistica) hacia las tiendas. La decisión de qué distribuir es de Forus y la toma esta app.

**Salida:** CD 320 → Tienda → Modelo/SKU → Talla → Cantidad, con el motivo explicado.

| Tienda | Modelo | SKU | Talla | Stock tienda | Venta 4S | Venta 12S | Demanda estimada | Stock objetivo | Necesidad | Stock CD 320 | Cantidad a distribuir | Motivo |
|---|---|---|---|---|---|---|---|---|---|---|---|---|

> Estado: **Fase 0 + Fase 1 (motor) + Fase 2 inicial (login y conexión a las tablas de
> Forus)**, con el mismo esquema de secrets que Catálogo Control Center y Repo Control Center.
> Ver [Información pendiente](#información-pendiente).

---

## Arquitectura

```
Tablas fuente Forus (solo lectura: venta, ARTI, stock_bi)
        │  fuente "bigquery": lectura directa agregada (data/fuentes.py)   ← hoy
        │  fuente "mart": scheduled queries a un MART propio (sql/marts)  ← optimización futura
        ▼
Dataset MART  ── mart_venta_semanal (partición semana, cluster tienda+sku), mart_stock_tienda,
        │        mart_stock_cd, mart_dim_producto, mart_dim_tienda
        │  1 lectura por corrida (st.cache_data + TTL), consultas parametrizadas
        ▼
Motor Python puro (src/forusight/engine) ── contratos pandera (data/schemas.py)
        │
        ▼
UI Streamlit ── filtros en memoria (st.fragment), edición (st.data_editor)
        │  load jobs al confirmar
        ▼
Dataset APP  ── corridas, distribucion_propuesta, distribucion_aprobada, auditoria
```

## Estructura

```
streamlit_app.py                 entrada Streamlit (st.navigation)
app/vistas/                       1_Dashboard … 5_Parametros, 6_Conexion (admin)
app/components/                  login, estado/caché, filtros, kpis, tablas, panel de motivo
src/forusight/config/            settings.py (pydantic) + params.yaml
src/forusight/auth.py            usuarios y roles desde [app_auth]
src/forusight/data/              bq_client.py, mapeo.py, fuentes.py, repository.py, schemas.py,
                                 synthetic.py, queries/*.sql
src/forusight/engine/            universe, availability, similarity, demand, size_curve,
                                 affinity, target, allocation, reasons, pipeline
src/forusight/export/            Excel/CSV
sql/discovery/                   consultas INFORMATION_SCHEMA para relevar el esquema fuente
sql/marts/                       DDL MART (tablas propias) + cargas (pendientes)
sql/app/                         DDL APP
tests/                           pytest (motor, datos, UI smoke)
```

## Instalación

Requiere Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Ejecutar

```bash
streamlit run streamlit_app.py
```

Sin configuración, la app arranca en **modo demo** (datos sintéticos, sin login). Con
`[app_auth]` pide correo y contraseña. En la barra lateral: fuente de datos, fecha de corte,
archivo STOCK CD opcional y **Ejecutar corrida**.

## Tests y lint

```bash
pytest                 # toda la suite
pytest tests/test_allocation.py -k cd      # p. ej. sólo "nunca asignar más que el CD"
ruff check . && ruff format --check .
```

La CI (`.github/workflows/ci.yml`) corre ruff + pytest en cada push y PR.

## Login

Mismo esquema que Catálogo/Repo Control Center (`[app_auth]` con `username`/`password` o
`[app_auth.users]`), comparación en tiempo constante (`hmac`) y **sin usuarios en el código**.
Roles opcionales en `[app_auth.roles]`:

| Rol | Puede |
|---|---|
| `admin` | todo: ejecutar, aprobar, guardar parámetros, página **Conexión** |
| `aprobador` | ejecutar, revisar y confirmar aprobaciones |
| `analista` (por defecto) | ejecutar, revisar y exportar la propuesta |

Sin `[app_auth]` la app sólo abre en **modo demo** (datos sintéticos); con BigQuery exige login.

## Conexión a las tablas de Forus

Fuente `bigquery`: lee directo de las tablas configuradas en `[bigquery]` (las mismas de los
otros Control Center) y las lleva a los contratos canónicos (`data/fuentes.py`):

| Clave en `[bigquery]` | Tabla (ejemplo) | Alimenta |
|---|---|---|
| `product_master_table` (o `table`) | `…bronze.stg_pe_central_arti` (ARTI) | dimensión producto: `CODINT_MA` = SKU, `CODMOD_MA`-`CODCOL_MA` = modelo-color, `TALNUM_MA`, `MARCA_MA`, `GENERO_MA` |
| `stock_table` | `…bronze.stg_pe_central_stock_bi` | última foto → stock por tienda y stock del CD 320; historial por `fecha_corte` → **días con stock** por semana |
| `ventas_table` | tabla de venta (la de Repo Control Center) | venta semanal por tienda × SKU, agregada en el servidor |
| *(archivo)* STOCK CD.xlsx | subido en la barra lateral | disponible y reservas del CD (mismo archivo que Repo Control Center) |

- **Columnas**: no se asumen. Se leen con `INFORMATION_SCHEMA` (gratis) y se emparejan por
  alias (algoritmo de Repo Control Center). La página **Conexión** (sólo admin) muestra y
  permite corregir el mapeo y genera el bloque `[bigquery.mapeo.<fuente>]` para fijarlo en
  los secrets (el disco de Streamlit Cloud es efímero).
- **Costo**: filtro de fecha parametrizado, agregación `GROUP BY` en el servidor, filtro por
  marca (`[forusight] marcas`, por defecto `AZALEIA`) con semijoin a ARTI, columnas
  explícitas y **dry run** con tope `max_gb` (20 GB) antes de cada consulta.
- **Stock**: `stock_tiendas + stock_bodega` (igual que Catálogo); se puede quitar
  `stock_bodega` del mapeo. Días con stock = fotos con stock × 7 / fotos de la semana.
- **Tiendas**: salen de la foto de stock y de la venta; el CD 320 y `tiendas_excluidas` no
  reciben. Sin clusters todavía: similitud por coseno del mix, importancia = percentil de
  venta.
- El ARTI de BigQuery **no trae precio**: `rango_precio = SIN_RANGO` hasta tener la fuente.

Errores de credenciales traducidos a algo accionable (firma JWT, formato de llave, permisos,
ruta inexistente), igual que en Repo Control Center.

## Configuración de secretos

Las credenciales **nunca** van en git: `.streamlit/secrets.toml` y `*.json` están en
`.gitignore`.

### Opción A — `st.secrets` (local o Streamlit Community Cloud)

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# completar [forusight] y [gcp_service_account]
```

En Streamlit Cloud, pegar el mismo contenido en *App settings → Secrets*.

### Opción B — variables de entorno (Cloud Run, contenedores, CI)

| Variable | Uso |
|---|---|
| `FORUSIGHT_DATA_SOURCE` | `bigquery` (tablas Forus), `mart` o `synthetic` |
| `FORUSIGHT_GCP_PROJECT` | proyecto que ejecuta/factura las consultas |
| `FORUSIGHT_BQ_LOCATION` | región de los datasets (ej. `US`) |
| `FORUSIGHT_DATASET_MART` / `FORUSIGHT_DATASET_APP` | datasets de Forusight |
| `FORUSIGHT_CD_ID` | `320` |
| `FORUSIGHT_CACHE_TTL_SECONDS` | TTL de la caché de lectura |
| `FORUSIGHT_GCP_SA_JSON` | JSON de la cuenta de servicio (sólo si no hay ADC) |

Si no hay cuenta de servicio en secrets ni en `FORUSIGHT_GCP_SA_JSON`, se usan las
*Application Default Credentials* (`gcloud auth application-default login`, o la identidad
del servicio en Cloud Run). Es la opción recomendada en GCP: no hay llaves que rotar.

### Cuenta de servicio con mínimo privilegio

- `roles/bigquery.jobUser` en el proyecto de ejecución (`job_project_id`).
- `roles/bigquery.dataViewer` sobre los datasets de las tablas fuente (lectura directa; es la
  misma cuenta que ya usan Catálogo/Repo Control Center) o sólo sobre el MART cuando exista.
- `roles/bigquery.dataEditor` **sólo** en el dataset APP.

## Contratos canónicos (`src/forusight/data/schemas.py`)

| Contrato | Grano | Columnas |
|---|---|---|
| `VentaSemanalSchema` | semana × tienda × SKU | semana_inicio, tienda_id, sku, unidades, dias_con_stock (0-7) |
| `StockTiendaSchema` | tienda × SKU | stock_disponible, stock_transito |
| `StockCDSchema` | SKU | fisico, reservado, comprometido |
| `DimProductoSchema` | SKU | modelo_id, modelo_color_id, color, talla, talla_orden, categoria, genero, rango_precio |
| `DimTiendaSchema` | tienda | nombre, cluster, formato, importancia_comercial (0-1), activa, max_unidades_corrida |
| `EngineParams` (pydantic) | — | todo `params.yaml` |
| `DistribucionSchema` | tienda × SKU | salida del motor (incluye filas no enviadas con motivo) |

## Motor (resumen)

1. **Disponibilidad** (12 semanas, por tienda×modelo-color y por SKU): `NUNCA_TUVO`,
   `EXPOSICION_INSUFICIENTE` (< 14 días con stock y sin venta), `TUVO_SIN_VENTA`,
   `TUVO_Y_VENDE`, `QUIEBRE` (vendía y hoy está en 0 o bajo el mínimo de exhibición). Un 0 en
   ventas sólo significa "sin demanda" en `TUVO_SIN_VENTA`.
2. **Demanda**: excluye semanas sin exposición; corrige la exposición parcial
   (`venta / max(expo, 0.3)`, tope 2×); pesos 0.5/0.3/0.2 sólo con volumen ≥ 8 y tendencia
   significativa; factor de tendencia acotado a [0.85, 1.2]. Sin historia: velocidad en
   tiendas similares × índice de la tienda en la categoría × 0.7.
3. **Curva de tallas**: tasa corregida por disponibilidad por talla, suavizado bayesiano con
   prior jerárquico (tienda×categoría×género → cluster×modelo → cluster×categoría →
   nacional), sólo tallas fabricadas, redondeo Hamilton (suma exacta).
4. **Afinidad** A1–A5 en [0, 1], penalización fuerte a `TUVO_SIN_VENTA`; bajo el umbral no
   se introduce el modelo.
5. **Objetivo y necesidad**: cobertura = lead time + revisión + seguridad (por categoría y
   rotación); mínimo de exhibición en tallas core; sobrestock → 0; curva rota → bono.
6. **Asignación** por SKU: suficiente → cada tienda su necesidad; escaso → etapa 1
   (quiebres hasta el mínimo) y etapa 2 (heap marginal con prioridad recalculada).
   Restricciones duras verificadas con aserción explícita; desempate determinista;
   introducción sólo si el CD cubre la curva mínima.
7. **Motivos**: código + texto para cada fila, enviada o no.

Supuestos que conviene validar están marcados como `PENDIENTE` en `params.yaml`.

## Relevamiento del esquema fuente

Ejecutar en BigQuery (consola), sobre el proyecto de Forus, y compartir el resultado:

- `sql/discovery/01_information_schema_columns.sql`: columnas, tipos, partición, cluster y
  descripción.
- `sql/discovery/02_volumetria.sql`: filas y tamaño por tabla.

## Información pendiente

| | Tema | Estado |
|---|---|---|
| a | historial diario de stock por tienda | **en curso**: se usa el historial de `stock_bi` por `fecha_corte`; confirmar que la foto es diaria |
| b | grano de ventas y devoluciones | ruta de `ventas_table` y si las devoluciones vienen netas |
| c | codificación SKU/modelo/color/talla | **resuelto con ARTI** (`CODINT_MA`, `CODMOD_MA`, `CODCOL_MA`, `TALNUM_MA`); validar el valor de `MARCA_MA` para Azaleia |
| d | físico/reservado/comprometido del CD 320 | foto de `stock_bi` (sin reservas) o archivo STOCK CD; falta tabla en BigQuery |
| e | tránsitos | `stock_bi` no trae tránsito: se asume 0 |
| f | clusters, formatos e importancia de tiendas | pendiente |
| g–j | despacho, lead time, parámetros, exclusiones (bodegas eComm) | pendiente |
| k | formato de exportación WMS/ERP | pendiente |
| l | volumetría | pendiente (`sql/discovery/02_volumetria.sql`) |
| m | destino de despliegue | Streamlit Cloud, como los otros Control Center (supuesto) |
| n | proyecto GCP para el dataset APP | pendiente: hasta entonces las aprobaciones no se persisten |
