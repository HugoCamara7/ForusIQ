Crea una presentación de 8 diapositivas, 16:9, en español, para explicar en 5 minutos la app **Forusight** a gerentes y planificadores de Forus Perú (retail de calzado y vestuario: Hush Puppies, Columbia, Vans, Rockford, Azaleia, entre otras).

**Estilo:** corporativo, limpio y moderno. Fondo blanco. Azul marino #17269A como color principal, azul Forus #2367FF y celeste #009FE3 como acentos. Verde #16A34A solo para lo bueno y rojo #DC2626 solo para alertas. Tipografía sans-serif (Inter o Montserrat). Íconos de línea simples. Poco texto: frases cortas y números grandes. En cada diapositiva, un mensaje principal en una línea.

**Diapositiva 1 – Portada**
Título: «Forusight». Subtítulo: «Reposición inteligente del CD a las tiendas». Línea pequeña: «Forus Perú · Distribución CD 320». Imagen sugerida: un camión saliendo de un centro de distribución hacia tiendas de un mall.

**Diapositiva 2 – El problema**
Título: «Reponer bien es reponer a tiempo, en la talla correcta». Tres íconos con texto corto:
- Quiebres de talla en tienda = venta perdida.
- Dependíamos de un proveedor externo (Neogística) para decidir qué enviar.
- Cada día hay que decidir miles de tienda × talla en minutos.

**Diapositiva 3 – Qué hace Forusight**
Título: «Decide qué enviar, a qué tienda y por qué». Flujo horizontal de 5 pasos con flechas:
1. BigQuery: stock, venta, pedidos.
2. Necesidad por tienda y talla.
3. Reparto del stock del CD.
4. Solo las tiendas con ruta ese día.
5. Archivo listo para cargar en SIAL.
Nota abajo: «Cada fila trae su motivo en texto».

**Diapositiva 4 – Cómo calcula (la fórmula)**
Título: «Una regla simple y transparente». Bloque central tipo ecuación, grande:
- Posición = Stock en tienda + Tránsito
- Nivel máximo = máx(Stock mínimo ; demanda hasta la próxima ruta + seguridad)
- Si Posición ≤ Punto de reorden → enviar hasta el Nivel máximo
Al costado, ejemplo con números: «Vende 1 par/semana → nivel 2 → tiene 0 → se envían 2».

**Diapositiva 5 – Tránsito sin errores**
Título: «Contamos lo que de verdad viene en camino». Tabla visual de 3 filas con check/cruz:
- Aprobado o en picking (en el CD) → cuenta.
- Despachado → cuenta solo dentro del plazo real de entrega de cada tienda (1 día en Lima, 2–5 en provincia).
- Recepcionado o creado después del cierre del stock → no cuenta.
Mensaje: «Misma foto que el stock: cada día se analiza lo mismo».

**Diapositiva 6 – El día a día**
Título: «Así se usa». Línea de tiempo:
- 3:00 llega el cierre de stock.
- 6:30 la corrida automática deja el archivo en Descargas.
- Revisión en Inicio.
- «Aprobar y descargar».
- Carga en SIAL.
Incluye una maqueta de pantalla con 4 tarjetas: Unidades a enviar, Tiendas con envío, Modelos color, Stock CD 320. Al lado, un velocímetro de «Disponibilidad del retail» en 97 % (verde, meta ≥ 93 %).

**Diapositiva 7 – Resultados y control**
Título: «Medible y comparable». Cuatro cifras grandes:
- 100 % igual stock físico y del CD frente al reporte de Neogística.
- 99,96 % igual cantidad a enviar cuando coinciden posición y nivel.
- 97,2 % disponibilidad en las tiendas de la ruta (meta ≥ 93 %).
- 0 archivos externos necesarios.
Abajo: «Lo enviado nunca supera el stock del CD».

**Diapositiva 8 – Cierre**
Título: «Forusight: datos propios, decisiones propias». Tres viñetas:
- Menos quiebres.
- Reposición explicada fila por fila.
- Control total de Forus.
Llamado a la acción: «Revisa el Manual y el Diccionario dentro de la app».
