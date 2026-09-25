"""Diccionario del archivo Forusight: qué es cada columna, cómo se calcula y de dónde sale.

Una sola fuente para la hoja «Diccionario» del archivo y para el Excel del diccionario.
Cada entrada: (sección, qué es, cómo se calcula, fuente en BigQuery, con el reporte del día).
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

REP = "Copiada del reporte"

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
        "Temporada del producto (p. ej. VERANO 2026).",
        "Tal cual.",
        "ARTI (si la columna existe)",
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
        "Copiado; sólo se dejan las filas (o la parte) de Revision de stock",
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
        "No aplica (el reporte no trae venta diaria)",
    ),
    "Venta después del corte [un]": (
        VENTA,
        "Venta posterior al corte de stock: todavía no está descontada del Stock Físico.",
        "SUMA(venta diaria) con fecha > fecha del corte de stock.",
        "ft_pe_venta_retail + stock_bi · fecha_corte",
        "No aplica",
    ),
    "VTA 2 SEM": (
        VENTA,
        "Venta de las 2 últimas semanas.",
        "Fórmula viva: última semana cerrada + Demanda Periodo Actual.",
        "Calculada en el Excel",
        "Fórmula viva en el Excel",
    ),
    "Pronóstico Demanda [un/semana]": (
        NIVELES,
        "Demanda semanal esperada de la talla en la tienda.",
        "0,5 × (demanda del modelo-color × participación de la talla en la curva) + 0,5 × (venta "
        "12 semanas de la talla ÷ semanas que el modelo estuvo en la tienda). Demanda del modelo "
        "= venta ÷ semanas con exposición, con más peso a las últimas 4 semanas si hay "
        "tendencia.",
        "Motor Forusight sobre ft_pe_venta_retail + stock_bi",
        REP,
    ),
    "Leadtime [días]": (
        NIVELES,
        "Días que tarda el envío del CD a la tienda.",
        "Por tienda; si no está, 3 días.",
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
    "Nivel Máximo [un]": (
        NIVELES,
        "Stock que la talla debe tener para cubrir hasta la próxima ruta.",
        "máx(1 si la tienda vende el modelo; redondeo(d·c + 0,5·√(d·c))), con d = Pronóstico y "
        "c = cobertura en semanas = (Leadtime + Período Revisión) ÷ 7 × factor de la tienda "
        "(prioridad A ×1,25, B ×1, C ×0,9; liquidadoras DH, SE, FB ×0,75). 0 si la tienda "
        "nunca tuvo ni vendió la talla.",
        "Motor Forusight",
        REP,
    ),
    "Stock Mínimo Total": (
        NIVELES,
        "Mínimo de exhibición de la talla.",
        "Mínimo de exhibición si la talla es core; si no, 0.",
        "Parámetros (exhibición)",
        REP,
    ),
    "Código Centro Origen": (
        STOCK,
        "Centro de distribución que envía.",
        "Siempre 320.",
        "Secrets · cd_id",
        REP,
    ),
    "Stock en CD": (
        STOCK,
        "Stock del CD 320 que se puede repartir para ese SKU.",
        "stock_bi · disponible del CD 320 (ya sin reservas de pedidos, retail, wholesale, "
        "multicanal y e-commerce) − envíos aprobados en los últimos 3 días que aún no llegan. "
        "La suma enviada a todas las tiendas nunca supera este valor.",
        "stock_bi · disponible (codigo_tienda = 320, último fecha_corte de este año)",
        "Copiado; se le restan los envíos aprobados aún no recibidos",
    ),
    "Stock Físico [un]": (
        STOCK,
        "Stock de la tienda al cierre del último corte.",
        "stock_tiendas del último corte de este año (el del año pasado se descarta).",
        "stock_bi · stock_tiendas",
        REP,
    ),
    "Stock Trán. Int. [un]": (
        STOCK,
        "Unidades en camino a la tienda.",
        "stock_bi · transito + envíos aprobados en los últimos 3 días aún no recibidos.",
        "stock_bi · transito + aprobaciones guardadas",
        "Copiado + envíos aprobados aún no recibidos",
    ),
    "Posición Stock [un]": (
        STOCK,
        "Lo que la tienda tiene y lo que ya viene en camino.",
        "Stock Físico + Stock Trán. Int. − Venta después del corte (mínimo 0).",
        "Calculada",
        "Físico + Trán. Int. + Trán. Prov. − Comprometido − Backorder",
    ),
    "Cantidad Pedida Final [un]": (
        RESULTADO,
        "Unidades a enviar del CD 320 a la tienda. Es la cantidad que se sube a SIAL.",
        "Necesidad = máx(Nivel Máximo − Posición; Venta desde ruta anterior), en empaques "
        "completos. Se reparte el Stock en CD por SKU: si alcanza, cada tienda recibe su "
        "necesidad; si no, primero quiebres y luego por prioridad (riesgo de quiebre, venta, "
        "prioridad A/B/C de la tienda, curva rota). Sólo reciben las tiendas cuyo mall tiene "
        "ruta ese día. 0 si la talla nunca se tuvo ni vendió, si el modelo se agotó sin venta "
        "en 4 semanas o si el modelo es nuevo para la tienda.",
        "Motor Forusight",
        "Misma regla del reporte (punto de reorden → nivel máximo); se recalcula",
    ),
    "Pendiente Reposición": (
        RESULTADO,
        "Necesidad que no se pudo enviar.",
        "Necesidad − Cantidad Pedida Final.",
        "Calculada",
        "Necesidad − Cantidad",
    ),
    "Motivo Pendiente Reposición": (
        RESULTADO,
        "Por qué quedó pendiente.",
        "Sin Reposición Pendiente (se envió todo) · Stock CD (el CD no alcanzó) · "
        "Almacenamiento (tope de la tienda).",
        "Calculado",
        "Sin Reposición Pendiente · Stock CD · Distribución · Almacenamiento",
    ),
    "Unidad Empaque Distribución": (
        RESULTADO,
        "Múltiplo de envío.",
        "1 (se envía por unidad).",
        "Parámetros (asignación)",
        REP,
    ),
    "Alcance Posición Stock Actual [semanas]": (
        NIVELES,
        "Semanas que dura el stock actual.",
        "Posición ÷ Pronóstico.",
        "Calculada",
        "Posición ÷ Pronóstico",
    ),
    "Alcance Posición Stock Final [semanas]": (
        NIVELES,
        "Semanas que dura el stock después del envío.",
        "(Posición + Cantidad Pedida Final) ÷ Pronóstico.",
        "Calculada",
        "(Posición + Cantidad) ÷ Pronóstico",
    ),
    "Motivo Forusight": (
        MOTIVO,
        "Explicación en texto de la cantidad de la fila.",
        "Regla que decidió la fila: reposición de la venta desde la ruta anterior, quiebre, "
        "curva rota, reposición al nivel máximo, o por qué no se envía.",
        "Motor Forusight",
        "Motor Forusight",
    ),
}

#: Hojas del archivo, para la portada del diccionario.
HOJAS = [
    ("Resumen", "Totales de la corrida y unidades por tienda."),
    (
        "Distribución",
        "Una fila por tienda × SKU con todas las columnas. Filtrada en Cantidad Pedida Final "
        "> 0 (quita el filtro para ver las filas sin envío).",
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
