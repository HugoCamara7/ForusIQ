import pandas as pd
import streamlit as st
from app.components.estado import entradas_de_la_corrida
from app.components.graficos import barras_apiladas_100, barras_ranking, gauge
from app.components.ui import chips, hero, html, issue_box, kpi_row, mini_tabla, section, stepper

from forusight.engine.reasons import DESCRIPCION_CODIGOS

ss = st.session_state
res = ss.get("resultado")
diag = ss.get("diagnostico") or {}
FLUJO = [
    ("BigQuery", "venta 12 sem + stock"),
    ("Necesidad", "demanda, curva, afinidad"),
    ("Distribución CD 320", "stock limitado"),
    ("Match maestros", "tienda · cadena · marca"),
    ("Archivo Neogística", "exportación"),
]
ESTADOS = {
    "Tiene y vende": "#16A34A",
    "Tiene sin venta": "#D97706",
    "Quiebre": "#DC2626",
    "Nunca tuvo": "#94A3B8",
}
NOMBRE_ESTADO = {
    "TUVO_Y_VENDE": "Tiene y vende",
    "TUVO_SIN_VENTA": "Tiene sin venta",
    "QUIEBRE": "Quiebre",
    "NUNCA_TUVO": "Nunca tuvo",
}

if res is None:
    hero(
        "Forusight",
        "Sugerido de distribución del CD 320 a tiendas, por modelo, talla y "
        "cantidad, con el motivo de cada envío.",
        eyebrow="Dashboard",
    )
    stepper(FLUJO, 1)
    issue_box(
        "info",
        "Todavía no hay una corrida",
        "Elige la marca y la semana en la barra lateral y presiona «Ejecutar corrida».",
    )
    st.stop()

det = res.detalle
r = res.resumen
entradas = entradas_de_la_corrida()
tiendas = entradas.dim_tienda if entradas is not None else pd.DataFrame(columns=["tienda_id"])
cols_t = [c for c in ("tienda_id", "nombre", "cadena") if c in tiendas.columns]
det = det.merge(tiendas[cols_t].drop_duplicates("tienda_id"), on="tienda_id", how="left")
det["tienda"] = det.get("nombre", pd.Series(index=det.index, dtype="string")).fillna(
    det["tienda_id"].astype("string")
)
tiene_cadena = "cadena" in det.columns and det["cadena"].notna().any()

meta = [f"Corte {r['fecha_corte']}", f"Corrida {r['run_id']}"]
if diag.get("fecha_foto"):
    meta.append(f"Stock al {diag['fecha_foto']}")
if diag.get("marcas"):
    meta.append(" · ".join(diag["marcas"]))
hero(
    "Distribución CD " + str(r["cd_id"]),
    f"{r['unidades_a_distribuir']:,} unidades sugeridas para {r['tiendas_con_envio']} tiendas "
    f"a partir de {r['filas_evaluadas']:,} combinaciones tienda × talla evaluadas.",
    eyebrow="Dashboard",
    meta=meta,
)
stepper(FLUJO, 5 if ss.get("aprobacion") else 4)

quiebres = det.loc[det["estado_mc"] == "QUIEBRE", ["tienda_id", "modelo_color_id"]]
kpi_row(
    [
        (
            "Unidades a distribuir",
            f"{r['unidades_a_distribuir']:,}",
            f"de {r['necesidad_total']:,} de necesidad",
            "truck",
        ),
        (
            "Tiendas con envío",
            f"{r['tiendas_con_envio']}",
            f"{det['tienda_id'].nunique()} tiendas evaluadas",
            "store",
        ),
        (
            "Modelos-color",
            f"{det.loc[det['cantidad'] > 0, 'modelo_color_id'].nunique():,}",
            "con al menos un envío",
            "layers",
        ),
        (
            f"Stock CD {r['cd_id']}",
            f"{r['stock_cd_disponible']:,}",
            "unidades disponibles",
            "package",
        ),
        ("En quiebre", f"{len(quiebres.drop_duplicates()):,}", "tienda × modelo-color", "flame"),
    ]
)

