"""Temporadas fuera de parámetros: desde qué temporada comercial repone cada tienda.

Se gestiona por Excel (Cod Tienda, Temporada minima), como los bloqueos: elegir la acción,
subir, revisar la validación y confirmar. Una tienda sin configuración usa el parámetro general
(``surtido.temporada_desde``). Las liquidadoras (DH, SE, FB) no se configuran aquí: reponen por
jerarquía las temporadas hasta INVIERNO 2026.

Además, las colecciones activas por tienda × clase × marca (reporte SAP 1037): las tiendas de
línea reponen también esas colecciones (las de TODO más las propias de la tienda)."""

import pandas as pd
import streamlit as st
from app.components.estado import catalogo_tiendas, guardar_mantenedor, mantenedor
from app.components.login import puede, usuario_actual
from app.components.ui import hero, kpi_row, section

from forusight.data import bloqueos as B
from forusight.data import colecciones as COL
from forusight.data import mantenedores as MT
from forusight.data.corridas import hoy_lima

ss = st.session_state
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ETIQUETA = {MT.ASIGNAR: "Asignar temporada mínima", MT.QUITAR: "Volver al parámetro general"}

params = ss.params
general = params.surtido.temporada_desde or "(todas)"
hasta_liq = params.surtido.temporada_hasta_liquidadoras
extras = ", ".join(params.surtido.temporadas_reponer) or "ninguna"


def _tiendas() -> pd.DataFrame:
    """Catálogo de tiendas + las de la última corrida (maestro de BigQuery)."""
    cat = catalogo_tiendas()[["codigo_tienda", "nombre_tienda", "cadena"]].astype(str)
    res = ss.get("resultado")
    if res is not None and getattr(res, "tiendas", None) is not None:
        t = res.tiendas
        extra = pd.DataFrame(
            {
                "codigo_tienda": t["tienda_id"].astype(str),
                "nombre_tienda": t.get("nombre", pd.Series("", index=t.index)).astype(str),
                "cadena": t.get("cadena", pd.Series("", index=t.index)).astype(str),
            }
        )
        cat = pd.concat([cat, extra], ignore_index=True)
    return cat.drop_duplicates("codigo_tienda").reset_index(drop=True)


def _liquidadoras(tiendas: pd.DataFrame) -> set[str]:
    res = ss.get("resultado")
    dim = getattr(res, "tiendas", None) if res is not None else None
    cadenas = params.prioridad_tiendas.cadenas_liquidadoras
    liq = B.tiendas_liquidadoras(dim, cadenas)
    marca = tiendas["cadena"].str.upper().isin([str(c).upper() for c in cadenas])
    return liq | set(tiendas.loc[marca, "codigo_tienda"])


def _guardar_colecciones(tabla: pd.DataFrame, fecha_reporte: str, nombre: str) -> None:
    try:
        nuevos = COL.nuevos(tabla, fecha_reporte, usuario_actual(), hoy_lima().date())
        donde = guardar_mantenedor(
            COL.RUTA,
            COL.COLS_HIST,
            nuevos,
            usuario_actual(),
            f"colecciones activas por marca ({nombre})",
        )
        ss.flash_col = ("ok", f"Reporte cargado: {len(nuevos):,} filas. Guardado en {donde}.")
        ss.col_excel_n = ss.get("col_excel_n", 0) + 1  # vacía el archivo subido
    except Exception as exc:
        ss.flash_col = ("error", f"No se pudo guardar: {exc}")


def _guardar(carga: pd.DataFrame, accion: str, validas: set[str], liq: set[str]) -> None:
    """Callback de confirmar: vuelve a validar contra lo vigente en ESTE momento (un doble
    clic o un cambio de otra persona no duplica ni pisa nada) y guarda sólo las filas OK."""
    try:
        actual = MT.temporada_tienda_vigente(
            mantenedor(MT.RUTA_TEMPORADA_TIENDA, MT.COLS_TEMPORADA_TIENDA)
        )
        cambios, _ = MT.validar_temporadas_tienda(carga, accion, actual, validas, liq)
        if cambios.empty:
            ss.flash_tt = ("ok", "No había cambios pendientes: no se guardó nada.")
            return
        nuevos = MT.nuevos_temporada_tienda(cambios, accion, usuario_actual(), hoy_lima().date())
        donde = guardar_mantenedor(
            MT.RUTA_TEMPORADA_TIENDA,
            MT.COLS_TEMPORADA_TIENDA,
            nuevos,
            usuario_actual(),
            "temporada mínima por tienda (Excel)",
        )
        ss.flash_tt = ("ok", f"{len(nuevos):,} tienda(s) actualizadas. Guardado en {donde}.")
        ss.tt_excel_n = ss.get("tt_excel_n", 0) + 1  # vacía el archivo subido
    except Exception as exc:
        ss.flash_tt = ("error", f"No se pudo guardar: {exc}")


