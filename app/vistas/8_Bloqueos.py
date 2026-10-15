"""Mantenedor de bloqueos en una sola pantalla: elegir bloquear o desbloquear, subir un Excel
(COD MOD COL, Cod Tienda), revisar la validación y confirmar. Además se consulta y se descarga
el maestro completo de bloqueos vigente. Los cambios se aplican en la próxima corrida."""

import pandas as pd
import streamlit as st
from app.components.estado import (
    bloqueos_de,
    bloqueos_manuales,
    clave_bloqueos,
    guardar_bloqueos_manuales,
)
from app.components.login import puede, usuario_actual
from app.components.ui import hero, kpi_row, section

from forusight.data import bloqueos as B
from forusight.data import cadenas as CAD
from forusight.data import temporadas as TEMP
from forusight.data.corridas import hoy_lima

ss = st.session_state
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ETIQUETA = {B.BLOQUEAR: "Bloquear", B.DESBLOQUEAR: "Desbloquear"}


def _tiendas_validas(catalogo: pd.DataFrame) -> set[str]:
    """Tiendas que se pueden bloquear: catálogo + las de la última corrida (maestro BigQuery)."""
    validas = set(catalogo["codigo_tienda"].astype(str))
    res = ss.get("resultado")
    if res is not None and getattr(res, "tiendas", None) is not None:
        validas |= set(res.tiendas["tienda_id"].astype(str))
    return validas


def _cargar_excel(detalle: pd.DataFrame, accion: str, tiendas: set[str]) -> None:
    """Callback de confirmar: vuelve a validar contra los bloqueos de ESTE momento (un doble
    clic o una carga de otra persona no duplica ni pisa nada) y guarda sólo las filas OK."""
    try:
        efectivos = bloqueos_de(clave_bloqueos())
        ok = B.validas(B.validar_carga(detalle, accion, efectivos, tiendas))
        if ok.empty:
            ss.flash_bloqueos = ("info", "No había cambios pendientes: no se guardó nada.")
            return
        nuevos = B.nuevos_desde_carga(ok, accion, ss.get("blq_motivo", ""), usuario_actual())
        hecho = "Bloqueados" if accion == B.BLOQUEAR else "Desbloqueados"
        ss.flash_bloqueos = (
            "ok",
            f"{hecho} {len(nuevos):,} modelo-color × tienda desde el Excel. "
            f"Guardado en {guardar_bloqueos_manuales(nuevos, usuario_actual())}.",
        )
        ss.blq_excel_n = ss.get("blq_excel_n", 0) + 1  # vacía el archivo subido
        ss.blq_motivo = ""
    except Exception as exc:
        ss.flash_bloqueos = ("error", f"No se pudo guardar la carga: {exc}")


@st.cache_data(max_entries=4, show_spinner=False)
def _maestro(
    clave: str, _efectivos: pd.DataFrame, _manuales: pd.DataFrame, _catalogo
) -> pd.DataFrame:
    """Maestro completo; ``clave`` cambia con cada carga por Excel (huella de los manuales)."""
    return B.maestro_completo(_efectivos, _manuales, _catalogo, TEMP.por_defecto())


@st.cache_data(max_entries=2, show_spinner="Generando el maestro de bloqueos…")
def _maestro_xlsx(clave: str, fecha: str, _maestro: pd.DataFrame) -> bytes:
    return B.maestro_excel(_maestro, fecha)


hero(
    "Bloqueos de productos",
    "Un modelo-color bloqueado para una tienda no se repone ahí, aunque la tienda lo venda. "
    "Elige la acción, sube el Excel, revisa la validación y confirma; el cambio se aplica en "
    "la próxima corrida.",
    eyebrow="Mantenedor",
)