g1, g2, g3 = st.columns([1, 1.35, 1.35], gap="medium")
with g1:
    uso = r["unidades_a_distribuir"] / r["stock_cd_disponible"] if r["stock_cd_disponible"] else 0
    html(
        f'<div class="gauge-card">{gauge(r["fill_rate"], "cobertura de necesidad")}'
        '<div class="gauge-txt"><b>Necesidad cubierta</b>'
        "<span>unidades asignadas ÷ necesidad total</span></div></div>"
    )
    html(
        f'<div class="gauge-card" style="margin-top:12px">{gauge(uso, "del stock del CD")}'
        '<div class="gauge-txt"><b>Uso del stock CD</b>'
        "<span>lo que sale ÷ lo disponible</span></div></div>"
    )
with g2, st.container(key="card_tiendas"):
    section("Unidades por tienda", "Las 12 tiendas que más reciben", "store")
    por_t = det.groupby("tienda", as_index=False)["cantidad"].sum()
    por_t = por_t[por_t["cantidad"] > 0]
    if len(por_t):
        st.altair_chart(barras_ranking(por_t, "tienda", "cantidad"), width="stretch")
    else:
        st.caption("Ninguna tienda recibe en esta corrida.")
with g3, st.container(key="card_cadenas"):
    dim, titulo = (
        ("cadena", "Unidades por cadena")
        if tiene_cadena
        else ("categoria", "Unidades por categoría")
    )
    section(titulo, "Cómo se reparte el envío", "grid")
    por_c = det.groupby(dim, as_index=False)["cantidad"].sum()
    por_c = por_c[por_c["cantidad"] > 0]
    if len(por_c):
        st.altair_chart(barras_ranking(por_c, dim, "cantidad"), width="stretch")

with st.container(key="card_estados"):
    grupo = "cadena" if tiene_cadena else "categoria"
    section(
        "Disponibilidad en tienda",
        f"Estado de cada tienda × modelo-color, por {grupo} (distingue falta de venta de "
        "falta de stock)",
        "target",
    )
    mc = det.drop_duplicates(["tienda_id", "modelo_color_id"]).assign(
        estado=lambda d: d["estado_mc"].map(NOMBRE_ESTADO), n=1
    )
    tot = mc["estado"].value_counts()
    chips(
        [
            ("ok", f"Tiene y vende · {tot.get('Tiene y vende', 0):,}"),
            ("warn", f"Tiene sin venta · {tot.get('Tiene sin venta', 0):,}"),
            ("err", f"Quiebre · {tot.get('Quiebre', 0):,}"),
            ("idle", f"Nunca tuvo · {tot.get('Nunca tuvo', 0):,}"),
        ]
    )
    comp = mc.groupby([grupo, "estado"], as_index=False)["n"].sum()
    st.altair_chart(barras_apiladas_100(comp, grupo, "estado", "n", ESTADOS), width="stretch")

m1, m2 = st.columns(2, gap="medium")
with m1, st.container(key="card_modelos"):
    section("Modelos que más salen", "Unidades, tiendas y tallas por modelo-color", "trending-up")
    env = det[det["cantidad"] > 0]
    top = (
        env.groupby("modelo_color_id")
        .agg(u=("cantidad", "sum"), t=("tienda_id", "nunique"), s=("talla", "nunique"))
        .nlargest(10, "u")
        .reset_index()
    )
    filas = [(x.modelo_color_id, int(x.t), int(x.s), int(x.u)) for x in top.itertuples()]
    html(
        mini_tabla(filas, ["Modelo-color", "Tiendas", "Tallas", "Unidades"], num={1, 2, 3}, barra=3)
        if filas
        else "<p>Sin envíos.</p>"
    )
with m2, st.container(key="card_motivos"):
    section("Por qué sí y por qué no", "Filas tienda × talla por motivo", "info")
    mot = (
        det.groupby("motivo_codigo")
        .agg(f=("sku", "size"), u=("cantidad", "sum"))
        .sort_values("f", ascending=False)
        .reset_index()
    )
    filas = [
        (DESCRIPCION_CODIGOS.get(x.motivo_codigo, x.motivo_codigo), int(x.u), int(x.f))
        for x in mot.itertuples()
    ]
    html(mini_tabla(filas, ["Motivo", "Unidades", "Filas"], num={1, 2}, barra=2))

notas = diag.get("notas") or []
if notas:
    section("Avisos de la carga", "Lo que conviene revisar antes de aprobar", "alert")
    for n in notas:
        issue_box("warn", "Revisar", str(n))