hero(
    "Temporadas por tienda",
    "Desde qué temporada comercial se repone cada tienda. Se repone esa temporada y las "
    "posteriores, en el orden comercial (INVIERNO mar–sep, VERANO sep–mar): INVIERNO 2026 → "
    "VERANO 2026 (con ESCOLAR 2026) → INVIERNO 2027…",
    eyebrow="Mantenedor",
)

tiendas = _tiendas()
liquidadoras = _liquidadoras(tiendas)
hist = mantenedor(MT.RUTA_TEMPORADA_TIENDA, MT.COLS_TEMPORADA_TIENDA)
vigente = MT.temporada_tienda_vigente(hist)
ultimo = (
    hist.assign(tienda_id=hist["tienda_id"].astype(str))
    .drop_duplicates("tienda_id", keep="last")
    .set_index("tienda_id")
)
tabla = tiendas.assign(
    temporada=lambda d: [
        f"hasta {hasta_liq} (jerarquía)"
        if c in liquidadoras and hasta_liq
        else vigente.get(c, general)
        for c in d["codigo_tienda"]
    ],
    regla=lambda d: [
        "Liquidadora: reposición por jerarquía"
        if c in liquidadoras and hasta_liq
        else ("Fuera de parámetros (Excel)" if c in vigente else "Parámetro general")
        for c in d["codigo_tienda"]
    ],
    usuario=lambda d: (
        d["codigo_tienda"]
        .map(ultimo["usuario"])
        .where(d["codigo_tienda"].isin(list(vigente)))
        .fillna("")
    ),
    fecha=lambda d: (
        d["codigo_tienda"]
        .map(ultimo["fecha"])
        .where(d["codigo_tienda"].isin(list(vigente)))
        .fillna("")
    ),
)
kpi_row(
    [
        ("Parámetro general", general, f"y además: {extras}", "settings"),
        ("Fuera de parámetros", f"{len(vigente):,}", "tiendas con temporada propia", "store"),
        (
            "Liquidadoras",
            f"{len(liquidadoras & set(tiendas['codigo_tienda'])):,}",
            f"DH, SE, FB: hasta {hasta_liq}" if hasta_liq else "sin regla especial",
            "layers",
        ),
    ]
)
if flash := ss.pop("flash_tt", None):
    (st.success if flash[0] == "ok" else st.error)(flash[1])
editable = puede("aprobar")
if not editable:
    st.info("Tu rol permite consultar las temporadas, no cambiarlas (rol `aprobador` o `admin`).")

