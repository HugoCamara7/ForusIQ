# Manual de Forusight

Forusight decide **qué enviar del CD a cada tienda**: modelo, color, talla y cantidad, con el
motivo de cada fila. Usa sólo datos de Forus en BigQuery (stock, venta, pedidos y maestros); no
necesita ningún archivo de Neogística.

**Salida:** CD 320 (AZALEIA: bodega 380) → tienda → modelo-color → talla → cantidad → motivo.

---

## 1. El día a día

| Hora | Qué pasa | Dónde |
|---|---|---|
| 3:00 | Llega a BigQuery el cierre de stock de ayer (stock_bi) y la venta. | — |
| 6:30 | La corrida automática genera el archivo del día. | **Descargas** |
| Durante el día | Si hace falta, se corre a mano: marca, semana, día de reposición → **Ejecutar corrida**. | **Inicio** |
| Después | Se revisa, **Aprobar y descargar**, y el archivo (hoja SIAL) se carga en el sistema. | **Inicio** |

Al cargarlo, los pedidos se crean en el sistema. En la corrida del **día siguiente** cuentan como
tránsito. La misma corrida del mismo día no los ve (el tránsito sólo toma pedidos hasta el cierre
del stock), así que se usa **el primer archivo del día**.

---

## 2. De dónde sale cada dato

| Dato | Fuente | Nota |
|---|---|---|
| Stock de tiendas y del CD | `stock_bi` (último corte) | Stock con fecha de corte 12/10 = stock al cierre del 12/10. |
| Venta | `ft_pe_venta_retail` | 12 semanas cerradas + semana en curso + venta diaria. |
| Tránsito | Tablas de pedidos (cabecera + detalle) | Ver sección 4. |
| Producto | ARTI (BigQuery) | SKU, modelo, color, talla, marca, clase, género. Un producto de la marca que aún no está en ARTI (p. ej. códigos nuevos de AZALEIA) entra igual si se vendió con esa marca, si se vendió o tiene stock en una tienda de la marca (nombre «AZALEIA …») o si tiene stock en su bodega (380). Modelo, color, talla y clase se toman de la venta (`ft_pe_venta_retail`, 180 días); un producto que nunca se vendió no se puede describir y queda fuera. Inicio lo avisa en las notas. |
| Tiendas | **BigQuery primero**: maestro de locales `pe_bronze.stg_pe_perucentral_loca` (`maestro_tiendas_table`) | Código (`NLOCAL_LOC`), nombre (`NOMBRE_LOC`) y ciudad. La cadena sale del nombre (AZALEIA PRIMAVERA → AZALEIA); bodegas y cadenas sin marcas quedan fuera. `config/tiendas_forus.csv` pone el **nombre completo** (BigQuery lo trae resumido) y completa el centro comercial y las tiendas que BigQuery no trae. Lo usan la corrida, Bloqueos, Rutas y Temporadas por tienda. |
| Cadenas, rutas | `config/` | Las rutas se editan en **Rutas**. |
| Stock mínimo, empaque, k | Maestro de planificación guardado (de Forus) + **Parámetros → Stock mínimo** | No vence (sección 3.4). |
| Temporada comercial | Maestro de temporadas guardado + **Parámetros → Temporadas** | Decide qué se repone (sección 3.8). |
| Bloqueos | Maestro (reporte de bloqueos del 14/10/2026) + cargas por Excel | Se editan y descargan en **Bloqueos**. |

---

## 3. Cómo se calcula la cantidad (paso a paso)

### 3.1 Stock físico de la tienda
```
Stock Físico = stock_bi (stock al cierre del día de corte)
             − venta posterior al día de corte (hasta ayer)
             + lo recibido después del día de corte
```
La **fecha de corte** es el día cuyo cierre trae stock_bi: el lunes 13/10 llega el stock con fecha
de corte 12/10, que es el stock al cierre del domingo 12/10. Con el corte de ayer no cambia nada;
un lunes con el corte del sábado, se ajusta lo del domingo.

