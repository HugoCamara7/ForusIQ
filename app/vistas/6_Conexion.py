"""Conexión a BigQuery: diagnóstico de secrets, tablas, mapeo de columnas y costo."""

import pandas as pd
import streamlit as st
from app.components.estado import SECRETS, SETTINGS, cargar_entradas
from app.components.login import puede

from forusight.data import mapeo
from forusight.data.bq_client import (
    bigquery_habilitado,
    diagnostico_secrets,
    explicar_error,
)
from forusight.data.repository import FuentesRepository

st.title("Conexión a BigQuery")
if not puede("conexion"):
    st.error("Sólo administradores.")
    st.stop()

with st.expander("Secrets detectados (sólo nombres, nunca valores)", expanded=True):
    for linea in diagnostico_secrets(SECRETS):
        st.markdown(f"- {linea}")
    st.markdown(
        f"- Proyecto de los jobs: `{SETTINGS.gcp_project or '—'}` · región "
        f"`{SETTINGS.bq_location}` · CD `{SETTINGS.cd_id}` · marcas `{SETTINGS.marcas}`"
    )

if not bigquery_habilitado(SECRETS):
    st.info(
        "Agrega los bloques `[bigquery]` y `[gcp_service_account]` a los secrets (los mismos "
        "de Catálogo/Repo Control Center) y define `ventas_table`, `product_master_table` y "
        "`stock_table`. Mientras tanto la app funciona en modo demo."
    )
    st.stop()

repo = FuentesRepository(settings=SETTINGS, secrets=SECRETS)
try:
    tablas = repo.tablas()
except ValueError as exc:
    st.error(str(exc))
    st.stop()
st.dataframe(
    pd.DataFrame({"fuente": list(tablas), "tabla": list(tablas.values())}),
    hide_index=True,
    width="stretch",
)

if st.button("Probar conexión y leer esquemas", type="primary"):
    try:
        st.session_state.mapeos_conexion = repo.mapeos(tablas)
        st.success("Conectado: esquemas leídos con INFORMATION_SCHEMA (sin costo).")
    except Exception as exc:
        st.error(explicar_error(exc))

mapas = st.session_state.get("mapeos_conexion")
if mapas:
    etiquetas = {"ventas": "Venta", "arti": "Maestro ARTI", "stock": "Stock por fecha de corte"}
    snippet = {}
    for fuente, tab in zip(mapas, st.tabs([etiquetas[f] for f in mapas]), strict=True):
        mapa, origen, cols = mapas[fuente]
        with tab:
            st.caption(f"`{tablas[fuente]}` · {len(cols)} columnas · mapeo **{origen}**")
            opciones = ["—"] + cols
            nuevo = {}
            campos = list(mapeo.FUENTES[fuente])
            grid = st.columns(3)
            for i, campo in enumerate(campos):
                actual = mapa.get(campo)
                sel = grid[i % 3].selectbox(
                    campo,
                    opciones,
                    key=f"map_{fuente}_{campo}",
                    index=opciones.index(actual) if actual in cols else 0,
                )
                if sel != "—":
                    nuevo[campo] = sel
            falta = mapeo.faltantes(fuente, nuevo)
            if falta:
                st.warning("Falta: " + ", ".join(falta))
            else:
                st.success("Mapeo completo.")
            if st.button("Guardar mapeo", key=f"guardar_{fuente}"):
                mapeo.guardar(fuente, tablas[fuente], nuevo)
                st.toast(f"Mapeo de {fuente} guardado.")
            snippet[fuente] = nuevo
    st.markdown(
        "**Para que el mapeo sobreviva a un redespliegue** (Streamlit Cloud tiene disco "
        "efímero), pégalo dentro del bloque `[bigquery]` de los secrets:"
    )
    st.code(mapeo.a_toml(snippet), language="toml")

st.divider()
st.subheader("Prueba de lectura")
st.caption(
    "Lee las fuentes para la fecha de corte de la barra lateral, con dry run y tope de "
    f"{repo.client.max_gb:.0f} GB por consulta."
)
if st.button("Leer datos y ver diagnóstico"):
    ss = st.session_state
    cd = ss.get("cd_archivo") or (None, "")
    try:
        inputs, diag = cargar_entradas(
            "bigquery", pd.Timestamp(ss.fecha_corte).date().isoformat(), cd[0], cd[1]
        )
        st.success(
            f"Lectura OK · {diag.get('gb_leidos', 0):.2f} GB leídos · foto de stock "
            f"{diag.get('fecha_foto')} · {diag.get('cortes_en_ventana')} fechas de corte"
        )
        st.json(diag.get("filas", {}))
        for nota in diag.get("notas", []):
            st.markdown(f"- {nota}")
        st.dataframe(inputs.dim_tienda, hide_index=True, width="stretch")
    except Exception as exc:
        st.error(explicar_error(exc))
