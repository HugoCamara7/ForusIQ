"""Excel de disponibilidad por tienda: el detalle del velocímetro «Disponibilidad del retail».

Usa los SKU activos de TODO el retail que guarda la corrida (``EngineResult.disponibilidad``),
así la suma de las tiendas da exactamente el valor del velocímetro. Hojas:

* «Por tienda»: SKU activos, disponibles, quiebres y disponibilidad (hoy, con tránsito,
  después del envío y ponderada por venta) de cada tienda, con el total del retail arriba;
* «Tallas en quiebre»: cada SKU activo (vendió en 8 semanas) sin stock en la tienda que el CD
  sí tiene, y si hoy se envía;
* «Cómo se calcula»: las definiciones.
"""

from __future__ import annotations

import io

import pandas as pd

from forusight.engine import disponibilidad as DISP

#: Mismos umbrales que el velocímetro: crítica < 90 %, en alerta 90–93 %, óptima ≥ 93 %.
UMBRAL_ALERTA, UMBRAL_OPTIMA = 0.90, 0.93


def estado(v: float) -> str:
    if pd.isna(v):
        return ""
    return "Óptima" if v >= UMBRAL_OPTIMA else "En alerta" if v >= UMBRAL_ALERTA else "Crítica"


def skus_de(res) -> tuple[pd.DataFrame, bool]:
    """(SKU activos, es_todo_el_retail). Corridas sin el dato (reporte del día o anteriores):
    se calcula con el detalle, que sólo trae las tiendas de la ruta de hoy."""
    skus = getattr(res, "disponibilidad", None)
    if skus is not None:
        return skus, True
    return DISP.sku_activos(res.detalle), False


def tablas(res, tiendas: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame, bool]:
    """(por tienda, tallas en quiebre, es_todo_el_retail) con nombres de columna del Excel.

    ``tiendas``: tienda_id, nombre, cadena y opcionalmente recibe_hoy."""
    skus, completo = skus_de(res)
    t = DISP.por_tienda_kpis(skus)
    info = pd.DataFrame(columns=["tienda_id", "nombre", "cadena", "recibe_hoy"])
    if tiendas is not None and len(tiendas):
        info = tiendas.drop_duplicates("tienda_id").reindex(columns=info.columns)
    info = info.assign(tienda_id=info["tienda_id"].astype(str)).set_index("tienda_id")
    tid = t["tienda_id"].astype(str)
    hoy = tid.map(info["recibe_hoy"]) if len(info) else pd.Series(pd.NA, index=t.index)
    por = pd.DataFrame(
        {
            "Cod Tienda": tid,
            "Tienda": tid.map(info["nombre"]).fillna(""),
            "Cadena": tid.map(info["cadena"]).fillna(""),
            "Repone hoy": hoy.map({True: "Sí", False: "No"}).fillna(""),
            "SKU activos": t["activos"].astype(int),
            "SKU disponibles": t["disponibles"].astype(int),
            "Tallas en quiebre": t["quiebres"].astype(int),
            "Quiebres que se reponen hoy": t["quiebres_se_reponen"].astype(int),
            "Disponibilidad": t["disp_antes"],
            "Con tránsito": t["disp_transito"],
            "Después del envío": t["disp_despues"],
            "Ponderada por venta": t["disp_ponderada"],
            "Semanas de cobertura": t["wos"].round(1),
        }
    )
    por["Estado"] = por["Disponibilidad"].map(estado)
    por = por.sort_values(["Disponibilidad", "SKU activos"], ascending=[True, False])
    if len(por):
        tot_a, tot_ok = por["SKU activos"].sum(), por["SKU disponibles"].sum()
        k = DISP.kpis_skus(skus)
        total = {
            "Cod Tienda": "",
            "Tienda": "TOTAL RETAIL" if completo else "TOTAL TIENDAS DE HOY",
            "Cadena": "",
            "Repone hoy": "",
            "SKU activos": int(tot_a),
            "SKU disponibles": int(tot_ok),
            "Tallas en quiebre": int(por["Tallas en quiebre"].sum()),
            "Quiebres que se reponen hoy": int(por["Quiebres que se reponen hoy"].sum()),
            "Disponibilidad": tot_ok / tot_a,
            "Con tránsito": k["con_transito_antes"],
            "Después del envío": k["simple_despues"],
            "Ponderada por venta": k["ponderada_antes"],
            "Semanas de cobertura": round(k["wos"], 1) if pd.notna(k["wos"]) else None,
        }
        total["Estado"] = estado(total["Disponibilidad"])
        por = pd.concat([pd.DataFrame([total]), por], ignore_index=True)

    q = DISP.quiebres(skus)
    qid = q["tienda_id"].astype(str)
    quie = pd.DataFrame(
        {
            "Cod Tienda": qid,
            "Tienda": qid.map(info["nombre"]).fillna(""),
            "Modelo-color": q["modelo_color_id"],
            "SKU": q["sku"],
            "Talla": q["talla"],
            "Stock tienda": q["stock_tienda"].fillna(0).astype(int),
            "Tránsito": q["stock_transito"].fillna(0).astype(int),
            "Stock CD": q["stock_cd_disponible"].fillna(0).astype(int),
            "Venta 8S": q["venta_8s"].fillna(0).astype(int),
            "Se envía hoy": q["cantidad"].fillna(0).astype(int),
        }
    )
    quie = quie.assign(_n=pd.to_numeric(quie["Cod Tienda"], errors="coerce")).sort_values(
        ["_n", "Venta 8S"], ascending=[True, False]
    )
    return por.reset_index(drop=True), quie.drop(columns="_n").reset_index(drop=True), completo