### 3.2 Tránsito y posición
```
Stock Trán. Int. = Tránsito en CD + Tránsito despachado      (detalle en la sección 4)
Posición         = Stock Físico + Stock Trán. Int.
```

### 3.3 Pronóstico de la talla
```
Pronóstico [un/sem] = k × venta 4 últimas semanas del modelo-color en la tienda
                      × participación de la talla
Participación       = venta nacional 12 sem del SKU ÷ venta nacional 12 sem de las tallas
                      que la tienda vendió en 12 semanas
```
k es el factor del planificador por marca × clase × prenda × género. Si no hay k: 0,325 en
calzado y accesorios, 0,35 en vestuario.

### 3.4 Stock mínimo (piso del nivel)
Es un dato **propio de Forus**: no se vuelve a pedir a Neogística. Se usa, en este orden:

0. el valor que alguien guardó en **Parámetros → Stock mínimo** (con usuario y fecha);

luego el último valor conocido de la tienda × SKU en el maestro guardado. Si la clave no está:
1. el más frecuente del SKU en otras tiendas;
2. luego el de marca × prenda × talla;
3. luego el de marca × prenda;
4. si no hay nada, 1.

Una tienda nueva puede tomar los valores de una **tienda espejo** (`config/tiendas_espejo.csv`).

Con 6 reportes (01–12/10/2026): el stock mínimo cambia en ~3,5 % de las claves entre reportes,
y el último valor conocido acierta el 94,5 % una semana después. Con el stock mínimo guardado,
el nivel máximo coincide con el de Neogística en el **95,4 %** de las tienda × talla; si se
reemplazara por una regla de venta, no pasa del 84 %. Por eso se conserva y sólo se edita
cuando el planificador lo cambia.

### 3.5 Nivel máximo y punto de reorden
```
1. Demanda hasta el próximo envío = Pronóstico × (Leadtime + Período Revisión) ÷ 7
2. Seguridad     = 0,84 × raíz cuadrada(Demanda hasta el próximo envío)
3. Nivel Máximo  = el mayor entre Stock Mínimo y (Demanda + Seguridad) redondeado hacia arriba
4. Punto Reorden = Nivel Máximo − 1
```
Se divide entre 7 para pasar de unidades por semana a unidades por día; el 0,84 da ≈ 80 % de
seguridad por si la tienda vende más de lo esperado.

Ejemplo: Pronóstico 1 par/semana, Leadtime 3 días, Revisión 2,33 días.
1. Demanda = 1 × (3 + 2,33) ÷ 7 = 0,76 pares.
2. Seguridad = 0,84 × raíz(0,76) = 0,73.
3. 0,76 + 0,73 = 1,49 → redondeado hacia arriba = 2; Stock Mínimo 1 → **Nivel Máximo 2**.
4. **Punto Reorden 1**: si la tienda tiene 1 par o menos (stock + tránsito), se envía hasta 2.

Una talla sin stock, tránsito ni venta, o de un modelo sin venta reciente en la tienda, no se
evalúa (nivel 0), igual que en el reporte.

### 3.6 Necesidad
```
Si Posición ≤ Punto Reorden:
    Necesidad = máx( Nivel Máximo − Posición ; venta desde la ruta anterior )
    redondeada a empaques completos (Unidad Empaque)
Si no: 0
```
**Marca nueva sin stock mínimo (AZALEIA):** lo vendido desde la ruta anterior se repone
**siempre**, aunque la talla no haya llegado a su punto de reorden (sin Stock Mínimo ni
planificación, su nivel queda en 1–2 y una tienda recién llenada casi nunca llega al reorden).
Se configura en `reposicion_venta.marcas_reponer_vendido`.

