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
st.subheader("Revisar la venta")
st.caption(
    "Compara la última fecha de venta de toda la tabla con la de la marca elegida. Si la tabla "
    "tiene venta más reciente que la marca, la tabla está al día y lo que cambió es el código "
    "de producto o de tienda (se muestran esas filas)."
)
if st.button("Revisar venta"):
    try:
        info, filas = repo.revisar_venta(list(st.session_state.get("marcas") or []))
        c = st.columns(3)
        c[0].metric("Última venta en la tabla", str(info.get("ultima_tabla") or "—"))
        c[1].metric("Última venta de la marca", str(info.get("ultima_marca") or "—"))
        c[2].metric("Filas últimos 14 días", f"{int(info.get('filas_14_dias') or 0):,}")
        if len(filas):
            st.warning(
                "La tabla tiene venta posterior a la última de la marca: revisa el código de "
                "producto/tienda de estas filas."
            )
            st.dataframe(filas, hide_index=True, width="stretch")
        elif info.get("ultima_tabla") == info.get("ultima_marca"):
            st.info("La tabla completa llega hasta la misma fecha: la tabla está atrasada.")
    except Exception as exc:
        st.error(explicar_error(exc))

st.divider()
st.subheader("Comparar stock del CD con el reporte")
st.caption(
    "El CD 320 trae dos columnas en stock_bi (stock_tiendas y stock_bodega). Sube el reporte "
    "de distribución del día en la barra lateral y compara qué parte coincide con su «Stock en "
    "CD» (el disponible para repartir)."
)
if st.button("Comparar stock del CD", disabled=not st.session_state.get("reporte")):
    ss = st.session_state
    from app.components.estado import _leer_reporte

    from forusight.data import reporte as R

    try:
        rep, _ = _leer_reporte(ss.reporte[0])
        marcas = tuple(ss.get("marcas_reporte") or ss.get("marcas") or ()) or None
        rep = R.filtrar_marcas(rep, list(marcas) if marcas else None)
        entradas_bq, _ = cargar_entradas(
            "bigquery", pd.Timestamp(ss.fecha_corte).date().isoformat(), None, "", marcas
        )
        ss.comparacion_cd = R.comparar_stock_cd(entradas_bq.stock_cd, rep)
    except Exception as exc:
        st.error(explicar_error(exc))

if st.session_state.get("comparacion_cd"):
    ss = st.session_state
    resumen, detalle = ss.comparacion_cd
    st.dataframe(
        resumen,
        hide_index=True,
        width="stretch",
        column_config={
            "opcion": "Stock CD de BigQuery",
            "sku_iguales": st.column_config.ProgressColumn(
                "SKU iguales al reporte", format="percent", min_value=0, max_value=1
            ),
            "unidades_bigquery": "Unidades BigQuery",
            "unidades_reporte": "Unidades reporte",
            "sku_con_mas_stock": "SKU con más stock",
            "sku_con_menos_stock": "SKU con menos stock",
        },
    )
    mejor = resumen.iloc[0]["opcion"] if len(resumen) else None
    st.caption(f"Hoy se reparte: «{ss.params.stock_cd.componentes}».")
    if (
        mejor
        and mejor != ss.params.stock_cd.componentes
        and st.button(f"Usar «{mejor}» como stock del CD", type="primary")
    ):
        ss.params.stock_cd.componentes = mejor
        st.success(f"Listo: el CD se reparte con «{mejor}». Guárdalo en Parámetros.")
    with st.expander("Detalle por SKU"):
        st.dataframe(detalle, hide_index=True, width="stretch")

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
