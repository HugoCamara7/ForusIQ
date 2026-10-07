"""Vista única: generar la distribución, aprobarla y descargar el archivo."""

import pandas as pd
import streamlit as st
from app.components.archivo import boton_archivo
from app.components.corridas_guardadas import panel as panel_corridas
from app.components.estado import entradas_de_la_corrida, revision_carga
from app.components.ui import hero, html, issue_box, kpi_row, ranking, section, velocimetro

ss = st.session_state
res = ss.get("resultado")
diag = ss.get("diagnostico") or {}


def _fecha(x) -> str:
    return pd.Timestamp(x).strftime("%d/%m/%Y") if x else ""


if res is None:
    hero(
        "Forusight",
        "Distribución del CD 320 a tiendas por modelo, talla y cantidad, con el motivo de "
        "cada envío.",
        eyebrow="Reposición",
    )
    issue_box(
        "info",
        "Empieza aquí",
        "Elige la marca y la semana en la barra lateral y presiona «Ejecutar corrida».",
    )
    panel_corridas()
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
hero(
    f"{r['unidades_a_distribuir']:,} unidades para {r['tiendas_con_envio']} tiendas",
    f"Distribución sugerida desde el CD {r['cd_id']}.",
    eyebrow="Aprobada" if aprobada else "Propuesta",
    meta=meta,
)
plan = diag.get("planificacion") or {}
if plan.get("claves") and plan.get("dias", 0) > res.params.nivel_neo.dias_nivel_reporte:
    issue_box(
        "warn",
        f"El último reporte de Neogística cargado es del {_fecha(plan['ultimo_reporte'])} "
        f"(hace {plan['dias']} días)",
        "Para reponer al mismo ritmo que Neogística, sube el reporte de distribución de hoy en "
        "Más opciones → «Reportes de distribución (Neogística)» y vuelve a ejecutar la corrida.",
    )

sin_plan = diag.get("tiendas_sin_planificacion") or []
if sin_plan:
    issue_box(
        "warn",
        f"{len(sin_plan)} tienda(s) sin datos de planificación: {', '.join(sin_plan)}",
        "Su stock mínimo, nivel y punto de reorden se estiman con los de otras tiendas y pueden "
        "salir altos. Sube un reporte de Neogística que incluya esas tiendas en Más opciones → "
        "«Reportes de distribución (Neogística)».",
    )

con_espejo = diag.get("tiendas_con_espejo") or []
if con_espejo:
    issue_box(
        "info",
        f"{len(con_espejo)} tienda(s) que Neogística no planifica, con tienda espejo: "
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
            f"Después del envío: {despues:.1%} · {activos:,} SKU activos · meta ≥ 93 %",
        )
    )

panel_corridas()
