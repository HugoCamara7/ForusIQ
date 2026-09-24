import pandas as pd
import streamlit as st
from app.components.archivo import boton_archivo
from app.components.estado import entradas_de_la_corrida
from app.components.ui import (
    apiladas,
    hero,
    html,
    issue_box,
    kpi_row,
    medidor,
    mini_tabla,
    ranking,
    section,
    stepper,
)

from forusight.engine.reasons import DESCRIPCION_CODIGOS

ss = st.session_state
res = ss.get("resultado")
diag = ss.get("diagnostico") or {}
FLUJO = [
    ("Datos", "venta 12 semanas + último stock"),
    ("Necesidad", "demanda, curva de tallas, afinidad"),
    ("Distribución", "stock del CD 320"),
    ("Tiendas y cadenas", "cada marca a su cadena"),
    ("Archivo Forusight", "listo para enviar"),
]
ESTADOS = {
    "Tiene y vende": "#16A34A",
    "Tiene sin venta": "#F59E0B",
    "Quiebre": "#DC2626",
    "Nunca tuvo": "#CBD5E1",
}
NOMBRE_ESTADO = {
    "TUVO_Y_VENDE": "Tiene y vende",
    "TUVO_SIN_VENTA": "Tiene sin venta",
    "QUIEBRE": "Quiebre",
    "NUNCA_TUVO": "Nunca tuvo",
}


def _fecha(x) -> str:
    return pd.Timestamp(x).strftime("%d/%m/%Y") if x else ""


if res is None:
    hero(
        "Forusight",
        "Distribución del CD 320 a tiendas por modelo, talla y cantidad, con el motivo de "
        "cada envío.",
        eyebrow="Dashboard",
    )
    stepper(FLUJO, 1)
    issue_box(
        "info",
        "Empieza aquí",
        "Elige la marca y la semana en la barra lateral y presiona «Ejecutar corrida».",
    )
    st.stop()

r = res.resumen
entradas = entradas_de_la_corrida()
tiendas = entradas.dim_tienda if entradas is not None else pd.DataFrame(columns=["tienda_id"])
cols_t = [c for c in ("tienda_id", "nombre", "cadena") if c in tiendas.columns]
det = res.detalle.merge(tiendas[cols_t].drop_duplicates("tienda_id"), on="tienda_id", how="left")
if "nombre" not in det:
    det["nombre"] = pd.NA
det["tienda"] = det["nombre"].astype("string").fillna(det["tienda_id"].astype("string"))
tiene_cadena = "cadena" in det.columns and det["cadena"].notna().any()
grupo = "cadena" if tiene_cadena else "categoria"
etiqueta = {"cadena": "cadena", "categoria": "categoría"}[grupo]
env = det[det["cantidad"] > 0]

meta = [f"Semana {_fecha(r['fecha_corte'])}"]
if diag.get("fecha_foto"):
    meta.append(f"Stock al {_fecha(diag['fecha_foto'])}")
meta += list(diag.get("marcas") or [])
if entradas is not None and getattr(entradas, "reporte", None) is not None:
    from forusight.data.reporte import coincidencia

    meta.append(
        f"Coincide con el reporte: {coincidencia(entradas.reporte, res.detalle)['pct_filas']:.1%}"
    )
elif diag.get("corte_venta"):
    meta.append(f"Venta hasta {_fecha(diag.get('venta_hasta'))}")
hero(
    f"{r['unidades_a_distribuir']:,} unidades para {r['tiendas_con_envio']} tiendas",
    f"Distribución sugerida desde el CD {r['cd_id']}: "
    f"{env['modelo_color_id'].nunique():,} modelos-color en "
    f"{env['talla'].nunique():,} tallas.",
    eyebrow="Dashboard",
    meta=meta,
)
stepper(FLUJO, 5 if ss.get("aprobacion") else 4)
_, zona_boton = st.columns([3, 1])
with zona_boton:
    boton_archivo("archivo_dashboard")

quiebres = det.loc[det["estado_mc"] == "QUIEBRE", ["tienda_id", "modelo_color_id"]]
kpi_row(
    [
        (
            "Unidades a enviar",
            f"{r['unidades_a_distribuir']:,}",
            f"de {r['necesidad_total']:,} de necesidad",
            "truck",
        ),
        (
            "Tiendas con envío",
            f"{r['tiendas_con_envio']}",
            f"de {det['tienda_id'].nunique()} evaluadas",
            "store",
        ),
        (
            "Modelos-color",
            f"{env['modelo_color_id'].nunique():,}",
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

c1, c2 = st.columns([1.5, 1], gap="medium")
with c1, st.container(key="card_tiendas"):
    section("Unidades por tienda", "Las 12 tiendas que más reciben", "store")
    por_t = (
        env.groupby(["tienda", "cadena" if tiene_cadena else "tienda_id"], as_index=False)[
            "cantidad"
        ]
        .sum()
        .nlargest(12, "cantidad")
    )
    filas = [
        (x.tienda, float(x.cantidad), x.cadena if tiene_cadena else "") for x in por_t.itertuples()
    ]
    html(ranking(filas) if filas else "<p>Ninguna tienda recibe en esta corrida.</p>")
with c2:
    with st.container(key="card_cobertura"):
        section("Cobertura", "Cuánto de lo que falta se cubre", "target")
        uso = (
            r["unidades_a_distribuir"] / r["stock_cd_disponible"] if r["stock_cd_disponible"] else 0
        )
        html(
            medidor(
                "Necesidad cubierta",
                r["fill_rate"],
                f"{r['unidades_a_distribuir']:,} de {r['necesidad_total']:,} unidades",
            )
            + medidor(
                "Stock del CD usado",
                uso,
                f"{r['unidades_a_distribuir']:,} de {r['stock_cd_disponible']:,} unidades",
            )
        )
    with st.container(key="card_cadenas"):
        section(f"Por {etiqueta}", "Cómo se reparte el envío", "grid")
        por_c = env.groupby(grupo, as_index=False)["cantidad"].sum().nlargest(10, "cantidad")
        html(ranking([(str(x[0]), float(x[1]), "") for x in por_c.itertuples(index=False)]))

with st.container(key="card_estados"):
    section(
        "Disponibilidad en tienda",
        f"Cada tienda × modelo-color por {etiqueta}: separa falta de venta de falta de stock",
        "target",
    )
    mc = det.drop_duplicates(["tienda_id", "modelo_color_id"]).assign(
        estado=lambda d: d["estado_mc"].map(NOMBRE_ESTADO)
    )
    tabla = mc.groupby([grupo, "estado"]).size().unstack(fill_value=0)
    tabla = tabla.loc[tabla.sum(axis=1).sort_values(ascending=False).index]
    grupos = {str(g): {k: int(v) for k, v in fila.items()} for g, fila in tabla.iterrows()}
    html(apiladas(grupos, ESTADOS))

m1, m2 = st.columns(2, gap="medium")
with m1, st.container(key="card_modelos"):
    section("Modelos que más salen", "Unidades, tiendas y tallas por modelo-color", "trending-up")
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