### 3.7 Reparto del CD
Por SKU, el **Stock en CD** (`disponible` de stock_bi) se reparte:
- **si alcanza**, cada tienda recibe su necesidad;
- **si no alcanza**, se reparte en este orden:
  1. lo vendido;
  2. los quiebres;
  3. por prioridad: riesgo de quiebre, venta, prioridad A/B/C de la tienda, curva rota.
  Las tiendas JOCKEY tienen prioridad máxima. Letra y orden de cada tienda (también las
  AZALEIA) en `config/prioridad_centros.csv`.

Lo enviado **nunca supera** el Stock en CD.

### 3.8 Filtros finales
- Sólo reciben las tiendas cuyo mall tiene **ruta ese día** (página **Rutas**). Una tienda con
  **ruta propia** (`config/rutas_tiendas.csv`, p. ej. las AZALEIA: Jockey y San Miguel sólo MI,
  Pucallpa LU/MI) recibe esos días aunque su mall salga otros.
- Un modelo-color **bloqueado** para la tienda no se envía (página **Bloqueos**).
- **Temporadas**, en el orden comercial (INVIERNO = marzo a septiembre, VERANO = septiembre a marzo del año siguiente; **ESCOLAR** va con el VERANO del mismo año): INVIERNO 2026 → VERANO 2026 = ESCOLAR 2026 → INVIERNO 2027 → VERANO 2027…
  - **Liquidadoras (DH, SE, FB): reposición por jerarquía.** Sólo temporadas **hasta INVIERNO 2026 inclusive** (todo lo anterior a VERANO 2026 y ESCOLAR 2026). No reciben VERANO 2026, ESCOLAR 2026, temporadas posteriores ni FUTURA. Nada de lo de abajo cambia esta regla (`surtido.temporada_hasta_liquidadoras`).
  - **Tiendas fuera de parámetros** (página **Temporadas por tienda**): esa temporada mínima y las posteriores.
  - **Colecciones activas por marca** (reporte SAP **1037 – Colecciones Activas Marca**, página **Temporadas por tienda**): las tiendas de línea reponen **además** las colecciones activas de la marca × clase del producto: las de `TODO` (todas las tiendas) **más** las propias de la tienda (Código centro). Ej.: KEDS calzado repone VERANO 2025 e INVIERNO 2026 en todas las tiendas de línea; RKF CAYMA (147) repone además INVIERNO 2026 de ROCKFORD calzado y vestuario, sin perder VERANO 2026. No quita nada de lo de arriba ni cambia a las liquidadoras.
  - **Resto de tiendas (parámetro general):** **VERANO 2026 en adelante** y **ESCOLAR 2026** (más FUTURA); INVIERNO 2026 y anteriores no.
  - Un modelo nuevo sin temporada conocida se repone en todas. La temporada de cada modelo-color se corrige en **Parámetros → Temporadas**.

---

## 4. Tránsito (lo más importante para cuadrar)

| Estado del pedido | ¿Cuenta? |
|---|---|
| 1 Aprobado, 2 en Picking (aún en el CD) | Sí, todo el historial → **Tránsito en CD** |
| 3 Documentado, 6 en Transporte, 7 Prerecepcionado | Sólo dentro del **plazo de entrega de la tienda** → **Tránsito despachado** |
| 4 Recepcionado, o con fecha de recepción | No: ya está en el stock |
| 0 Creado | No |

Reglas:
- **Plazo de entrega de la tienda:** percentil 95 de los días entre pedido y recepción de sus
  propios pedidos de los últimos 120 días, + 1 día, con un mínimo de 7 días (sin datos: 7). Se ve en **Conexión →
  Medir plazos de entrega**. Un pedido despachado más antiguo que el plazo ya llegó y quedó sin
  cerrar.
- **Misma foto que el stock:** sólo cuentan los pedidos creados **hasta el día del cierre del
  stock**. Los creados después (los de hoy, también los de Neogística de la madrugada) no
  cuentan. Inicio dice cuántas unidades quedaron fuera.
- **Clasificaciones:** reposición, llenado de canal y traspasos entre tiendas (no devoluciones
  al CD).
- El tránsito de `stock_bi` **no se usa**: es lo que sale de la tienda, no lo que llega.

