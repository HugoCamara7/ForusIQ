"""Archivo de distribución con el formato operativo de distribución de Forus.

Referencia: reporte "534 - Sugerido de Distribución (Extendido)" (Hoja1): cabecera de 6
filas (Empresa / Reporte / Fecha), encabezado en la fila 7 con autofiltro y una fila por
SKU × tienda evaluada. Nombres, orden, colores, formatos numéricos y anchos de columna son
los del archivo original.

Versión RESUMIDA: sólo se escriben las columnas que Forusight tiene con dato real; las que
no tienen fuente (costos, clases de demanda, backorder, …) se omiten en vez de inventarse.
Filas: todo lo evaluado con actividad (stock, venta, envío o pendiente).
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
import pandas as pd

from forusight.config.settings import EngineParams

AZUL = "#084B8A"
GRIS = "#E6E6E6"


@dataclass(frozen=True)
class Col:
    nombre: str
    relleno: str = GRIS
    formato: str = "General"
    ancho: float = 12
    derecha: bool = False
    rojo: bool = False
    siempre: bool = False  # se escribe aunque venga vacía (columnas operativas)


def _txt(nombre, ancho=12):
    return Col(nombre, ancho=ancho)


def _num(nombre, formato="#,##0", relleno=GRIS, ancho=12, siempre=False):
    return Col(nombre, relleno, formato, ancho, True, siempre=siempre)


#: Columnas del reporte original, en su orden, con su estilo (Hoja1, fila 8).
COLUMNAS: list[Col] = [
    _txt("Código SKU", 40),
    _txt("Modelo"),
    _txt("Color", 25),
    _txt("Código Modelo", 15),
    _txt("Código Color", 10),
    _txt("Talla", 5),
    _txt("Descripción SKU", 80),
    _txt("Clase", 15),
    _txt("Marca", 20),
    _txt("Género"),
    _txt("Prenda", 30),
    _txt("Temporada comercial"),
    _txt("Código Centro", 15),
    _txt("Nombre Centro", 50),
    _txt("Centro Comercial", 30),
    _txt("Zona CC"),
    _txt("Código Grupo Planificación", 15),
    _txt("Grupo Requerimiento", 20),
    # 12 semanas: se insertan dinámicamente (relleno #A4C4C4, formato #,##0)
    _num("Demanda Periodo Actual", ancho=15),
    _num("Pronóstico Demanda [un/semana]", "#,##0.00", "#87A6C4"),
    _num("Leadtime [días]", ancho=10),
    _num("Período Revisión [días]", "#,##0.0"),
    _num("Nivel Máximo [un]", "#,##0.0", "#C88946"),
    _num("Stock Mínimo Total"),
    Col("Código Centro Origen", ancho=15, siempre=True),
    _num("Stock en CD", ancho=10, siempre=True),
    _num("Stock Físico [un]", "#,##0.0", "#C4A687", siempre=True),
    _num("Stock Trán. Int. [un]", "#,##0.0", "#C4A687"),
    _num("Posición Stock [un]", "#,##0.0", "#C4A687", siempre=True),
    Col("Cantidad Pedida Final [un]", "#C2C567", "#,##0", 10, True, rojo=True, siempre=True),
    _num("Pendiente Reposición", "#,##0.0", ancho=15, siempre=True),
    Col("Motivo Pendiente Reposición", ancho=40, siempre=True),
    _num("Unidad Empaque Distribución", ancho=15),
    _num("Alcance Posición Stock Actual [semanas]", "#,##0.0"),
    _num("Alcance Posición Stock Final [semanas]", "#,##0.0"),
]
SEMANA = Col("", "#A4C4C4", "#,##0", 12, True)
GRUPO_PLANIFICACION = {"VESTUARIO": "VST", "CALZADO": "CLZ", "ACCESORIOS": "ACC"}
SIN_PENDIENTE = "Sin Reposición Pendiente"


def _vacio_a_na(s: pd.Series) -> pd.Series:
    return s.where(~s.isin(["SIN_CATEGORIA", "SIN_GENERO", "SIN_RANGO", ""]), pd.NA)


def construir_tabla(
    detalle: pd.DataFrame,
    ventas: pd.DataFrame,
    dim_producto: pd.DataFrame,
    dim_tienda: pd.DataFrame,
    params: EngineParams,
    fecha_corte: pd.Timestamp,
    cd_id: str = "320",
    cantidad: pd.Series | None = None,
) -> pd.DataFrame:
    """Detalle del motor → tabla con columnas del archivo (sólo las que tienen dato)."""
    d = detalle.copy()
    if cantidad is not None:
        d["cantidad"] = cantidad.reindex(d.index).fillna(0).astype(int)
    d["pendiente"] = (d["necesidad"] - d["cantidad"]).clip(lower=0)
    activos = (
        (d["cantidad"] > 0)
        | (d["pendiente"] > 0)
        | (d["stock_tienda"] > 0)
        | (d["venta_12s"] > 0)
        | (d["stock_transito"] > 0)
    )
    d = d.loc[activos]

    prod = dim_producto.drop_duplicates("sku").set_index("sku")
    tien = dim_tienda.drop_duplicates("tienda_id").set_index("tienda_id")

    def p(c: str) -> pd.Series:
        s = d["sku"].map(prod[c]) if c in prod else pd.Series(pd.NA, index=d.index)
        return s.astype("string")

    def t(c: str) -> pd.Series:
        s = d["tienda_id"].map(tien[c]) if c in tien else pd.Series(pd.NA, index=d.index)
        return s.astype("string")

    clase = _vacio_a_na(d["categoria"])
    marca, desc, color = p("marca"), p("descripcion"), p("color")
    talla = d["talla"].astype("string")
    descripcion_sku = (marca + " " + desc + " " + color.fillna("") + " " + talla).where(
        marca.notna() & desc.notna()
    )
    cob = params.cobertura
    lead = d["categoria"].map(
        lambda c: (
            (
                cob.por_categoria.get(c).lead_time_semanas
                if c in cob.por_categoria and cob.por_categoria[c].lead_time_semanas is not None
                else cob.lead_time_semanas
            )
            * 7
        )
    )
    revision = d["categoria"].map(
        lambda c: (
            (
                cob.por_categoria.get(c).ciclo_revision_semanas
                if c in cob.por_categoria
                and cob.por_categoria[c].ciclo_revision_semanas is not None
                else cob.ciclo_revision_semanas
            )
            * 7
        )
    )
    posicion = d["stock_tienda"] + d["stock_transito"]
    fc = d["demanda_semanal"].where(d["demanda_semanal"] > 0)
    almacenamiento = d["motivo_codigo"].eq("NO_TOPE_TIENDA") | d["motivo_parcial"].eq(
        "PARCIAL_TOPE"
    )

    out = pd.DataFrame(
        {
            "Código SKU": d["sku"],
            "Modelo": desc,
            "Color": color,
            "Código Modelo": d["modelo_id"],
            "Código Color": p("cod_color"),
            "Talla": d["talla"],
            "Descripción SKU": descripcion_sku,
            "Clase": clase,
            "Marca": marca,
            "Género": _vacio_a_na(d["genero"]),
            "Prenda": p("prenda"),
            "Temporada comercial": p("temporada"),
            "Código Centro": d["tienda_id"],
            "Nombre Centro": t("nombre"),
            "Centro Comercial": t("centro_comercial"),
            "Zona CC": t("zona"),
            "Código Grupo Planificación": clase.map(GRUPO_PLANIFICACION),
            "Grupo Requerimiento": np.where(
                d["es_introduccion"], "Carga Pedidos", "Revision de stock"
            ),
        },
        index=d.index,
    )

    # 12 semanas cerradas (encabezado = lunes de la semana) + semana en curso
    corte = pd.Timestamp(fecha_corte).normalize()
    v = ventas.copy()
    v["semana_inicio"] = pd.to_datetime(v["semana_inicio"])
    semanas = [
        corte - pd.Timedelta(weeks=k) for k in range(params.horizonte.semanas_analisis, 0, -1)
    ]
    piv = v.loc[v["semana_inicio"].isin(semanas)].pivot_table(
        index=["tienda_id", "sku"], columns="semana_inicio", values="unidades", aggfunc="sum"
    )
    llave = pd.MultiIndex.from_arrays([d["tienda_id"], d["sku"]])
    for s in semanas:
        col = piv[s].reindex(llave).to_numpy() if s in piv else np.full(len(d), np.nan)
        out[s.strftime("%Y-%m-%d")] = np.nan_to_num(col, nan=0.0)
    actual = v.loc[v["semana_inicio"] == corte].groupby(["tienda_id", "sku"])["unidades"].sum()
    out["Demanda Periodo Actual"] = actual.reindex(llave).fillna(0).to_numpy()

    out["Pronóstico Demanda [un/semana]"] = d["demanda_semanal"].round(6)
    out["Leadtime [días]"] = lead
    out["Período Revisión [días]"] = revision
    out["Nivel Máximo [un]"] = d["stock_objetivo"]
    out["Stock Mínimo Total"] = d["minimo_exhibicion"].where(d["es_core"], 0)
    out["Código Centro Origen"] = str(cd_id)
    out["Stock en CD"] = d["stock_cd_disponible"]
    out["Stock Físico [un]"] = d["stock_tienda"]
    if d["stock_transito"].gt(0).any():  # la fuente de stock actual no trae tránsito
        out["Stock Trán. Int. [un]"] = d["stock_transito"]
    out["Posición Stock [un]"] = posicion
    out["Cantidad Pedida Final [un]"] = d["cantidad"]
    out["Pendiente Reposición"] = d["pendiente"]
    out["Motivo Pendiente Reposición"] = np.where(
        d["pendiente"] > 0, np.where(almacenamiento, "Almacenamiento", "Stock CD"), SIN_PENDIENTE
    )
    out["Unidad Empaque Distribución"] = params.asignacion.multiplo_envio
    out["Alcance Posición Stock Actual [semanas]"] = posicion / fc
    out["Alcance Posición Stock Final [semanas]"] = (posicion + d["cantidad"]) / fc

    # sólo columnas con dato real (las operativas siempre)
    siempre = {c.nombre for c in COLUMNAS if c.siempre}
    semanas_txt = [s.strftime("%Y-%m-%d") for s in semanas]
    cols = [c for c in out.columns if c in siempre or c in semanas_txt or out[c].notna().any()]
    out = out[cols].sort_values(
        ["Código Centro", "Código Modelo", "Talla"], kind="mergesort", key=lambda s: s.astype(str)
    )
    return out.reset_index(drop=True)


def resumen_por_tienda(tabla: pd.DataFrame) -> pd.DataFrame:
    """Como la dinámica que el equipo arma sobre el reporte (Hoja4)."""
    g = ["Nombre Centro", "Código Centro"] if "Nombre Centro" in tabla else ["Código Centro"]
    r = (
        tabla.groupby(g, dropna=False)
        .agg(
            **{
                "Suma de Cantidad Pedida Final [un]": ("Cantidad Pedida Final [un]", "sum"),
                "Suma de Pendiente Reposición": ("Pendiente Reposición", "sum"),
            }
        )
        .reset_index()
    )
    return r.sort_values("Suma de Cantidad Pedida Final [un]", ascending=False)


def nombre_archivo(fecha: pd.Timestamp) -> str:
    return f"{pd.Timestamp(fecha):%Y%m%d}_Distribucion_Forusight.xlsx"


def a_excel_forusight(
    tabla: pd.DataFrame,
    fecha: pd.Timestamp,
    reporte: str = "Archivo Forusight · Distribución CD 320",
) -> bytes:
    import xlsxwriter

    estilos = {c.nombre: c for c in COLUMNAS}
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True, "strings_to_numbers": False})
    ws = wb.add_worksheet("Hoja1")
    cab = wb.add_format({"font_name": "Calibri", "font_size": 11})
    enc = wb.add_format(
        {
            "bold": True,
            "font_color": "#FFFFFF",
            "bg_color": AZUL,
            "align": "center",
            "valign": "vcenter",
            "text_wrap": True,
            "font_name": "Calibri",
        }
    )
    ws.set_row(0, 36)
    ws.write(2, 0, "Empresa:", cab), ws.write(2, 1, "Forus Peru", cab)
    ws.write(3, 0, "Reporte:", cab), ws.write(3, 1, reporte, cab)
    ws.write(4, 0, "Fecha:", cab), ws.write(4, 1, f"{pd.Timestamp(fecha):%d/%m/%Y}", cab)
    ws.set_row(6, 76.05)
    for j, nombre in enumerate(tabla.columns):
        c = estilos.get(nombre, SEMANA if nombre[:2] == "20" else Col(nombre))
        fmt = wb.add_format(
            {
                "bg_color": c.relleno,
                "num_format": c.formato,
                "align": "right" if c.derecha else "left",
                "font_name": "Calibri",
                **({"font_color": "#FF0000"} if c.rojo else {}),
            }
        )
        # El formato de columna pinta también las celdas vacías: sólo se escriben valores.
        ws.set_column(j, j, c.ancho, fmt)
        ws.write(6, j, nombre, enc)
        col = tabla[nombre]
        if pd.api.types.is_numeric_dtype(col) and not pd.api.types.is_bool_dtype(col):
            v = pd.to_numeric(col, errors="coerce").to_numpy(dtype=float, na_value=np.nan)
            escribir = ws.write_number
            filas = np.flatnonzero(np.isfinite(v))
            valores = v[filas].tolist()
        else:
            v = col.astype(object).where(col.notna(), None).tolist()
            escribir = ws.write
            filas = [i for i, x in enumerate(v) if x is not None and x != ""]
            valores = [v[i] for i in filas]
        for i, x in zip(filas, valores, strict=True):
            escribir(7 + int(i), j, x, fmt)
    ws.autofilter(6, 0, 6 + len(tabla), len(tabla.columns) - 1)

    rs = wb.add_worksheet("Resumen")
    r = resumen_por_tienda(tabla)
    rs.write_row(0, 0, list(r.columns), enc)
    for i, fila in enumerate(r.itertuples(index=False), start=1):
        rs.write_row(i, 0, [None if pd.isna(x) else x for x in fila])
    rs.set_column(0, 0, 30), rs.set_column(1, len(r.columns), 18)
    wb.close()
    return buf.getvalue()
