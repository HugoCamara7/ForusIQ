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
from pathlib import Path

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
    _num("Venta desde ruta anterior [un]", ancho=14),
    _num("Venta después del corte [un]", ancho=14),
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
    Col("Motivo Forusight", ancho=90),
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
    # Sólo reposición (Revision de stock): los modelos nuevos para la tienda (carga de pedidos)
    # no son de Forusight y no van en el archivo.
    d = d.loc[~d["es_introduccion"].fillna(False).astype(bool)]
    vr = d["venta_desde_ruta"] if "venta_desde_ruta" in d else pd.Series(0.0, index=d.index)
    activos = (
        (vr.fillna(0) > 0)
        | (d["cantidad"] > 0)
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
    if "leadtime_dias" in tien:  # calendario: lead time y revisión propios de cada tienda
        lead = d["tienda_id"].map(tien["leadtime_dias"]).fillna(lead)
        revision = d["tienda_id"].map(tien["revision_dias"]).fillna(revision)
    post = d["venta_post_corte"] if "venta_post_corte" in d else pd.Series(0.0, index=d.index)
    posicion = (d["stock_tienda"] + d["stock_transito"] - post.fillna(0)).clip(lower=0)
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
            "Grupo Requerimiento": "Revision de stock",
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
    for col, c in (
        ("Venta desde ruta anterior [un]", "venta_desde_ruta"),
        ("Venta después del corte [un]", "venta_post_corte"),
    ):
        if c in d and d[c].fillna(0).gt(0).any():
            out[col] = d[c].fillna(0)

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
    if "motivo_texto" in d:
        out["Motivo Forusight"] = d["motivo_texto"]

    # sólo columnas con dato real (las operativas siempre)
    siempre = {c.nombre for c in COLUMNAS if c.siempre}
    semanas_txt = [s.strftime("%Y-%m-%d") for s in semanas]
    cols = [c for c in out.columns if c in siempre or c in semanas_txt or out[c].notna().any()]
    out = out[cols].sort_values(
        ["Código Centro", "Código Modelo", "Talla"], kind="mergesort", key=lambda s: s.astype(str)
    )
    return out.reset_index(drop=True)


# ------------------------------------------------------------------ Excel estilo Forus

LOGO = Path(__file__).resolve().parents[3] / "assets" / "forus_logo.png"
NAVY, AZUL_FORUS, ACENTO = "#17269A", "#2367FF", "#009FE3"
CEBRA, TINTA, GRIS_TXT = "#F5F7FC", "#0F172A", "#64748B"
Q_COL, P_COL = "Cantidad Pedida Final [un]", "Pendiente Reposición"
FILAS_PRODUCTO = ["Código Modelo", "Modelo", "Código Color", "Talla", "Descripción SKU"]


def resumen_por_tienda(tabla: pd.DataFrame) -> pd.DataFrame:
    """Unidades, SKU y pendiente por tienda (como la dinámica del equipo)."""
    g = [c for c in ("Código Centro", "Nombre Centro", "Centro Comercial") if c in tabla]
    t = tabla.assign(_sku=tabla[Q_COL].gt(0))
    r = (
        t.groupby(g, dropna=False)
        .agg(
            **{
                "Unidades a enviar": (Q_COL, "sum"),
                "SKU con envío": ("_sku", "sum"),
                "Pendiente": (P_COL, "sum"),
            }
        )
        .reset_index()
    )
    return r.sort_values("Unidades a enviar", ascending=False).reset_index(drop=True)


def nombre_archivo(fecha: pd.Timestamp) -> str:
    return f"{pd.Timestamp(fecha):%Y%m%d}_Distribucion_Forusight.xlsx"


def dinamica(tabla: pd.DataFrame) -> pd.DataFrame:
    """Filas producto (modelo, nombre, color, talla, descripción), columnas código de tienda,
    valores suma de Cantidad Pedida Final; sólo cantidades > 0."""
    t = tabla.loc[pd.to_numeric(tabla[Q_COL], errors="coerce").fillna(0) > 0]
    filas = [c for c in FILAS_PRODUCTO if c in t]
    if t.empty:
        return pd.DataFrame(columns=filas)
    t = t.assign(**{c: t[c].astype("string").fillna("") for c in filas})
    piv = t.pivot_table(
        index=filas, columns="Código Centro", values=Q_COL, aggfunc="sum", fill_value=0
    )
    orden = sorted(
        piv.columns, key=lambda c: (not str(c).isdigit(), int(c) if str(c).isdigit() else 0, str(c))
    )
    piv = piv[orden]
    piv.columns = [str(c) for c in piv.columns]
    return piv.reset_index()


def _codigo(c: str):
    return int(c) if str(c).isdigit() else c


class _Libro:
    """Formatos compartidos del libro (cacheados por propiedades)."""

    def __init__(self, wb):
        self.wb = wb
        self._cache: dict = {}

    def f(self, **props):
        clave = tuple(sorted(props.items()))
        if clave not in self._cache:
            self._cache[clave] = self.wb.add_format({"font_name": "Calibri", **props})
        return self._cache[clave]

    def portada(self, ws, titulo: str, subtitulo: str, kpis: list[tuple[str, str]], ancho: int):
        """Logo de Forus, título, subtítulo y fila de indicadores (filas 0 a 4)."""
        ws.hide_gridlines(2)
        ws.set_row(0, 30), ws.set_row(1, 22), ws.set_row(2, 16), ws.set_row(3, 26)
        if LOGO.exists():
            ws.insert_image(
                0, 0, str(LOGO), {"x_scale": 0.085, "y_scale": 0.085, "x_offset": 6, "y_offset": 6}
            )
        ws.write(0, 2, titulo, self.f(bold=True, font_size=18, font_color=NAVY, valign="vcenter"))
        ws.write(1, 2, subtitulo, self.f(font_size=10, font_color=GRIS_TXT, valign="top"))
        col = 2
        for etiqueta, valor in kpis:
            ws.write(2, col, etiqueta.upper(), self.f(font_size=8, bold=True, font_color=GRIS_TXT))
            ws.write(3, col, valor, self.f(font_size=15, bold=True, font_color=AZUL_FORUS))
            col += 2
        ws.set_row(4, 5)
        for j in range(max(ancho, col)):
            ws.write_blank(4, j, None, self.f(bg_color=NAVY))


def _banda(lb: _Libro, ws, fila: int, columnas: list[str]) -> None:
    """Fila de secciones con el color Forus de cada una (tramos contiguos combinados)."""
    from forusight.export.diccionario import COLORES, seccion

    secs = [seccion(c) for c in columnas]
    j = 0
    while j < len(secs):
        k = j
        while k + 1 < len(secs) and secs[k + 1] == secs[j]:
            k += 1
        fmt = lb.f(
            bold=True,
            font_color="#FFFFFF",
            bg_color=COLORES[secs[j]][0],
            align="center",
            valign="vcenter",
            font_size=10,
        )
        if k > j:
            ws.merge_range(fila, j, fila, k, secs[j].upper(), fmt)
        else:
            ws.write(fila, j, secs[j].upper(), fmt)
        j = k + 1


def _encabezado(lb: _Libro, columna: str):
    from forusight.export.diccionario import COLORES, seccion

    _, claro, texto = COLORES[seccion(columna)]
    return lb.f(
        bold=True,
        bg_color=claro,
        font_color=texto,
        text_wrap=True,
        align="center",
        valign="vcenter",
        border=1,
        border_color="#FFFFFF",
        font_size=10,
    )


def _escribir_columna(ws, fila0: int, j: int, col: pd.Series, fmt) -> None:
    if pd.api.types.is_numeric_dtype(col) and not pd.api.types.is_bool_dtype(col):
        v = pd.to_numeric(col, errors="coerce").to_numpy(dtype=float, na_value=np.nan)
        for i in np.flatnonzero(np.isfinite(v)):
            ws.write_number(fila0 + int(i), j, float(v[i]), fmt)
    else:
        vals = col.astype(object).where(col.notna(), None).tolist()
        for i, x in enumerate(vals):
            if x is not None and x != "":
                ws.write(fila0 + i, j, x, fmt)


def _hoja_distribucion(lb: _Libro, tabla: pd.DataFrame, titulo: str, subtitulo: str, kpis):
    ws = lb.wb.add_worksheet("Distribución")
    estilos = {c.nombre: c for c in COLUMNAS}
    cols = list(tabla.columns)
    lb.portada(ws, titulo, subtitulo, kpis, len(cols))
    _banda(lb, ws, 5, cols)
    ws.set_row(6, 62)
    n = len(tabla)
    for j, nombre in enumerate(cols):
        c = estilos.get(nombre, SEMANA if nombre[:2] == "20" else Col(nombre))
        props = {
            "num_format": c.formato,
            "align": "right" if c.derecha else "left",
            "font_size": 10,
            "font_color": TINTA,
        }
        if nombre == Q_COL:
            props.update(bold=True, bg_color="#FFF1CC", font_color="#7A5200", align="center")
        fmt = lb.f(**props)
        ws.set_column(j, j, min(c.ancho, 60) if nombre != "Motivo Forusight" else 90)
        ws.write(6, j, nombre, _encabezado(lb, nombre))
        _escribir_columna(ws, 7, j, tabla[nombre], fmt)
    if n:
        # cebra suave (sin tapar la columna de cantidad)
        jq = cols.index(Q_COL) if Q_COL in cols else -1
        cebra = lb.f(bg_color=CEBRA)
        for a, b in ((0, jq - 1), (jq + 1, len(cols) - 1)) if jq >= 0 else ((0, len(cols) - 1),):
            if b >= a:
                ws.conditional_format(
                    7,
                    a,
                    6 + n,
                    b,
                    {"type": "formula", "criteria": "=MOD(ROW(),2)=0", "format": cebra},
                )
    ws.autofilter(6, 0, 6 + n, len(cols) - 1)
    fijas = next((i + 1 for i, c in enumerate(cols) if c == "Talla"), 1)
    ws.freeze_panes(7, fijas)
    return ws


def _hoja_resumen(lb: _Libro, tabla: pd.DataFrame, titulo: str, subtitulo: str, kpis):
    ws = lb.wb.add_worksheet("Resumen")
    r = resumen_por_tienda(tabla)
    lb.portada(ws, titulo, subtitulo, kpis, max(len(r.columns), 8))
    ws.set_column(0, 0, 16), ws.set_column(1, 1, 34), ws.set_column(2, 2, 26)
    ws.set_column(3, len(r.columns), 16)
    ws.write(6, 0, "Unidades por tienda", lb.f(bold=True, font_size=13, font_color=NAVY))
    enc = lb.f(
        bold=True,
        font_color="#FFFFFF",
        bg_color=NAVY,
        align="center",
        valign="vcenter",
        text_wrap=True,
        border=1,
        border_color="#FFFFFF",
    )
    ws.set_row(7, 30)
    for j, c in enumerate(r.columns):
        ws.write(7, j, c, enc)
    txt, num = lb.f(font_size=10), lb.f(font_size=10, num_format="#,##0")
    for i, fila in enumerate(r.itertuples(index=False), start=8):
        for j, x in enumerate(fila):
            if pd.isna(x):
                continue
            ws.write(i, j, x, num if isinstance(x, int | float | np.integer | np.floating) else txt)
    fin = 7 + len(r)
    tot = lb.f(bold=True, font_color="#FFFFFF", bg_color=AZUL_FORUS, num_format="#,##0")
    ws.write(fin + 1, 0, "Total general", tot)
    for j, c in enumerate(r.columns):
        if j == 0:
            continue
        if pd.api.types.is_numeric_dtype(r[c]):
            ws.write_number(fin + 1, j, float(r[c].sum()), tot)
        else:
            ws.write_blank(fin + 1, j, None, tot)
    if len(r):
        ju = list(r.columns).index("Unidades a enviar")
        ws.conditional_format(
            8, ju, fin, ju, {"type": "data_bar", "bar_color": ACENTO, "bar_solid": True}
        )
        ws.conditional_format(
            8,
            0,
            fin,
            len(r.columns) - 1,
            {"type": "formula", "criteria": "=MOD(ROW(),2)=1", "format": lb.f(bg_color=CEBRA)},
        )
    return ws


def _hoja_dinamica(lb: _Libro, piv: pd.DataFrame, tabla: pd.DataFrame, titulo: str, kpis):
    ws = lb.wb.add_worksheet("Dinámica")
    filas = [c for c in FILAS_PRODUCTO if c in piv]
    tiendas = [c for c in piv.columns if c not in filas]
    lb.portada(
        ws,
        titulo,
        "Suma de Cantidad Pedida Final [un] por producto y tienda",
        kpis,
        len(filas) + len(tiendas) + 1,
    )
    ws.write(5, 0, "Cantidad Pedida Final [un]", lb.f(bold=True, font_size=10, font_color=NAVY))
    ws.write(
        5,
        1,
        "> 0",
        lb.f(bold=True, font_size=10, font_color="#FFFFFF", bg_color=ACENTO, align="center"),
    )
    anchos = {
        "Código Modelo": 17,
        "Modelo": 28,
        "Código Color": 11,
        "Talla": 7,
        "Descripción SKU": 44,
    }
    for j, c in enumerate(filas):
        ws.set_column(j, j, anchos.get(c, 14))
    ws.set_column(len(filas), len(filas) + len(tiendas), 7)
    ws.set_column(len(filas) + len(tiendas), len(filas) + len(tiendas), 11)
    enc = lb.f(
        bold=True,
        font_color="#FFFFFF",
        bg_color=NAVY,
        align="center",
        valign="vcenter",
        border=1,
        border_color="#FFFFFF",
        font_size=10,
    )
    sub = lb.f(
        bold=True,
        font_color=NAVY,
        bg_color="#E3E7FB",
        align="center",
        valign="vcenter",
        border=1,
        border_color="#FFFFFF",
        font_size=10,
        text_wrap=True,
    )
    nombres = (
        tabla.drop_duplicates("Código Centro").set_index("Código Centro")["Nombre Centro"]
        if "Nombre Centro" in tabla
        else pd.Series(dtype=str)
    )
    nombres.index = nombres.index.astype(str)
    ws.write(7, 0, "Suma de Cantidad Pedida Final", enc)
    for j in range(1, len(filas)):
        ws.write_blank(7, j, None, enc)
    if tiendas:
        if len(tiendas) > 1:
            ws.merge_range(7, len(filas), 7, len(filas) + len(tiendas) - 1, "Código Centro", enc)
        else:
            ws.write(7, len(filas), "Código Centro", enc)
    ws.write(7, len(filas) + len(tiendas), "", enc)
    # fila 8: nombre de la tienda (chico); fila 9: encabezados y código de tienda
    ws.set_row(8, 34)
    chico = lb.f(
        font_size=7,
        font_color=GRIS_TXT,
        text_wrap=True,
        align="center",
        valign="bottom",
        bg_color="#E3E7FB",
    )
    for j in range(len(filas)):
        ws.write_blank(8, j, None, chico)
    for k, t in enumerate(tiendas):
        ws.write(8, len(filas) + k, str(nombres.get(str(t), "")), chico)
    ws.write_blank(8, len(filas) + len(tiendas), None, chico)
    for j, c in enumerate(filas):
        ws.write(9, j, c, sub)
    for k, t in enumerate(tiendas):
        ws.write(9, len(filas) + k, _codigo(t), sub)
    ws.write(9, len(filas) + len(tiendas), "Total general", sub)
    # filas: bloques de modelo con color alterno; el código de modelo en negrita
    modelo = piv["Código Modelo"] if "Código Modelo" in piv else pd.Series("", index=piv.index)
    m = modelo.astype(str).to_numpy()
    bloque = pd.Series(np.r_[True, m[1:] != m[:-1]] if len(m) else [], index=piv.index).cumsum()
    for i, (fila, b) in enumerate(zip(piv.itertuples(index=False), bloque, strict=True), start=10):
        fondo = "#FFFFFF" if b % 2 else "#F1F4FB"
        nuevo = i == 10 or bloque.iloc[i - 10] != bloque.iloc[i - 11]
        for j, c in enumerate(filas):
            negrita = c == "Código Modelo" or (c == "Modelo" and nuevo)
            ws.write(
                i,
                j,
                fila[j],
                lb.f(bg_color=fondo, font_size=10, bold=negrita, font_color=TINTA),
            )
        valores = [float(x) for x in fila[len(filas) :]]
        for k, x in enumerate(valores):
            fmt = lb.f(
                bg_color=fondo,
                font_size=10,
                align="center",
                num_format="#,##0",
                font_color="#7A5200" if x > 0 else TINTA,
                bold=x > 0,
            )
            if x > 0:
                ws.write_number(i, len(filas) + k, x, fmt)
            else:
                ws.write_blank(i, len(filas) + k, None, fmt)
        ws.write_number(
            i,
            len(filas) + len(tiendas),
            sum(valores),
            lb.f(
                bg_color="#E3E7FB",
                bold=True,
                font_size=10,
                align="center",
                num_format="#,##0",
                font_color=NAVY,
            ),
        )
    fin = 10 + len(piv)
    tot = lb.f(bold=True, font_color="#FFFFFF", bg_color=NAVY, num_format="#,##0", align="center")
    ws.write(fin, 0, "Total general", lb.f(bold=True, font_color="#FFFFFF", bg_color=NAVY))
    for j in range(1, len(filas)):
        ws.write_blank(fin, j, None, tot)
    for k, t in enumerate(tiendas):
        ws.write_number(fin, len(filas) + k, float(piv[t].sum()), tot)
    ws.write_number(
        fin, len(filas) + len(tiendas), float(piv[tiendas].to_numpy().sum()) if tiendas else 0, tot
    )
    ws.freeze_panes(10, len(filas))
    return ws


def _hoja_sial(lb: _Libro, piv: pd.DataFrame) -> None:
    """Valores planos para subir a SIAL: encabezados de producto + códigos de tienda."""
    ws = lb.wb.add_worksheet("SIAL")
    filas = [c for c in FILAS_PRODUCTO if c in piv]
    tiendas = [c for c in piv.columns if c not in filas]
    ws.write_row(0, 0, filas + [_codigo(t) for t in tiendas])
    for i, fila in enumerate(piv.itertuples(index=False), start=1):
        ws.write_row(i, 0, [str(x) for x in fila[: len(filas)]])
        for k, x in enumerate(fila[len(filas) :]):
            if float(x) > 0:
                ws.write_number(i, len(filas) + k, float(x))


def hoja_diccionario(lb: _Libro, columnas: list[str], titulo: str) -> None:
    from forusight.export.diccionario import COLORES, HOJAS, entrada

    ws = lb.wb.add_worksheet("Diccionario")
    lb.portada(ws, titulo, "Qué es cada columna, cómo se calcula y de dónde sale el dato", [], 7)
    ws.set_column(0, 0, 34), ws.set_column(1, 1, 18), ws.set_column(2, 2, 46)
    ws.set_column(3, 3, 70), ws.set_column(4, 4, 44), ws.set_column(5, 5, 38)
    ws.write(6, 0, "Hojas del archivo", lb.f(bold=True, font_size=13, font_color=NAVY))
    fila = 7
    for hoja, texto in HOJAS:
        ws.write(fila, 0, hoja, lb.f(bold=True, font_color=AZUL_FORUS))
        ws.merge_range(fila, 1, fila, 4, texto, lb.f(text_wrap=True, font_size=10))
        fila += 1
    fila += 1
    ws.write(fila, 0, "Columnas", lb.f(bold=True, font_size=13, font_color=NAVY))
    fila += 1
    enc = lb.f(
        bold=True,
        font_color="#FFFFFF",
        bg_color=NAVY,
        valign="vcenter",
        text_wrap=True,
        border=1,
        border_color="#FFFFFF",
    )
    for j, c in enumerate(
        [
            "Columna",
            "Sección",
            "Qué es",
            "Cómo se calcula",
            "Fuente (BigQuery)",
            "Con el reporte del día",
        ]
    ):
        ws.write(fila, j, c, enc)
    ws.freeze_panes(fila + 1, 1)
    fila += 1
    vistos = set()
    for col in columnas:
        e = entrada(col)
        clave = "semanas" if col[:2] == "20" else col
        if clave in vistos:
            continue
        vistos.add(clave)
        banda, claro, texto = COLORES[e[0]]
        nombre = "Semanas (12 columnas con fecha)" if clave == "semanas" else col
        ws.write(
            fila,
            0,
            nombre,
            lb.f(bold=True, text_wrap=True, valign="top", font_size=10, left=5, left_color=banda),
        )
        ws.write(
            fila,
            1,
            e[0],
            lb.f(bold=True, font_color=texto, bg_color=claro, valign="top", font_size=10),
        )
        for j, x in enumerate(e[1:], start=2):
            ws.write(fila, j, x, lb.f(text_wrap=True, valign="top", font_size=10))
        fila += 1


def _kpis(tabla: pd.DataFrame) -> list[tuple[str, str]]:
    q = pd.to_numeric(tabla.get(Q_COL, pd.Series(dtype=float)), errors="coerce").fillna(0)
    env = tabla.loc[q > 0]
    k = [
        ("Unidades a enviar", f"{int(q.sum()):,}"),
        ("Tiendas", f"{env['Código Centro'].nunique() if len(env) else 0}"),
        ("SKU", f"{env['Código SKU'].nunique() if 'Código SKU' in env and len(env) else 0}"),
    ]
    if P_COL in tabla:
        k.append(
            ("Pendiente", f"{int(pd.to_numeric(tabla[P_COL], errors='coerce').fillna(0).sum()):,}")
        )
    return k


def a_excel_forusight(
    tabla: pd.DataFrame,
    fecha: pd.Timestamp,
    reporte: str = "Distribución CD 320",
) -> bytes:
    """Archivo Forusight con estilo Forus: Resumen, Distribución (filtrada en cantidad > 0),
    Dinámica, SIAL (valores para subir) y Diccionario."""
    import xlsxwriter

    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True, "strings_to_numbers": False})
    lb = _Libro(wb)
    marcas = (
        ", ".join(sorted(tabla["Marca"].dropna().astype(str).unique())) if "Marca" in tabla else ""
    )
    titulo = f"Forusight · {reporte}"
    subtitulo = f"Forus Perú · {pd.Timestamp(fecha):%d/%m/%Y}" + (f" · {marcas}" if marcas else "")
    kpis = _kpis(tabla)
    _hoja_resumen(lb, tabla, titulo, subtitulo, kpis)
    _hoja_distribucion(lb, tabla, titulo, subtitulo, kpis)
    piv = dinamica(tabla)
    _hoja_dinamica(lb, piv, tabla, titulo, kpis)
    _hoja_sial(lb, piv)
    hoja_diccionario(lb, list(tabla.columns), titulo)
    wb.worksheets()[1].activate()
    wb.close()
    return buf.getvalue()


def diccionario_excel(columnas: list[str] | None = None) -> bytes:
    """Excel sólo con el diccionario (todas las columnas si no se indican)."""
    import xlsxwriter

    from forusight.export.diccionario import DICCIONARIO

    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    lb = _Libro(wb)
    cols = columnas or [c for c in DICCIONARIO if c != "Semanas (columnas con fecha)"]
    if columnas is None:  # las semanas van después de Grupo Requerimiento, como en el archivo
        i = cols.index("Demanda Periodo Actual")
        cols = cols[:i] + ["2026-01-05"] + cols[i:]
    hoja_diccionario(lb, cols, "Forusight · Diccionario del archivo")
    wb.close()
    return buf.getvalue()