catalogo = CAD.catalogo_tiendas()[["codigo_tienda", "nombre_tienda", "cadena"]].drop_duplicates(
    "codigo_tienda"
)
nombre = dict(zip(catalogo["codigo_tienda"], catalogo["nombre_tienda"], strict=True))
nombre[B.TODAS] = "TODAS LAS TIENDAS"
clave = clave_bloqueos()
efectivos = bloqueos_de(clave)
manuales = B.vigentes(bloqueos_manuales())
kpi_row(
    [
        ("Bloqueos vigentes", f"{len(efectivos):,}", "modelo-color × tienda", "shield"),
        (
            "Del reporte de bloqueos",
            f"{int(efectivos['origen'].astype(str).str.startswith('reporte').sum()):,}",
            f"maestro del {B.FECHA_POR_DEFECTO}",
            "layers",
        ),
        (
            "Bloqueados por Excel",
            f"{int(manuales['accion'].eq(B.BLOQUEAR).sum()):,}",
            "cargados en este mantenedor",
            "lock",
        ),
        (
            "Desbloqueados por Excel",
            f"{int(manuales['accion'].eq(B.DESBLOQUEAR).sum()):,}",
            "vuelven a reponerse",
            "check-circle",
        ),
    ]
)
if flash := ss.pop("flash_bloqueos", None):  # mensaje del último guardado (sobrevive al rerun)
    {"ok": st.success, "info": st.info}.get(flash[0], st.error)(flash[1])
editable = puede("aprobar")
if not editable:
    st.info(
        "Tu rol permite ver y descargar los bloqueos, no cambiarlos (rol `aprobador` o `admin`)."
    )

# ---------------------------------------------------------------- 1. carga por Excel
with st.container(key="card_excel"):
    section(
        "Bloquear o desbloquear con Excel",
        "1) Elige la acción · 2) sube el Excel con COD MOD COL y Cod Tienda (vacío = todas las "
        "tiendas) · 3) revisa la validación y confirma",
        "upload",
    )
    accion = st.segmented_control(
        "Acción",
        [B.BLOQUEAR, B.DESBLOQUEAR],
        default=B.BLOQUEAR,
        format_func=ETIQUETA.get,
        key="blq_accion",
        required=True,
    )
    c1, c2 = st.columns([3, 1])
    archivo = c1.file_uploader(
        "Excel con COD MOD COL y Cod Tienda",
        type=["xlsx", "xls", "csv"],
        key=f"blq_excel_{ss.get('blq_excel_n', 0)}",
        help="Una fila por modelo-color (p. ej. HP10201162490-N11) y tienda. "
        "Cod Tienda vacío = todas las tiendas.",
    )
    c2.download_button(
        "Plantilla",
        B.plantilla_excel(),
        file_name="plantilla_bloqueos.xlsx",
        mime=XLSX,
        icon=":material/download:",
        width="stretch",
        on_click="ignore",
        key="blq_plantilla",
    )
    if archivo is not None:
        tiendas_ok = _tiendas_validas(catalogo)
        try:
            detalle = B.leer_carga_detalle(archivo.getvalue(), archivo.name)
        except Exception as exc:
            st.error(f"No se pudo leer el archivo: {exc}")
            detalle = None
        if detalle is not None and detalle.empty:
            st.warning("El archivo no tiene filas con datos.")
        elif detalle is not None:
            val = B.validar_carga(detalle, accion, efectivos, tiendas_ok)
            r = val["resultado"].astype(str)
            n_ok = int(r.str.startswith("OK").sum())
            n_omit = int(r.str.startswith("Omitida").sum())
            n_rech = int(r.str.startswith("Rechazada").sum())
            kpi_row(
                [
                    ("Filas leídas", f"{len(val):,}", archivo.name, "file"),
                    ("Se aplican", f"{n_ok:,}", ETIQUETA[accion].lower(), "check-circle"),
                    ("Omitidas", f"{n_omit:,}", "repetidas o sin cambio", "layers"),
                    ("Rechazadas", f"{n_rech:,}", "errores del archivo", "alert"),
                ]
            )
            if n_rech:
                st.warning(
                    f"{n_rech:,} fila(s) con error no se guardan. Corrígelas en el Excel o "
                    "confirma sólo las válidas."
                )
            solo_obs = st.toggle("Ver sólo filas omitidas o rechazadas", key="blq_solo_obs")
            vista = val.loc[~r.str.startswith("OK")] if solo_obs else val
            st.dataframe(
                vista.assign(nombre=lambda d: d["tienda_id"].astype(str).map(nombre).fillna(""))[
                    ["fila", "modelo_color_id", "tienda_id", "nombre", "resultado"]
                ],
                hide_index=True,
                width="stretch",
                height=240,
                column_config={
                    "fila": "Fila",
                    "modelo_color_id": "COD MOD COL",
                    "tienda_id": "Cod Tienda",
                    "nombre": "Tienda",
                    "resultado": "Validación",
                },
            )
            v1, v2 = st.columns([3, 1], vertical_alignment="bottom")
            v1.text_input("Motivo", key="blq_motivo", placeholder="p. ej. Colección no activa")
            v2.download_button(
                "Descargar validación",
                val.to_csv(index=False).encode("utf-8-sig"),
                file_name="validacion_bloqueos.csv",
                mime="text/csv",
                icon=":material/download:",
                on_click="ignore",
                key="blq_validacion",
                width="stretch",
            )
            st.button(
                f"{ETIQUETA[accion]} {n_ok:,} modelo-color × tienda",
                type="primary",
                icon=":material/lock:" if accion == B.BLOQUEAR else ":material/lock_open:",
                disabled=not editable or n_ok == 0,
                key="btn_excel",
                on_click=_cargar_excel,
                args=(detalle, accion, tiendas_ok),
            )

