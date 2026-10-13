"""Vista única: generar la distribución, aprobarla y descargar el archivo."""

import pandas as pd
import streamlit as st
from app.components.archivo import boton_archivo
from app.components.estado import entradas_de_la_corrida, panel_corrida, revision_carga
from app.components.ui import hero, html, issue_box, kpi_row, ranking, section, velocimetro

ss = st.session_state
# Arriba el recuadro azul y debajo la consulta. El recuadro muestra la corrida que se acaba de
# ejecutar, así que se reserva su lugar y se llena después de la consulta.
cabecera = st.container()
panel_corrida()  # marca, fechas y «Ejecutar corrida»
res = ss.get("resultado")
diag = ss.get("diagnostico") or {}


def _fecha(x) -> str:
    return pd.Timestamp(x).strftime("%d/%m/%Y") if x else ""


if res is None:
    with cabecera:
        hero(
            "Forusight",
            "Reposición del CD 320 (y la bodega 380 de Azaleia) a tiendas por modelo, talla y "
            "cantidad, con el motivo de cada envío.",
            eyebrow="Reposición",
        )
    issue_box(
        "info",
        "Empieza aquí",
        "Elige la marca y las fechas arriba y presiona «Ejecutar corrida».",
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
env = det[det["cantidad"] > 0]

if diag.get("aviso_carga"):  # corrida antes de la carga diaria de stock / venta
    st.error(diag["aviso_carga"])
    if revision := revision_carga(diag):
        st.caption(revision)

meta = [f"Semana {_fecha(r['fecha_corte'])}"]
if diag.get("fecha_foto"):  # el corte de fecha F es el cierre del día F
    meta.append(f"Stock al cierre del {_fecha(pd.Timestamp(diag['fecha_foto']))}")
meta += list(diag.get("marcas") or [])
if diag.get("dia_reposicion"):
    from forusight.data.calendario import dia_semana

    dias = {
        "LU": "lunes",
        "MA": "martes",
        "MI": "miércoles",
        "JU": "jueves",
        "VI": "viernes",
        "SA": "sábado",
        "DO": "domingo",
    }
    meta.append(
        f"Reposición del {dias[dia_semana(diag['dia_reposicion'])]} "
        f"{_fecha(diag['dia_reposicion'])}"
        + (f" · rutas: {', '.join(diag['malls_hoy'])}" if diag.get("malls_hoy") else "")
    )
aprobada = ss.get("aprobacion_confirmada") == res.run_id
if aprobada and ss.get("aprobacion_info"):
    info = ss.aprobacion_info
    meta.append(f"Aprobada {info['hora']} · {info['usuario']}")
with cabecera:
    hero(
        f"{r['unidades_a_distribuir']:,} unidades para {r['tiendas_con_envio']} tiendas",
        f"Distribución sugerida desde el CD {r['cd_id']}.",
        eyebrow="Aprobada" if aprobada else "Propuesta",
        meta=meta,
    )
tr = diag.get("transito_pedidos") or {}
if tr:
    partes = [
        f"En el CD (aprobado, picking): {tr.get('en_cd', 0):,} u",
        f"despachado (documentado, en transporte, prerecepcionado): {tr.get('despachado', 0):,} u",
        "sólo pedidos creados hasta el día del cierre del stock (misma foto que el stock)",
    ]
    post = tr.get("post_cierre") or {}
    if post.get("unidades"):
        partes.append(
            f"no cuentan {post['unidades']:,} u de pedidos creados después del cierre"
            if post.get("excluidos")
            else f"incluye {post['unidades']:,} u de pedidos creados después del cierre"
        )
    issue_box(
        "info",
        f"Tránsito de pedidos: {tr.get('unidades', 0):,} u hacia {tr.get('tiendas', 0)} tiendas",
        " · ".join(partes) + ".",
    )
elif "transito_pedidos" in diag:  # no se leyeron los pedidos: el tránsito quedó en 0
    motivo = [n for n in diag.get("notas") or [] if str(n).startswith("Tránsito")]
    issue_box(
        "warn",
        "Tránsito en 0: no se pudieron leer los pedidos",
        (" ".join(motivo) or "La corrida no trajo las tablas de pedidos.")
        + " La posición de stock no descuenta lo que ya va en camino: revisa antes de aprobar.",
    )

sin_plan = diag.get("tiendas_sin_planificacion") or []
if sin_plan:
    issue_box(
        "info",
        f"{len(sin_plan)} tienda(s) sin stock mínimo propio: {', '.join(sin_plan)}",
        "Su stock mínimo se estima con el del mismo SKU en otras tiendas y el nivel sale de su "
        "propia venta. Para fijarlo, asígnales una tienda espejo en config/tiendas_espejo.csv.",
    )

con_espejo = diag.get("tiendas_con_espejo") or []
if con_espejo:
    issue_box(
        "info",
        f"{len(con_espejo)} tienda(s) sin stock mínimo propio, con tienda espejo: "
        f"{', '.join(con_espejo)}",
        "Toman el stock mínimo, la unidad de empaque y la categoría de su espejo; el pronóstico "
        "sale de su propia venta. Se cambia en config/tiendas_espejo.csv.",
    )

# --- acción única: aprobar y descargar el archivo Forusight
with st.container(key="accion_archivo"):
    boton_archivo("archivo_dashboard")

# --- KPI
env_mc = env["modelo_color_id"].nunique()
kpi_row(
    [
        ("Unidades a enviar", f"{r['unidades_a_distribuir']:,}", "desde el CD", "truck"),
        ("Tiendas con envío", f"{r['tiendas_con_envio']}", "de la ruta de hoy", "store"),
        ("Cantidad de modelos color", f"{env_mc:,}", "con al menos un envío", "layers"),
        (
            f"Stock CD {r['cd_id']}",
            f"{r['stock_cd_disponible']:,}",
            "unidades disponibles",
            "package",
        ),
    ]
)

c1, c2 = st.columns([1.5, 1], gap="medium")
with c1, st.container(key="card_tiendas"):
    section("Unidades por tienda", "Tiendas de la ruta con envío", "store")
    tiene_cadena = "cadena" in env and env["cadena"].notna().any()
    por_t = (
        env.groupby(["tienda", "cadena" if tiene_cadena else "tienda_id"], as_index=False)[
            "cantidad"
        ]
        .sum()
        .sort_values("cantidad", ascending=False)
    )
    filas = [
        (x.tienda, float(x.cantidad), x.cadena if tiene_cadena else "") for x in por_t.itertuples()
    ]
    html(ranking(filas) if filas else "<p>Ninguna tienda recibe en esta corrida.</p>")
with c2, st.container(key="card_disponibilidad"):
    hoy = r.get("disponibilidad_retail")
    if hoy is None:  # corridas anteriores a este indicador
        from forusight.engine import disponibilidad as DISP

        k = DISP.kpis(det)
        hoy, despues, activos = (
            k.get("simple_antes", 1.0),
            k.get("simple_despues", 1.0),
            k.get("activos", 0),
        )
    else:
        despues, activos = (
            r.get("disponibilidad_retail_despues", hoy),
            r.get("sku_activos_retail", 0),
        )
    html(
        velocimetro(
            hoy,
            "Disponibilidad del retail",
            (
                f"Con tránsito: {r['disponibilidad_retail_transito']:.1%} · "
                if r.get("disponibilidad_retail_transito") is not None
                else ""
            )
            + f"Después del envío: {despues:.1%} · {activos:,} tallas activas · meta ≥ 93 %",
        )
    )
