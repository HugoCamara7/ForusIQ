"""Diccionario del archivo Forusight: qué es cada columna, cómo se calcula y de dónde sale.

Una sola fuente para la hoja «Diccionario» del archivo y para el Excel del diccionario.
Cada entrada: (sección, qué es, cómo se calcula, fuente en BigQuery, cómo compararla con la
misma columna del reporte de distribución de Neogística).
"""

from __future__ import annotations

PRODUCTO, TIENDA, VENTA, NIVELES, STOCK, RESULTADO, MOTIVO = (
    "Producto",
    "Tienda",
    "Venta",
    "Pronóstico y niveles",
    "Stock",
    "Resultado",
    "Motivo",
)

#: Colores Forus por sección: (banda oscura, encabezado claro, texto del encabezado).
COLORES = {
    PRODUCTO: ("#17269A", "#E3E7FB", "#17269A"),
    TIENDA: ("#2367FF", "#E0EAFF", "#1846B8"),
    VENTA: ("#009FE3", "#DDF3FC", "#006C9C"),
    NIVELES: ("#6E3CBC", "#EDE5F8", "#4E2A87"),
    STOCK: ("#152238", "#E2E6EE", "#152238"),
    RESULTADO: ("#E8A400", "#FFF1CC", "#7A5200"),
    MOTIVO: ("#475569", "#EEF1F5", "#334155"),
}

REP = "Igual nombre y significado en el reporte de Neogística: debe coincidir."

