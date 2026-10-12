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
            {
                "dato": "Pedidos · cabecera (tránsito)",
                "tabla": tablas["pedidos"] or "—",
                "estado": "secrets" if tablas["pedidos"] else "opcional: `pedidos_header_table`",
            },
            {
                "dato": "Pedidos · detalle (tránsito)",
                "tabla": tablas["pedidos_detalle"] or "—",
                "estado": (
                    "secrets" if tablas["pedidos_detalle"] else "opcional: `pedidos_detail_table`"
                ),
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
        "pedidos": "Pedidos · cabecera",
        "pedidos_detalle": "Pedidos · detalle",
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
st.subheader("Revisar tránsito")
st.caption(
    "Muestra cada línea de pedido hacia la tienda (todo el historial), si "
    "cuenta como tránsito con las reglas de Parámetros → recepcion y por qué (motivo; un "
    "pedido despachado deja de contar al pasar el plazo de entrega de la tienda), y cuántas "
    "veces aparece "
    "en las tablas (repetida = copia de staging, cuenta una sola vez). Abajo, los locales "
    "destino de los últimos 3 días: así se ve si una tienda llega con otro código."
)
t1, t2 = st.columns([3, 1])
tienda_tr = t1.text_input("Código de tienda", value="97", key="tienda_transito")
if t2.button("Revisar", width="stretch", key="revisar_transito"):
    try:
        st.session_state.revision_transito = (
            tienda_tr,
            *repo.revisar_transito(tienda_tr, st.session_state.params.recepcion),
        )
    except Exception as exc:
        st.error(explicar_error(exc))
rev = st.session_state.get("revision_transito")
if rev:
    tienda_r, lineas, locales = rev
    if lineas.empty:
        st.warning(
            f"No hay pedidos hacia el local «{tienda_r}» en la ventana. Revisa abajo si la "
            "tienda aparece con otro código."
        )
    else:
        cuenta = lineas.loc[lineas["cuenta_transito"]]
        c1, c2, c3 = st.columns(3)
        c1.metric("Tránsito que cuenta", f"{int(cuenta['unidades'].sum()):,} u")
        c2.metric("Pedidos que cuentan", f"{cuenta['nro_pedido'].nunique():,}")
        c3.metric("Líneas repetidas", f"{int(lineas['repetida'].sum()):,}")
        if "motivo" in lineas:
            st.dataframe(
                lineas.groupby("motivo", as_index=False)
                .agg(lineas=("unidades", "size"), unidades=("unidades", "sum"))
                .sort_values("unidades", ascending=False),
                hide_index=True,
                width="stretch",
            )
        st.dataframe(lineas, hide_index=True, width="stretch")
    if len(locales):
        st.markdown("**Locales destino de los últimos 3 días**")
        st.dataframe(locales, hide_index=True, width="stretch")

st.markdown("**Plazo de entrega de cada tienda**")
st.caption(
    "Días entre la fecha del pedido y su fecha de recepción, con los pedidos recibidos en los "
    "últimos 120 días (reposición, llenado y traspasos). Un pedido ya despachado (documentado, "
    "en transporte, prerecepcionado) cuenta como tránsito sólo hasta «Plazo tránsito» días: "
    "más antiguo, ya llegó y quedó sin cerrar. Sale de nuestros pedidos, sin reportes externos."
)
if st.button("Medir plazos de entrega", key="medir_plazos"):
    try:
        st.session_state.plazos_entrega = repo.plazos_entrega(
            pd.Timestamp.today().date(), st.session_state.params.recepcion
        )
    except Exception as exc:
        st.error(explicar_error(exc))
plz = st.session_state.get("plazos_entrega")
if plz is not None:
    if plz.empty:
        st.warning(
            "No hay pedidos con fecha de recepción en los últimos 120 días: revisa el mapeo de "
            "`fecha` y `fecha_recepcion` de la cabecera de pedidos."
        )
    else:
        vista = plz.rename(
            columns={
                "tienda_id": "Tienda",
                "pedidos": "Pedidos recibidos",
                "dias_p50": "Días (mediana)",
                "dias_p90": "Días (90 %)",
                "dias_p95": "Días (95 %)",
                "dias_max": "Días (máx.)",
                "plazo_transito": "Plazo tránsito",
            }
        )
        st.dataframe(vista, hide_index=True, width="stretch")
        st.caption(
            f"Tiendas con menos de {st.session_state.params.recepcion.min_pedidos_plazo} pedidos "
            f"recibidos usan {st.session_state.params.recepcion.dias_transito_recientes} días."
        )

st.divider()
st.subheader("Explorar tablas: pedidos, despachos, ingresos y ARTI")
st.caption(
    "Busca tablas por nombre y muestra sus columnas y 20 filas de ejemplo (lectura directa, "
    "sin costo de consulta). Sirve para conectar el detalle de pedidos (aprobados, en "
    "despacho, ingresos) y para encontrar la temporada comercial en ARTI."
)
e1, e2 = st.columns([3, 1])
patron = e1.text_input(
    "Nombre contiene", value="pedido, despacho, ingreso, traslado, guia, transfer"
)
if e2.button("Buscar", width="stretch", key="buscar_explorar"):
    try:
        pats = tuple(p.strip().lower() for p in patron.split(",") if p.strip())
        st.session_state.tablas_explorar = repo.buscar_tablas(pats)
    except Exception as exc:
        st.error(explicar_error(exc))
halladas = st.session_state.get("tablas_explorar")
opciones_t = list(halladas["ruta"]) if halladas is not None and len(halladas) else []
arti_ruta = repo.tablas().get("arti")
if arti_ruta:
    opciones_t = [arti_ruta] + [o for o in opciones_t if o != arti_ruta]
if opciones_t:
    ruta = st.selectbox("Tabla", opciones_t, key="tabla_explorar")
    if st.button("Ver columnas y ejemplo", key="ver_explorar"):
        try:
            st.session_state.exploracion = (ruta, *repo.explorar_tabla(ruta))
        except Exception as exc:
            st.error(explicar_error(exc))
exp = st.session_state.get("exploracion")
if exp:
    ruta, cols, muestra = exp
    st.markdown(f"**{ruta}** · {len(cols)} columnas")
    temp = [c for c in cols["column_name"] if "temp" in str(c).lower()]
    if temp:
        st.caption("Columnas de temporada: " + ", ".join(temp))
    st.dataframe(cols, hide_index=True, width="stretch", height=240)
    if len(muestra):
        st.dataframe(muestra, hide_index=True, width="stretch")
    else:
        st.caption("Es una vista: no se puede leer una muestra sin consulta.")

st.divider()
st.subheader("Prueba de lectura")
st.caption(
    f"Lee los datos para la semana y la marca elegidas en Inicio, con dry run y "
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