# ---------------------------------------------------------------- 2. maestro y consulta
with st.container(key="card_consultar"):
    section(
        "Maestro de bloqueos",
        f"Reporte de bloqueos del {B.FECHA_POR_DEFECTO} + cargas por Excel, por tienda y "
        "modelo-color. Descárgalo completo o consulta una tienda o un modelo",
        "search",
    )
    maestro = _maestro(clave, efectivos, bloqueos_manuales(), catalogo)
    hoy = f"{hoy_lima():%d/%m/%Y}"
    st.download_button(
        f"Descargar maestro completo ({len(maestro):,} filas)",
        lambda: _maestro_xlsx(clave, hoy, maestro),
        file_name=f"{hoy_lima():%Y%m%d}_Maestro_Bloqueos.xlsx",
        mime=XLSX,
        icon=":material/download:",
        on_click="ignore",
        key="blq_maestro",
        help="Todos los bloqueos vigentes con tienda, cadena, temporada, motivo y origen. "
        "Las columnas COD MOD COL y Cod Tienda se pueden volver a subir arriba.",
    )
    f1, f2 = st.columns(2)
    tienda_f = f1.selectbox(
        "Tienda",
        ["(todas)"]
        + sorted(
            efectivos["tienda_id"].astype(str).unique(), key=lambda x: int(x) if x.isdigit() else -1
        ),
        format_func=lambda t: t if t == "(todas)" else f"{t} · {nombre.get(t, '')}",
        key="dbl_tienda",
    )
    texto = f2.text_input("Modelo-color contiene", key="dbl_texto", placeholder="p. ej. HP1020116")
    vista = maestro
    if tienda_f != "(todas)":
        cod = "" if tienda_f == B.TODAS else tienda_f
        vista = vista.loc[vista["Cod Tienda"].astype(str).eq(cod)]
    if texto.strip():
        vista = vista.loc[
            vista["COD MOD COL"].astype(str).str.contains(texto.strip().upper(), regex=False)
        ]
    if tienda_f == "(todas)" and not texto.strip():
        st.caption(f"{len(maestro):,} bloqueos vigentes: elige una tienda o escribe un modelo.")
    else:
        st.caption(
            f"{len(vista):,} bloqueos encontrados"
            + (" (se muestran 2.000)" if len(vista) > 2000 else "")
        )
        st.dataframe(vista.head(2000), hide_index=True, width="stretch", height=380)
        st.download_button(
            "Descargar como Excel de carga",
            B.a_excel_carga(
                pd.DataFrame(
                    {
                        "tienda_id": vista["Cod Tienda"].astype(str).replace("", B.TODAS),
                        "modelo_color_id": vista["COD MOD COL"],
                    }
                )
            ),
            file_name="bloqueos_seleccion.xlsx",
            mime=XLSX,
            icon=":material/download:",
            on_click="ignore",
            key="dbl_descargar",
            help="Estos bloqueos en el formato de carga (COD MOD COL, Cod Tienda): deja las "
            "filas a cambiar y súbelo arriba con la acción que corresponda.",
        )

# ---------------------------------------------------------------- 3. historial
with st.expander("Historial de cargas por Excel", icon=":material/history:"):
    hist = bloqueos_manuales()
    if hist.empty:
        st.caption("Todavía no hay bloqueos ni desbloqueos cargados por Excel.")
    else:
        st.dataframe(
            hist.iloc[::-1].assign(tienda=lambda d: d["tienda_id"].map(nombre).fillna("")),
            hide_index=True,
            width="stretch",
        )
        st.download_button(
            "Descargar historial (CSV)",
            hist.to_csv(index=False).encode("utf-8-sig"),
            file_name="bloqueos_manuales.csv",
            mime="text/csv",
        )