DICCIONARIO: dict[str, tuple[str, str, str, str, str]] = {
    "Código SKU": (
        PRODUCTO,
        "Código interno del artículo (modelo + color + talla).",
        "Se normaliza: mayúsculas, sin espacios ni «.0».",
        "ARTI · CODINT_MA (= stock_bi · id_producto, venta · codpro_df)",
        REP,
    ),
    "Modelo": (
        PRODUCTO,
        "Nombre del modelo.",
        "Tal cual del maestro.",
        "ARTI · DESCRIPCION_MA",
        REP,
    ),
    "Color": (PRODUCTO, "Nombre del color.", "Tal cual del maestro.", "ARTI · COLOR_MA", REP),
    "Código Modelo": (
        PRODUCTO,
        "Código del modelo (agrupa todos sus colores y tallas).",
        "Tal cual del maestro.",
        "ARTI · CODMOD_MA",
        REP,
    ),
    "Código Color": (
        PRODUCTO,
        "Código del color del modelo.",
        "Tal cual del maestro.",
        "ARTI · CODCOL_MA",
        REP,
    ),
    "Talla": (
        PRODUCTO,
        "Talla del SKU (390 = 39, 075 = 7,5).",
        "Tal cual del maestro.",
        "ARTI · TALNUM_MA",
        REP,
    ),
    "Descripción SKU": (
        PRODUCTO,
        "Descripción completa del SKU.",
        "Marca + Modelo + Color + Talla.",
        "ARTI · MARCA_MA, DESCRIPCION_MA, COLOR_MA, TALNUM_MA",
        REP,
    ),
    "Clase": (PRODUCTO, "Calzado, vestuario o accesorios.", "Tal cual.", "ARTI · TIPO_MA", REP),
    "Marca": (PRODUCTO, "Marca del producto.", "Tal cual.", "ARTI · MARCA_MA", REP),
    "Género": (PRODUCTO, "Género del producto.", "Tal cual.", "ARTI · GENERO_MA", REP),
    "Prenda": (PRODUCTO, "Tipo de prenda.", "Tal cual.", "ARTI (si la columna existe)", REP),
    "Temporada comercial": (
        PRODUCTO,
        "Temporada comercial del modelo-color (p. ej. VERANO 2026). Sólo se reponen INVIERNO "
        "2026 y VERANO 2026 (Parámetros → surtido); lo demás no aparece en el archivo.",
        "Maestro de temporadas guardado en Forusight (aprendido de reportes anteriores y del "
        "reporte de bloqueos; ARTI no trae la temporada comercial).",
        "config/temporadas.csv.gz",
        REP,
    ),
    "Código Centro": (
        TIENDA,
        "Código de la tienda que recibe.",
        "Número sin ceros a la izquierda (008 → 8).",
        "stock_bi · codigo_tienda / venta · nlocal_lv",
        REP,
    ),
    "Nombre Centro": (
        TIENDA,
        "Nombre de la tienda (el prefijo es la cadena: HP JOCKEY → HP).",
        "Maestro de tiendas; si no está, catálogo de tiendas de Forus.",
        "Maestro de tiendas / config/calendario_tiendas.csv",
        REP,
    ),
    "Centro Comercial": (
        TIENDA,
        "Mall donde está la tienda. La ruta de despacho es por mall.",
        "Maestro de tiendas.",
        "Maestro de tiendas",
        REP,
    ),
    "Zona CC": (TIENDA, "Lima o provincia.", "Maestro de tiendas.", "Maestro de tiendas", REP),
    "Código Grupo Planificación": (
        PRODUCTO,
        "Grupo de planificación de la clase.",
        "CALZADO → CLZ, VESTUARIO → VST, ACCESORIOS → ACC.",
        "Derivado de Clase",
        REP,
    ),
    "Grupo Requerimiento": (
        RESULTADO,
        "Tipo de requerimiento. El archivo sólo trae «Revision de stock» (reposición).",
        "Revision de stock = reponer lo vendido y anticipar. Carga Pedidos (modelos nuevos) no "
        "se genera: entra por carga manual.",
        "Regla de Forusight",
        "Compara sólo las filas «Revision de stock» del reporte: las «Carga Pedidos» son cargas "
        "manuales que Forusight no genera.",
    ),
    "Semanas (columnas con fecha)": (
        VENTA,
        "Unidades vendidas en cada una de las 12 semanas cerradas (lunes a domingo); el "
        "encabezado es el lunes de la semana.",
        "SUMA(unidades) por tienda × SKU × semana. La marca se filtra por ARTI. Si la venta "
        "llega atrasada, las 12 semanas terminan en la última semana completa con venta.",
        "ft_pe_venta_retail · fecmov_lv, nlocal_lv, codpro_df, unidades_venta",
        REP,
    ),
    "Demanda Periodo Actual": (
        VENTA,
        "Venta de la semana en curso (desde el lunes hasta hoy).",
        "SUMA(unidades) con fecha ≥ lunes de esta semana.",
        "ft_pe_venta_retail",
        REP,
    ),
    "Venta desde ruta anterior [un]": (
        VENTA,
        "Lo que la tienda vendió desde la ruta anterior de su mall hasta ayer: lo que el envío "
        "anterior todavía no cubrió.",
        "SUMA(venta diaria) con fecha entre la ruta anterior del mall y el día antes de la ruta "
        "de hoy. Ej.: vendió 10 el 24 y 15 el 25 y la ruta es el 26 → 25. Sin ruta: los "
        "últimos días del período de revisión.",
        "ft_pe_venta_retail (venta diaria de los últimos 14 días) + config/rutas_mall.csv",
        "No está en el reporte (propia de Forusight).",
    ),
    "Venta después del corte [un]": (
        VENTA,
        "Venta posterior al cierre del stock: todavía no está descontada del Stock Físico.",
        "SUMA(venta diaria neta, notas de crédito en negativo) con fecha > fecha_corte del stock "
        "y antes de hoy (la fecha de corte es el día cuyo cierre trae stock_bi).",
        "ft_pe_venta_retail + stock_bi · fecha_corte",
        "No está en el reporte: el reporte ya la trae descontada de su Stock Físico.",
    ),
    "Recepción después del corte [un]": (
        STOCK,
        "Lo que la tienda recepcionó después del cierre del stock: se suma al Stock Físico.",
        "SUMA(unidades de pedidos recepcionados con fecha de recepción > fecha_corte del "
        "stock), reposición, llenado de canal y traspasos.",
        "pedidos_header_table + pedidos_detail_table · fecrec",
        "No está en el reporte: ya viene sumada en su Stock Físico.",
    ),
    "VTA 2 SEM": (
        VENTA,
        "Venta de las 2 últimas semanas.",
        "Fórmula viva: última semana cerrada + Demanda Periodo Actual.",
        "Calculada en el Excel",
        "No está en el reporte.",
    ),
    "Pronóstico Demanda [un/semana]": (
        NIVELES,
        "Demanda semanal esperada de la talla en la tienda.",
        "Pronóstico = k × venta de las 4 últimas semanas cerradas del modelo-color en la tienda "
        "× participación de la talla. Participación = venta nacional de 12 semanas del SKU ÷ "
        "la de las tallas del modelo-color que la tienda vendió en 12 semanas (talla sin venta "
        "en 12 semanas: 0). k = factor por marca × clase × prenda × género del maestro de "
        "planificación guardado (si falta: 0,325 calzado y accesorios, 0,35 vestuario).",
        "Motor Forusight sobre ft_pe_venta_retail",
        "Debe coincidir; si no, revisar la venta de 4 semanas del modelo-color y el k.",
    ),
    "Leadtime [días]": (
        NIVELES,
        "Días que tarda el envío del CD a la tienda.",
        "Por tienda; si no está, 3 días. Se usa en la cobertura del nivel máximo (no en el "
        "tránsito: el plazo real de entrega se mide con las recepciones).",
        "config/calendario_tiendas.csv",
        REP,
    ),
    "Período Revisión [días]": (
        NIVELES,
        "Días entre dos rutas del mall de la tienda.",
        "Período oficial de Forus; si no hay, 7 ÷ despachos por semana del mall (3 → 2,33; 2 → "
        "3,5; 1 → 7).",
        "config/calendario_tiendas.csv + config/rutas_mall.csv",
        REP,
    ),
    "Punto Reorden [un]": (
        NIVELES,
        "Posición a la que la talla vuelve a pedir (como el reporte de distribución).",
        "Punto Reorden = Nivel Máximo − 1. Se pide sólo si Posición ≤ Punto Reorden. "
        "(Si el maestro guardado trae esa tienda × SKU de un reporte de hasta 3 días, usa su "
        "punto de reorden.)",
        "Motor Forusight + maestro de planificación",
        "Debe coincidir cuando coincide el Nivel Máximo.",
    ),
    "Nivel Máximo [un]": (
        NIVELES,
        "Stock que la talla debe tener para cubrir hasta la próxima ruta.",
        "1) Demanda hasta el próximo envío = Pronóstico × (Leadtime + Período Revisión) ÷ 7. "
        "2) Seguridad = 0,84 × raíz cuadrada de esa demanda (≈ 80 % de servicio). 3) Nivel "
        "Máximo = el mayor entre Stock Mínimo Total y (Demanda + Seguridad) redondeado hacia "
        "arriba. Ej.: Pronóstico 1 par/sem, Leadtime 3 y Revisión 2,33 → Demanda 0,76; "
        "Seguridad 0,73; 1,49 → 2. Talla sin stock, tránsito ni venta, o modelo sin venta "
        "reciente en la tienda: 0 (no se evalúa, como en el reporte).",
        "Motor Forusight + maestro de planificación",
        "Debe coincidir; si no, mirar Stock Mínimo Total y Pronóstico.",
    ),
    "Stock Mínimo Total": (
        NIVELES,
        "Piso del nivel máximo que fija el planificador por tienda × SKU.",
        "Último valor conocido de la tienda × SKU en el maestro de planificación guardado "
        "(no vence: cambia en ~3,5 % de las claves entre reportes y el último valor acierta el "
        "94,5 % una semana después). Si la clave no está: el más frecuente del SKU en otras "
        "tiendas, luego de la marca × prenda × talla, luego de la marca × prenda; si nada, 1. "
        "Una tienda sin claves propias toma las de su tienda espejo.",
        "config/planificacion_claves.csv.gz (maestro guardado)",
        "Puede diferir en las claves que el planificador cambió después del 12/10/2026.",
    ),
    "Código Centro Origen": (
        STOCK,
        "Centro de distribución que envía.",
        "320 (CD Forus); AZALEIA sale de la bodega 380.",
        "Secrets · cd_id + Parámetros → cd_por_marca",
        REP,
    ),
    "Stock en CD": (
        STOCK,
        "Stock del CD 320 que se puede repartir para ese SKU.",
        "stock_bi · disponible del CD (ya sin reservas de pedidos, retail, wholesale, "
        "multicanal y e-commerce), tal cual. La suma enviada a todas las tiendas nunca supera "
        "este valor.",
        "stock_bi · disponible (codigo_tienda = 320, último fecha_corte de este año)",
        "Debe coincidir con el «Stock en CD» del reporte (12/10: 100 % igual).",
    ),
    "Stock Físico [un]": (
        STOCK,
        "Stock de la tienda hoy: el del corte de stock, menos lo vendido y más lo recepcionado "
        "después del corte.",
        "stock_tiendas del último fecha_corte (el stock al cierre de ese día; el lunes 13/10 "
        "llega el del 12/10) − venta neta posterior a la fecha de corte + lo recibido después "
        "de la fecha de corte (con el corte de ayer no cambia; un lunes con el corte del sábado, "
        "ajusta el domingo).",
        "stock_bi · stock_tiendas",
        "Debe coincidir con el «Stock Físico» del reporte (12/10: 100 % igual).",
    ),
    "Stock Trán. Int. [un]": (
        STOCK,
        "Unidades en camino a la tienda.",
        "Pedidos del sistema hacia la tienda (reposición, llenado de canal y traspasos): "
        "aprobados o en picking (todo el historial) y documentados, en transporte o "
        "prerecepcionados dentro del plazo de entrega de la tienda. Recepcionado o más antiguo "
        "se toma como ya recibido. Sólo pedidos creados hasta el día del cierre del stock (misma "
        "foto que el stock). No incluye el tránsito de stock_bi: es lo que SALE de la tienda.",
        "pedidos_header_table + pedidos_detail_table (candes_pd o canped_pd)",
        "Comparar con «Stock Trán. Int.» + «Stock Trán. Prov.» del reporte. El reporte se genera "
        "en la madrugada, así que tampoco trae los pedidos de ese día.",
    ),
    "Tránsito en CD [un]": (
        STOCK,
        "Parte del tránsito que todavía está en el CD.",
        "Pedidos hacia la tienda aprobados o en picking (todo el historial). Tránsito en CD + "
        "Tránsito despachado = Stock Trán. Int.",
        "pedidos_header_table + pedidos_detail_table",
        "No está en el reporte (detalle propio de Forusight).",
    ),
    "Tránsito despachado [un]": (
        STOCK,
        "Parte del tránsito que ya salió del CD y va camino a la tienda.",
        "Pedidos documentados, en transporte o prerecepcionados dentro del plazo de entrega de "
        "la tienda (percentil 95 de sus recepciones + 1 día); más antiguos ya llegaron.",
        "pedidos_header_table + pedidos_detail_table",
        "No está en el reporte (detalle propio de Forusight).",
    ),
    "Posición Stock [un]": (
        STOCK,
        "Lo que la tienda tiene y lo que ya viene en camino.",
        "Posición = Stock Físico + Stock Trán. Int.",
        "Calculada",
        "Reporte: Físico + Trán. Int. + Trán. Prov. − Comprometido − Backorder (los dos últimos "
        "casi siempre 0).",
    ),
    "Cantidad Pedida Final [un]": (
        RESULTADO,
        "Unidades a enviar del CD 320 a la tienda. Es la cantidad que se sube a SIAL.",
        "Si Posición ≤ Punto Reorden: Nivel Máximo − Posición (o lo vendido desde la ruta "
        "anterior, si es mayor), en múltiplos de la unidad de empaque; si no, 0 (como el reporte: "
        "no se envía con la talla sobre su punto de reorden). Se reparte el Stock en CD por SKU: "
        "si alcanza, cada tienda recibe su necesidad; si no, primero lo vendido, luego quiebres "
        "y luego por prioridad (riesgo de quiebre, venta, prioridad A/B/C, curva rota). Sólo "
        "reciben las tiendas cuyo mall tiene ruta ese día. 0 si el modelo-color está bloqueado "
        "para la tienda (reporte de bloqueos) o su temporada comercial no se repone.",
        "Motor Forusight",
        "Misma regla del reporte (punto de reorden → nivel máximo). Para comparar, filtrar las "
        "mismas tiendas de la ruta del día y sólo «Revision de stock».",
    ),
    "Unidad Empaque Distribución": (
        RESULTADO,
        "Múltiplo de envío (empaque).",
        "Unidad de empaque del maestro de planificación guardado (6, 12 o 24 en accesorios); "
        "si no está, la más frecuente del SKU; si nada, 1. Se envían empaques completos.",
        "config/planificacion_claves.csv.gz",
        REP,
    ),
    "Alcance Posición Stock Actual [semanas]": (
        NIVELES,
        "Semanas que dura el stock actual.",
        "Posición ÷ Pronóstico.",
        "Calculada",
        "Debe coincidir si coinciden Posición y Pronóstico.",
    ),
    "Alcance Posición Stock Final [semanas]": (
        NIVELES,
        "Semanas que dura el stock después del envío.",
        "(Posición + Cantidad Pedida Final) ÷ Pronóstico.",
        "Calculada",
        "Debe coincidir si coinciden Posición, Pronóstico y Cantidad.",
    ),
    "Motivo Forusight": (
        MOTIVO,
        "Explicación en texto de la cantidad de la fila.",
        "Regla que decidió la fila: reposición de la venta desde la ruta anterior, quiebre, "
        "curva rota, reposición al nivel máximo, o por qué no se envía.",
        "Motor Forusight",
        "El reporte trae «Motivo Pendiente Reposición» (Sin Reposición Pendiente, Stock CD, "
        "Almacenamiento): sólo explica lo que no alcanzó a enviar.",
    ),
}

