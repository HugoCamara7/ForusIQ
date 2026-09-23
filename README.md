# Forusight

Reposición y distribución de productos **Azaleia (Forus)** desde el **CD 320** (operado por
Neogistica) hacia las tiendas. La decisión de qué distribuir es de Forus y la toma esta app.

**Salida:** CD 320 → Tienda → Modelo/SKU → Talla → Cantidad, con el motivo explicado.

| Tienda | Modelo | SKU | Talla | Stock tienda | Venta 4S | Venta 12S | Demanda estimada | Stock objetivo | Necesidad | Stock CD 320 | Cantidad a distribuir | Motivo |
|---|---|---|---|---|---|---|---|---|---|---|---|---|

> Estado: **Fase 0 + Fase 1 (motor)**. El motor corre sobre contratos canónicos y datos
> sintéticos. El mapeo a las tablas reales de Forus queda pendiente del esquema
> (ver [Información pendiente](#información-pendiente)).

---

## Arquitectura

```
Tablas fuente Forus (solo lectura)
        │  scheduled queries (sql/marts, pendiente de esquema)
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
app/pages/                       1_Dashboard … 5_Parametros
app/components/                  estado/caché, filtros, kpis, tablas, panel de motivo
src/forusight/config/            settings.py (pydantic) + params.yaml
src/forusight/data/              bq_client.py, repository.py, schemas.py, synthetic.py, queries/*.sql
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

Sin configuración, la app arranca en **modo demo** (datos sintéticos). En la barra lateral:
fuente de datos, fecha de corte, usuario y **Ejecutar corrida**.

## Tests y lint

```bash
pytest                 # toda la suite
pytest tests/test_allocation.py -k cd      # p. ej. sólo "nunca asignar más que el CD"
ruff check . && ruff format --check .
```

La CI (`.github/workflows/ci.yml`) corre ruff + pytest en cada push y PR.

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
| `FORUSIGHT_DATA_SOURCE` | `bigquery` o `synthetic` |
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

- `roles/bigquery.jobUser` en el proyecto de ejecución.
- `roles/bigquery.dataViewer` **sólo** en el dataset MART.
- `roles/bigquery.dataEditor` **sólo** en el dataset APP.
- Ningún permiso sobre las tablas fuente de Forus (las lee la scheduled query, con su
  propia identidad).

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

a) historial diario de stock por tienda · b) grano de ventas y devoluciones ·
c) codificación SKU/modelo/color/talla · d) físico/reservado/comprometido del CD 320 ·
e) tránsitos · f) clusters, formatos e importancia de tiendas · g) despacho en pares o curvas
cerradas · h) lead time y frecuencia · i) cobertura, mínimos, máximos y tallas core ·
j) exclusiones · k) formato de exportación WMS/ERP · l) volumetría · m) destino de
despliegue · n) proyecto GCP para los datasets MART y APP.