# ---------------------------------------------------------------- carga por Excel
with st.container(key="card_tt_excel"):
    section(
        "Asignar o quitar con Excel",
        "1) Elige la acción · 2) sube el Excel con Cod Tienda y Temporada minima "
        "(VERANO AAAA o INVIERNO AAAA) · 3) revisa la validación y confirma",
        "upload",
    )
    accion = st.segmented_control(
        "Acción",
        [MT.ASIGNAR, MT.QUITAR],
        default=MT.ASIGNAR,
        format_func=ETIQUETA.get,
        key="tt_accion",
        required=True,
    )
    c1, c2 = st.columns([3, 1])
    archivo = c1.file_uploader(
        "Excel con Cod Tienda y Temporada minima",
        type=["xlsx", "xls", "csv"],
        key=f"tt_excel_{ss.get('tt_excel_n', 0)}",
        help="Una fila por tienda. Para volver al parámetro general basta el Cod Tienda.",
    )
    c2.download_button(
        "Plantilla",
        MT.plantilla_temporada_tienda(),
        file_name="plantilla_temporadas_tienda.xlsx",
        mime=XLSX,
        icon=":material/download:",
        width="stretch",
        on_click="ignore",
        key="tt_plantilla",
    )
    if archivo is not None:
        try:
            carga = MT.leer_carga_temporadas(archivo.getvalue(), archivo.name)
        except Exception as exc:
            st.error(f"No se pudo leer el archivo: {exc}")
            carga = None
        if carga is not None and carga.empty:
            st.warning("El archivo no tiene filas con datos.")
        elif carga is not None:
            cambios, val = MT.validar_temporadas_tienda(
                carga, accion, vigente, set(tiendas["codigo_tienda"]), liquidadoras
            )
            r = val["resultado"].astype(str)
            kpi_row(
                [
                    ("Filas leídas", f"{len(val):,}", archivo.name, "file"),
                    ("Se aplican", f"{len(cambios):,}", "tiendas", "check-circle"),
                    (
                        "Omitidas",
                        f"{int(r.str.startswith('Omitida').sum()):,}",
                        "sin cambio",
                        "layers",
                    ),
                    (
                        "Rechazadas",
                        f"{int(r.str.startswith('Rechazada').sum()):,}",
                        "errores del archivo",
                        "alert",
                    ),
                ]
            )
            nombre = dict(zip(tiendas["codigo_tienda"], tiendas["nombre_tienda"], strict=True))
            st.dataframe(
                val.assign(nombre=lambda d: d["tienda_id"].map(nombre).fillna(""))[
                    ["fila", "tienda_id", "nombre", "temporada", "actual", "resultado"]
                ],
                hide_index=True,
                width="stretch",
                height=240,
                column_config={
                    "fila": "Fila",
                    "tienda_id": "Cod Tienda",
                    "nombre": "Tienda",
                    "temporada": "Temporada mínima",
                    "actual": "Vigente hoy",
                    "resultado": "Validación",
                },
            )
            st.button(
                f"{ETIQUETA[accion]}: {len(cambios):,} tienda(s)",
                type="primary",
                icon=":material/save:",
                disabled=not editable or cambios.empty,
                key="tt_guardar",
                on_click=_guardar,
                args=(carga, accion, set(tiendas["codigo_tienda"]), liquidadoras),
            )

# ---------------------------------------------------------------- consulta
with st.container(key="card_tt_tabla"):
    section(
        "Temporada mínima vigente por tienda",
        "Lo que usará la próxima corrida. Descárgala, edítala y vuelve a subirla",
        "store",
    )
    solo = st.toggle("Ver sólo tiendas fuera de parámetros", key="tt_solo")
    vista = tabla.loc[tabla["regla"].str.startswith("Fuera")] if solo else tabla
    vista = vista.assign(_n=pd.to_numeric(vista["codigo_tienda"], errors="coerce")).sort_values(
        "_n"
    )
    st.dataframe(
        vista.drop(columns="_n"),
        hide_index=True,
        width="stretch",
        height=380,
        column_config={
            "codigo_tienda": "Cod Tienda",
            "nombre_tienda": "Tienda",
            "cadena": "Cadena",
            "temporada": "Temporada mínima",
            "regla": "Regla",
            "usuario": "Usuario",
            "fecha": "Fecha",
        },
    )
    configurables = tabla.loc[~tabla["regla"].str.startswith("Liquidadora")]
    st.download_button(
        "Descargar configuración (Excel de carga)",
        MT.plantilla_temporada_tienda(
            pd.DataFrame(
                {
                    "tienda_id": configurables["codigo_tienda"],
                    # vacía = sigue el parámetro general (no se fija al volver a subirla)
                    "temporada_minima": configurables["codigo_tienda"].map(vigente).fillna(""),
                    "nombre_tienda": configurables["nombre_tienda"],
                }
            )
        ),
        file_name=f"{hoy_lima():%Y%m%d}_Temporadas_por_tienda.xlsx",
        mime=XLSX,
        icon=":material/download:",
        on_click="ignore",
        key="tt_descargar",
        help="Todas las tiendas configurables; Temporada minima vacía = usa el parámetro "
        "general. Las liquidadoras no van: reponen por jerarquía.",
    )