#: Hojas del archivo, para la portada del diccionario.
HOJAS = [
    ("Resumen", "Totales de la corrida y unidades por tienda."),
    (
        "Distribución",
        "Una fila por tienda × SKU de las tiendas en ruta, se envíe o no, con todas las "
        "columnas y el motivo (filtra Cantidad Pedida Final > 0 para ver sólo lo que sale).",
    ),
    (
        "Dinámica",
        "Como la tabla dinámica: filas modelo, color y talla; columnas código de tienda; "
        "valores suma de Cantidad Pedida Final (sólo > 0).",
    ),
    (
        "SIAL",
        "Lo mismo que la Dinámica en valores, sin totales ni formato: el archivo que se sube a SIAL.",
    ),
    ("Diccionario", "Esta hoja: qué es cada columna y cómo se calcula."),
]


def entrada(columna: str) -> tuple[str, str, str, str, str]:
    """Entrada del diccionario; las semanas (encabezado con fecha) comparten una."""
    if columna[:2] == "20" and len(columna) == 10:
        return DICCIONARIO["Semanas (columnas con fecha)"]
    if columna in DICCIONARIO:
        return DICCIONARIO[columna]
    return (seccion(columna), "Columna del reporte de distribución.", REP, "—", REP)


def seccion(columna: str) -> str:
    if columna in DICCIONARIO:
        return DICCIONARIO[columna][0]
    c = columna.lower()
    if columna[:2] == "20" or any(k in c for k in ("venta", "demanda", "vta")):
        return VENTA
    if "motivo" in c:
        return RESULTADO
    if any(k in c for k in ("cantidad", "pendiente", "monto", "requerimiento", "empaque")):
        return RESULTADO
    if any(k in c for k in ("stock", "posición", "trán", "backorder", "comprometido")):
        return STOCK
    if any(
        k in c
        for k in ("pronóstico", "nivel", "punto", "leadtime", "período", "alcance", "clase de")
    ):
        return NIVELES
    if any(k in c for k in ("centro", "zona", "tienda", "almacenamiento")):
        return TIENDA
    return PRODUCTO