Para revisar una tienda: **Conexión → Revisar tránsito**. Cada línea de pedido dice si cuenta y
por qué.

---

## 5. Indicadores de Inicio

| Indicador | Fórmula |
|---|---|
| Unidades a enviar | Σ Cantidad Pedida Final |
| Tiendas con envío | tiendas de la ruta con al menos 1 unidad |
| Modelos color | modelos-color con al menos un envío |
| Stock CD | Σ Stock en CD (disponible) |
| **Disponibilidad del retail** | tallas activas con stock ÷ tallas activas (todas las tiendas) |
| Con tránsito | igual, contando la talla que ya va en camino |
| Después del envío | igual, sumando lo que se envía hoy |

- **Talla activa:** talla de un modelo-color que la tienda maneja (tiene stock o vendió en 12
  semanas) y que está planificada (stock objetivo > 0) o tiene stock.
- Una talla sin stock que el CD tampoco tiene no cuenta como quiebre.
- Colores del velocímetro: rojo < 90 %, naranja 90–93 %, verde ≥ 93 % (meta).

Recuadros de aviso:
- **Tránsito de pedidos:** total, en CD, despachado, y lo que quedó fuera por ser posterior al
  cierre.

---

## 6. Páginas

| Página | Para qué |
|---|---|
El menú lateral se divide en **Operación**, **Mantenedores** y **Configuración**.

| Sección | Página | Para qué |
|---|---|---|
| Operación | **Inicio** | Nueva corrida, resultado, indicadores, **Aprobar y descargar**. |
| Operación | **Descargas** | Archivos de las corridas automáticas de las 6:30. |
| Mantenedores | **Bloqueos** | En una sola pantalla: elegir **Bloquear** o **Desbloquear**, subir el Excel (COD MOD COL, Cod Tienda; vacío = todas las tiendas), revisar la validación (OK / omitidas / rechazadas) y confirmar. Descarga del **maestro completo de bloqueos** (reporte del 14/10/2026 + cargas por Excel) y consulta por tienda o modelo. |
| Mantenedores | **Temporadas por tienda** | Temporada mínima de reposición por tienda (temporadas fuera de parámetros) por Excel (Cod Tienda, Temporada minima): asignar o volver al parámetro general, con validación, consulta y descarga. Además, **colecciones activas por marca** (reporte SAP 1037): consulta del mapeo vigente y carga de un reporte nuevo, que reemplaza al anterior completo. |
| Mantenedores | **Rutas** | Qué días sale el camión a cada mall, y las excepciones de la semana. |
| Configuración | **Parámetros** (acceso restringido) | Reglas del motor (formulario o YAML) y los mantenedores **Temporadas** y **Stock mínimo**. Sólo la ven y usan los usuarios autorizados en los secrets (`[app_auth.permisos] parametros`). |
| Configuración | **Conexión** (admin) | Tablas y mapeo, plazos de entrega, revisar tránsito y venta, prueba de lectura. |

### Bloqueos: validación de la carga

| Resultado | Cuándo | ¿Se guarda? |
|---|---|---|
| OK | Bloquear algo que no estaba bloqueado / desbloquear algo bloqueado. | Sí |
| Omitida | Fila repetida en el archivo; bloquear lo que ya está bloqueado; desbloquear lo que no lo está. | No (no cambia nada) |
| Rechazada | Falta COD MOD COL; formato que no es MODELO-COLOR; tienda que no existe; desbloquear en una tienda algo bloqueado para todas (se desbloquea con Cod Tienda vacío). | No |

Al confirmar se vuelve a validar contra los bloqueos de ese momento: un doble clic o una carga simultánea no duplica registros.

### Temporadas por tienda: validación de la carga

Se rechaza la tienda vacía o inexistente, una **liquidadora** (DH, SE, FB: repone por jerarquía y no se configura), una temporada que no sea `VERANO AAAA` o `INVIERNO AAAA`, o una tienda que aparece con dos temporadas distintas. Se omite lo que no cambia nada. En el Excel descargado, una tienda con la temporada vacía sigue el parámetro general.

