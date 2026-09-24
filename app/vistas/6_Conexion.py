"""Conexión a BigQuery: estado de secrets, tablas, mapeo de columnas y prueba de lectura."""

import pandas as pd
import streamlit as st
from app.components.estado import SECRETS, SETTINGS, cargar_entradas, repositorio
from app.components.login import puede
from app.components.ui import hero

from forusight.data import mapeo
from forusight.data.bq_client import bigquery_habilitado, diagnostico_secrets, explicar_error

hero(
    "Conexión a BigQuery",
    "Tablas, columnas y mapeo que usa Forusight. Prueba la lectura antes de correr.",
    eyebrow="Datos",
)
if not puede("conexion"):
    st.error("Sólo administradores.")
    st.stop()

with st.expander("Secrets detectados (sólo nombres, nunca valores)"):
    for linea in diagnostico_secrets(SECRETS):
        st.markdown(f"- {linea}")
    st.markdown(
        f"- Proyecto de los jobs: `{SETTINGS.gcp_project or '—'}` · región "
        f"`{SETTINGS.bq_location or 'automática'}` · CD `{SETTINGS.cd_id}`"
    )

if not bigquery_habilitado(SECRETS):
    st.info(
        "Pega en los secrets los bloques `[bigquery]` y `[gcp_service_account]` de Catálogo "
        "Control Center. No hace falta configurar tablas: ARTI y stock se toman por defecto."
    )
    st.stop()

repo = repositorio("bigquery")
tablas = repo.tablas()
st.subheader("Tablas")
st.dataframe(
    pd.DataFrame(
        [
            {
                "dato": "Maestro de productos (ARTI)",
                "tabla": tablas["arti"],
                "estado": "por defecto / secrets",
            },
            {
                "dato": "Stock por fecha de corte",
                "tabla": tablas["stock"],
                "estado": "por defecto / secrets",
            },
            {
                "dato": "Venta",
                "tabla": tablas["ventas"] or "—",
                "estado": "secrets" if tablas["ventas"] else "FALTA: `ventas_table` es obligatoria",
            },
            {
                "dato": "Maestro tienda → nombre",
                "tabla": tablas["tiendas"] or "—",
                "estado": "secrets" if tablas["tiendas"] else "opcional: `maestro_tiendas_table`",
            },
            {
                "dato": "Maestro modelo → cadena",
                "tabla": tablas["cadena"] or "—",
                "estado": "secrets" if tablas["cadena"] else "opcional: `maestro_cadena_table`",
            },
        ]
    ),
    hide_index=True,
    width="stretch",
)

c1, c2 = st.columns(2)
if c1.button("Probar conexión y leer columnas", type="primary", width="stretch"):
    try:
        st.session_state.mapeos_conexion = repo.mapeos(tablas)
        st.success("Conectado. Columnas leídas con INFORMATION_SCHEMA (sin costo).")
    except Exception as exc:
        st.error(explicar_error(exc))
if c2.button("Buscar tablas de venta en el datalake", width="stretch"):
    try:
        st.session_state.tablas_venta = repo.buscar_tablas()
    except Exception as exc:
        st.error(explicar_error(exc))

encontradas = st.session_state.get("tablas_venta")
if encontradas is not None:
    if encontradas.empty:
        st.info("No se encontraron tablas con `venta`, `vta` o `sales` en el nombre.")
    else:
        st.caption("Copia la que corresponda a los secrets, dentro de `[bigquery]`:")
        st.dataframe(encontradas[["dataset", "tabla", "tipo"]], hide_index=True, width="stretch")
        elegida = st.selectbox("Tabla de venta", encontradas["ruta"])
        st.code(f'ventas_table = "{elegida}"', language="toml")

mapas = st.session_state.get("mapeos_conexion")
if mapas:
    etiquetas = {
        "ventas": "Venta",
        "arti": "Maestro ARTI",
        "stock": "Stock",
        "tiendas": "Maestro tiendas",
        "cadena": "Maestro modelo→cadena",
    }
    snippet = {}
    for fuente, tab in zip(mapas, st.tabs([etiquetas[f] for f in mapas]), strict=True):
        mapa, origen, cols = mapas[fuente]
        with tab:
            st.caption(f"`{tablas[fuente]}` · {len(cols)} columnas · mapeo {origen}")
            opciones = ["—", *cols]
            nuevo = {}
            grid = st.columns(3)
            for i, campo in enumerate(mapeo.FUENTES[fuente]):
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
            (st.warning("Falta: " + ", ".join(falta)) if falta else st.success("Mapeo completo."))
            if st.button("Guardar mapeo", key=f"guardar_{fuente}"):
                mapeo.guardar(fuente, tablas[fuente], nuevo)
                st.toast(f"Mapeo de {fuente} guardado.")
            snippet[fuente] = nuevo
    with st.expander("Fijar el mapeo en los secrets (sobrevive a un redespliegue)"):
        st.code(mapeo.a_toml(snippet), language="toml")

st.divider()
st.subheader("Prueba de lectura")
st.caption(
    f"Lee los datos para la semana y la marca elegidas en la barra lateral, con dry run y "
    f"tope de {repo.client.max_gb:.0f} GB por consulta."
)
if st.button("Leer datos y ver diagnóstico"):
    ss = st.session_state
    cd = ss.get("cd_archivo") or (None, "")
    try:
        inputs, diag = cargar_entradas(
            "bigquery",
            pd.Timestamp(ss.fecha_corte).date().isoformat(),
            cd[0],
            cd[1],
            tuple(ss.marcas) if ss.get("marcas") else None,
        )
        st.success(
            f"Lectura OK · {diag.get('gb_leidos', 0):.2f} GB · stock al "
            f"{diag.get('fecha_foto')} · {diag.get('cortes_en_ventana')} fotos · venta: "
            f"{diag.get('fuente_venta')}"
        )
        st.json(diag.get("filas", {}))
        for nota in diag.get("notas", []):
            st.markdown(f"- {nota}")
        st.dataframe(inputs.dim_tienda, hide_index=True, width="stretch")
    except Exception as exc:
        st.error(explicar_error(exc))