DEFINICIONES = [
    (
        "SKU activo",
        "Tienda × SKU (talla) que vendió en las últimas 8 semanas cerradas y que se repone en "
        "esa tienda (no bloqueado ni de temporada que no se repone). Un SKU sin venta en 8 "
        "semanas no se mide, aunque tenga stock.",
    ),
    (
        "SKU disponible",
        "SKU activo con stock en la tienda (> 0), o que el CD tampoco tiene (no se puede "
        "reponer, así que no resta).",
    ),
    (
        "Disponibilidad",
        "SKU disponibles / SKU activos. Es el valor del velocímetro; el total "
        "suma todas las tiendas.",
    ),
    ("Con tránsito", "Cuenta además la talla que ya va en camino (pedidos en tránsito)."),
    ("Después del envío", "Disponibilidad si se recibe lo que propone esta corrida."),
    ("Ponderada por venta", "Cada SKU pesa por su venta de 8 semanas."),
    ("Tallas en quiebre", "SKU activos sin stock en la tienda que el CD sí tiene."),
    ("Semanas de cobertura", "(Stock + tránsito) / demanda semanal estimada."),
    ("Estado", "Óptima ≥ 93 %, En alerta 90–93 %, Crítica < 90 % (como el velocímetro)."),
]


def a_excel(res, tiendas: pd.DataFrame | None = None, fecha: str = "") -> bytes:
    import xlsxwriter

    por, quie, completo = tablas(res, tiendas)
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True, "nan_inf_to_errors": True})
    cab = wb.add_format(
        {
            "bold": True,
            "bg_color": "#1F3A93",
            "font_color": "#FFFFFF",
            "text_wrap": True,
            "valign": "vcenter",
        }
    )
    pct = wb.add_format({"num_format": "0.0%"})
    pct_tot = wb.add_format({"num_format": "0.0%", "bold": True, "bg_color": "#E8EEFB"})
    tot = wb.add_format({"bold": True, "bg_color": "#E8EEFB"})
    titulo = wb.add_format({"bold": True, "font_size": 13})
    colores = {
        "Óptima": wb.add_format({"font_color": "#16A34A", "bold": True}),
        "En alerta": wb.add_format({"font_color": "#B45309", "bold": True}),
        "Crítica": wb.add_format({"font_color": "#DC2626", "bold": True}),
    }
    porcentajes = {"Disponibilidad", "Con tránsito", "Después del envío", "Ponderada por venta"}

    ws = wb.add_worksheet("Por tienda")
    alcance = "todo el retail" if completo else "sólo las tiendas de la ruta de hoy"
    ws.write(
        0, 0, f"Disponibilidad por tienda · corrida {res.run_id} · {fecha} · {alcance}", titulo
    )
    ws.write_row(2, 0, list(por.columns), cab)
    ws.set_row(2, 30)
    for i, fila in enumerate(por.itertuples(index=False), start=3):
        es_total = i == 3 and str(fila[1]).startswith("TOTAL")
        for j, (col, v) in enumerate(zip(por.columns, fila, strict=True)):
            if col in porcentajes:
                fmt = pct_tot if es_total else pct
            elif col == "Estado":
                fmt = colores.get(v)
            else:
                fmt = tot if es_total else None
            if pd.isna(v) if not isinstance(v, str) else False:
                ws.write_blank(i, j, None, fmt)
            else:
                ws.write(i, j, v, fmt)
    ws.autofilter(2, 0, 2 + len(por), len(por.columns) - 1)
    ws.freeze_panes(3, 2)
    for j, ancho in enumerate([9, 26, 8, 9, 10, 11, 10, 12, 13, 12, 12, 12, 12, 11]):
        ws.set_column(j, j, ancho)

    wq = wb.add_worksheet("Tallas en quiebre")
    wq.write_row(0, 0, list(quie.columns), cab)
    for i, fila in enumerate(quie.itertuples(index=False, name=None), start=1):
        wq.write_row(i, 0, [x if isinstance(x, int | float) else str(x) for x in fila])
    wq.autofilter(0, 0, max(len(quie), 1), len(quie.columns) - 1)
    wq.freeze_panes(1, 0)
    for j, ancho in enumerate([9, 26, 22, 16, 7, 11, 9, 9, 10, 12]):
        wq.set_column(j, j, ancho)

    wd = wb.add_worksheet("Cómo se calcula")
    wd.write_row(0, 0, ["Concepto", "Definición"], cab)
    for i, (k, v) in enumerate(DEFINICIONES, start=1):
        wd.write_row(i, 0, [k, v])
    wd.set_column(0, 0, 24)
    wd.set_column(1, 1, 100)
    wb.close()
    return buf.getvalue()