---

## 7. Archivo Forusight (`AAAAMMDD_Distribucion_Forusight.xlsx`)

| Hoja | Contenido |
|---|---|
| Resumen | Totales y unidades por tienda; reposición de lo vendido. |
| Distribución | Una fila por tienda × SKU de la ruta, con todas las columnas y el motivo. |
| Dinámica | Modelo-color-talla × tienda (suma de cantidad > 0). |
| SIAL | Lo mismo en valores: **el archivo que se carga**. |
| Diccionario | Qué es cada columna, cómo se calcula, fuente y cómo compararla con Neogística. |

---

## 8. Cómo comparar con Neogística (checklist)

Sigue este orden:

1. **Mismo día y mismas tiendas:** filtra en el reporte las tiendas de la ruta del día y sólo
   «Revision de stock».
2. **Stock Físico y Stock en CD:** deben ser iguales (12/10: 100 %).
3. **Tránsito:** «Stock Trán. Int.» de Forusight contra Trán. Int. + Trán. Prov. del reporte.
   Ninguno de los dos trae los pedidos del mismo día.
4. **Pronóstico:** si difiere, revisa la venta de 4 semanas del modelo-color y el k.
5. **Stock Mínimo y Nivel Máximo:** las diferencias suelen ser stock mínimos que el planificador
   cambió después del 12/10.
6. **Cantidad:** con posición, nivel y punto de reorden iguales, la cantidad coincide (12/10:
   99,96 % de esas filas). La diferencia restante es el reparto del CD cuando no alcanza.
7. **Disponibilidad:** compara la de las mismas tiendas (12/10, HUSH PUPPIES en ruta: Forusight
   97,2 %, reporte 97,5 %).

---

## 9. Parámetros clave (`params.yaml`)

| Parámetro | Valor | Efecto |
|---|---|---|
| `nivel_neo.z` | 0,84 | Seguridad del nivel máximo (≈ 80 %). |
| `nivel_neo.dias_maximos` | 0 | El maestro de planificación no vence. |
| `recepcion.estados_transito` | [1, 2] | Cuentan con todo el historial. |
| `recepcion.estados_transito_recientes` | [3, 6, 7] | Cuentan dentro del plazo de la tienda. |
| `recepcion.percentil_plazo` / `margen_plazo_dias` | 0,95 / 1 | Plazo = p95 + 1 día. |
| `recepcion.plazo_minimo_dias` | 7 | Un pedido despachado cuenta al menos 7 días. |
| `surtido.temporada_desde` / `temporadas_reponer` | VERANO 2026 / ESCOLAR 2026, FUTURA | Temporadas que se reponen (parámetro general). |
| `surtido.temporada_hasta_liquidadoras` | INVIERNO 2026 | Liquidadoras DH, SE, FB: sólo hasta esta temporada (jerarquía). |
| `surtido.temporada_desde_tienda` | {} | Temporada mínima por tienda: la llena **Temporadas por tienda** en cada corrida. |
| `surtido.colecciones_activas` | reporte 1037 del 15/10/2026 | Colecciones activas por tienda × clase × marca (`TODO` = todas): se suman a lo anterior en las tiendas de línea. El último reporte subido en **Temporadas por tienda** lo reemplaza. |
| `recepcion.dias_transito_recientes` | 7 | Plazo si la tienda no tiene datos. |
| `recepcion.solo_hasta_cierre_stock` | true | Tránsito en la misma foto que el stock. |
| `stock_cd.componentes` | disponible | Qué parte del CD se reparte. |
| `reposicion_venta.reponer_venta_desde_ruta` | true | Repone lo vendido desde la ruta anterior. |
| `reposicion_venta.marcas_reponer_vendido` | ["AZALEIA"] | Marcas nuevas sin stock mínimo: lo vendido desde la ruta se repone siempre, sin esperar el punto de reorden. |