with st.expander(f"Historial de cambios ({len(hist):,})", icon=":material/history:"):
    if hist.empty:
        st.caption("Todavía no hay tiendas fuera de parámetros.")
    else:
        st.dataframe(hist.iloc[::-1], hide_index=True, width="stretch")

# ---------------------------------------------------------------- colecciones activas (1037)
hist_col = mantenedor(COL.RUTA, COL.COLS_HIST)
ult_col = COL.vigente(hist_col)
mapa_col = params.surtido.colecciones_activas if ult_col is None else COL.a_mapa(ult_col)
origen_col = (
    "reporte del 15/10/2026 (incluido en la app)"
    if ult_col is None
    else f"reporte del {ult_col['fecha_reporte'].iloc[0] or '¿?'}, subido por "
    f"{ult_col['usuario'].iloc[0]} el {ult_col['fecha'].iloc[0]}"
)
with st.container(key="card_tt_colecciones"):
    section(
        "Colecciones activas por marca",
        "Reporte SAP 1037: las tiendas de línea reponen además estas colecciones de cada marca "
        "× clase (las de TODO y las propias de la tienda se suman). Las liquidadoras siguen "
        "por jerarquía",
        "layers",
    )
    st.caption(f"Vigente: {origen_col}.")
    if flash := ss.pop("flash_col", None):
        (st.success if flash[0] == "ok" else st.error)(flash[1])
    res_col = COL.resumen(mapa_col)
    nombre_t = dict(zip(tiendas["codigo_tienda"], tiendas["nombre_tienda"], strict=True))
    res_col.insert(
        1,
        "tienda",
        res_col["tienda_id"].map(lambda c: "Todas" if c == COL.TODAS else nombre_t.get(c, "")),
    )
    st.dataframe(
        res_col,
        hide_index=True,
        width="stretch",
        height=320,
        column_config={
            "tienda_id": "Código centro",
            "tienda": "Tienda",
            "clase": "Clase",
            "marca": "Marca",
            "colecciones": "Colecciones activas",
        },
    )
    sin_tienda = sorted(
        set(res_col["tienda_id"]) - set(tiendas["codigo_tienda"]) - {COL.TODAS}, key=str
    )
    if sin_tienda:
        st.warning(f"Centros del reporte que no están en el maestro de tiendas: {sin_tienda}.")
    en_liq = sorted(set(res_col["tienda_id"]) & liquidadoras, key=str)
    if en_liq:
        st.caption(f"No se aplican en liquidadoras (reponen por jerarquía): {en_liq}.")
    archivo_col = st.file_uploader(
        "Subir un reporte 1037 nuevo (reemplaza al vigente completo)",
        type=["xlsx", "xls", "csv"],
        key=f"col_excel_{ss.get('col_excel_n', 0)}",
        help="Excel «Colecciones Activas Marca» tal como sale de SAP: Código centro, Clase, "
        "Marca y Coleccion activa (TODO = todas las tiendas).",
    )
    if archivo_col is not None:
        try:
            nueva, fecha_rep = COL.leer_reporte(archivo_col.getvalue(), archivo_col.name)
        except Exception as exc:
            st.error(f"No se pudo leer el archivo: {exc}")
            nueva = None
        if nueva is not None and nueva.empty:
            st.warning("El reporte no tiene filas con datos.")
        elif nueva is not None:
            antes = {(k, c) for k, v in mapa_col.items() for c in v}
            despues = {(k, c) for k, v in COL.a_mapa(nueva).items() for c in v}
            kpi_row(
                [
                    ("Filas", f"{len(nueva):,}", fecha_rep or archivo_col.name, "file"),
                    (
                        "Nuevas",
                        f"{len(despues - antes):,}",
                        "colección × marca × tienda",
                        "check-circle",
                    ),
                    ("Salen", f"{len(antes - despues):,}", "dejan de estar activas", "alert"),
                ]
            )
            st.button(
                "Reemplazar colecciones activas",
                type="primary",
                icon=":material/save:",
                disabled=not editable,
                key="col_guardar",
                on_click=_guardar_colecciones,
                args=(nueva, fecha_rep, archivo_col.name),
            )
