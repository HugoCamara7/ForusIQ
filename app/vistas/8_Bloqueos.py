"""Mantenedor de bloqueos: bloquear un modelo-color en una o todas las tiendas (no se repone)
o sacarlo del bloqueo, a mano o con un Excel (Mod-Col, Cod Tienda). Los cambios quedan guardados y se aplican en la próxima corrida."""

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

ss = st.session_state


def _modelos_elegidos() -> list[str]:
    pegados = ss.get("blq_texto", "") or ""
    return list(
        dict.fromkeys(list(ss.get("blq_modelos") or []) + pegados.replace(",", "\n").split())
    )


def _cargar_excel(carga: pd.DataFrame, accion: str) -> None:
    """Callback de la carga masiva: guarda los movimientos y limpia el archivo subido."""
    try:
        nuevos = B.nuevos_desde_carga(
            carga, accion, ss.get("blq_excel_motivo", ""), usuario_actual()
        )
        hecho = "Bloqueados" if accion == B.BLOQUEAR else "Desbloqueados"
        ss.flash_bloqueos = (
            "ok",
            f"{hecho} {len(nuevos):,} modelo-color × tienda desde el Excel. "
            f"Guardado en {guardar_bloqueos_manuales(nuevos, usuario_actual())}.",
        )
        ss.blq_excel_n = ss.get("blq_excel_n", 0) + 1  # vacía el archivo subido
        ss.blq_excel_motivo = ""
    except Exception as exc:
        ss.flash_bloqueos = ("error", f"No se pudo guardar la carga: {exc}")


def _bloquear() -> None:
    """Callback: lee los widgets ya actualizados, guarda y limpia el formulario."""
    try:
        nuevos = B.nuevos_manuales(
            list(ss.blq_tiendas),
            _modelos_elegidos(),
            B.BLOQUEAR,
            ss.get("blq_motivo", ""),
            usuario_actual(),
        )
        ss.flash_bloqueos = (
            "ok",
            f"Bloqueado. Guardado en {guardar_bloqueos_manuales(nuevos, usuario_actual())}.",
        )
        ss.blq_tiendas, ss.blq_modelos, ss.blq_texto, ss.blq_motivo = [], [], "", ""
    except Exception as exc:
        ss.flash_bloqueos = ("error", f"No se pudo guardar el bloqueo: {exc}")


def _desbloquear(marcados: pd.DataFrame) -> None:
    try:
        nuevos = pd.concat(
            [
                B.nuevos_manuales(
                    [t], [m], B.DESBLOQUEAR, ss.get("dbl_motivo", ""), usuario_actual()
                )
                for t, m in zip(marcados["tienda_id"], marcados["modelo_color_id"], strict=True)
            ],
            ignore_index=True,
        )
        ss.flash_bloqueos = (
            "ok",
            f"Desbloqueado. Guardado en {guardar_bloqueos_manuales(nuevos, usuario_actual())}.",
        )
        ss.dbl_motivo = ""
    except Exception as exc:
        ss.flash_bloqueos = ("error", f"No se pudo guardar el desbloqueo: {exc}")


hero(
    "Bloqueos de productos",
    "Un modelo-color bloqueado para una tienda no se repone ahí, aunque la tienda lo venda. "
    "Aquí se bloquea o se saca del bloqueo; el cambio se aplica en la próxima corrida.",
    eyebrow="Mantenedor",
)

tiendas = CAD.catalogo_tiendas()[["codigo_tienda", "nombre_tienda"]].drop_duplicates()
nombre = dict(zip(tiendas["codigo_tienda"], tiendas["nombre_tienda"], strict=True))
nombre[B.TODAS] = "TODAS LAS TIENDAS"
efectivos = bloqueos_de(clave_bloqueos())
manuales = B.vigentes(bloqueos_manuales())
kpi_row(
    [
        ("Bloqueos vigentes", f"{len(efectivos):,}", "modelo-color × tienda", "shield"),
        (
            "Del reporte de bloqueos",
            f"{int(efectivos['origen'].astype(str).str.startswith('reporte').sum()):,}",
            f"cargado del {B.FECHA_POR_DEFECTO} o subido",
            "layers",
        ),
        (
            "Bloqueados a mano",
            f"{int(manuales['accion'].eq(B.BLOQUEAR).sum()):,}",
            "en este mantenedor",
            "lock",
        ),
        (
            "Desbloqueados a mano",
            f"{int(manuales['accion'].eq(B.DESBLOQUEAR).sum()):,}",
            "vuelven a reponerse",
            "check-circle",
        ),
    ]
)
if flash := ss.pop("flash_bloqueos", None):  # mensaje del último guardado (sobrevive al rerun)
    (st.success if flash[0] == "ok" else st.error)(flash[1])
editable = puede("aprobar")
if not editable:
    st.info("Tu rol permite ver los bloqueos, no cambiarlos (rol `aprobador` o `admin`).")

# ---------------------------------------------------------------- bloquear
with st.container(key="card_bloquear"):
    section("Bloquear", "El modelo-color deja de reponerse en las tiendas elegidas", "lock")
    res = ss.get("resultado")
    sugeridos = (
        sorted(res.detalle["modelo_color_id"].astype(str).unique()) if res is not None else []
    )
    c1, c2 = st.columns(2)
    elegidas = c1.multiselect(
        "Tiendas",
        [B.TODAS] + sorted(tiendas["codigo_tienda"], key=lambda x: int(x) if x.isdigit() else 0),
        format_func=lambda t: f"{t} · {nombre.get(t, '')}" if t != B.TODAS else nombre[t],
        key="blq_tiendas",
        placeholder="Elige tiendas (o «Todas las tiendas»)",
    )
    de_corrida = c2.multiselect(
        "Modelos-color de la última corrida",
        sugeridos,
        key="blq_modelos",
        placeholder="Elige modelos-color"
        if sugeridos
        else "Ejecuta una corrida para elegir de la lista",
    )
    pegados = st.text_area(
        "O pega modelos-color (uno por línea, p. ej. HP10201162490-N11)",
        key="blq_texto",
        height=90,
    )
    motivo = st.text_input("Motivo", key="blq_motivo", placeholder="p. ej. Colección no activa")
    modelos = list(dict.fromkeys(de_corrida + pegados.replace(",", "\n").split()))
    st.button(
        f"Bloquear {len(modelos) * len(elegidas):,} modelo-color × tienda",
        type="primary",
        icon=":material/lock:",
        disabled=not (editable and modelos and elegidas),
        key="btn_bloquear",
        on_click=_bloquear,
    )

# ---------------------------------------------------------------- carga masiva por Excel
with st.container(key="card_excel"):
    section(
        "Cargar Excel",
        "Columnas Mod-Col y Cod Tienda; sin Cod Tienda, el modelo-color aplica a todas las tiendas",
        "upload",
    )
    c1, c2 = st.columns([3, 1])
    archivo = c1.file_uploader(
        "Excel con Mod-Col y Cod Tienda",
        type=["xlsx", "xls", "csv"],
        key=f"blq_excel_{ss.get('blq_excel_n', 0)}",
        help="Una fila por modelo-color (p. ej. HP10201162490-N11). Cod Tienda vacío = todas.",
    )
    c2.download_button(
        "Plantilla",
        B.plantilla_excel(),
        file_name="plantilla_bloqueos.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        icon=":material/download:",
        width="stretch",
        on_click="ignore",
        key="blq_plantilla",
    )
    if archivo is not None:
        try:
            carga = B.leer_carga(archivo.getvalue(), archivo.name)
        except Exception as exc:
            st.error(f"No se pudo leer el archivo: {exc}")
            carga = None
        if carga is not None and len(carga):
            todas = int(carga["tienda_id"].eq(B.TODAS).sum())
            st.caption(
                f"{carga['modelo_color_id'].nunique():,} modelos-color · "
                f"{len(carga) - todas:,} por tienda · {todas:,} para todas las tiendas"
            )
            st.dataframe(
                carga.assign(tienda=lambda d: d["tienda_id"].map(nombre).fillna("")).rename(
                    columns={
                        "modelo_color_id": "Mod-Col",
                        "tienda_id": "Cod Tienda",
                        "tienda": "Tienda",
                    }
                ),
                hide_index=True,
                width="stretch",
                height=220,
            )
            accion = st.segmented_control(
                "Acción",
                [B.BLOQUEAR, B.DESBLOQUEAR],
                default=B.BLOQUEAR,
                format_func={B.BLOQUEAR: "Bloquear", B.DESBLOQUEAR: "Desbloquear"}.get,
                key="blq_excel_accion",
                required=True,
            )
            st.text_input(
                "Motivo", key="blq_excel_motivo", placeholder="p. ej. Colección no activa"
            )
            st.button(
                f"{'Bloquear' if accion == B.BLOQUEAR else 'Desbloquear'} {len(carga):,} "
                "modelo-color × tienda",
                type="primary",
                icon=":material/lock:" if accion == B.BLOQUEAR else ":material/lock_open:",
                disabled=not editable,
                key="btn_excel",
                on_click=_cargar_excel,
                args=(carga, accion),
            )
        elif carga is not None:
            st.warning("El archivo no tiene filas con Mod-Col.")

# ---------------------------------------------------------------- consultar y desbloquear
with st.container(key="card_desbloquear"):
    section(
        "Consultar y desbloquear",
        "Busca por tienda o modelo; marca los que vuelven a reponerse",
        "search",
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
    vista = efectivos
    if tienda_f != "(todas)":
        vista = vista.loc[vista["tienda_id"].astype(str).eq(tienda_f)]
    if texto.strip():
        vista = vista.loc[
            vista["modelo_color_id"].astype(str).str.contains(texto.strip().upper(), regex=False)
        ]
    if tienda_f == "(todas)" and not texto.strip():
        st.caption(f"{len(efectivos):,} bloqueos vigentes: elige una tienda o escribe un modelo.")
    else:
        st.caption(
            f"{len(vista):,} bloqueos encontrados"
            + (" (se muestran 2.000)" if len(vista) > 2000 else "")
        )
        tabla = vista.head(2000).assign(
            tienda=lambda d: d["tienda_id"].astype(str).map(nombre).fillna(""),
            desbloquear=False,
        )[["desbloquear", "tienda_id", "tienda", "modelo_color_id", "origen"]]
        editado = st.data_editor(
            tabla,
            hide_index=True,
            width="stretch",
            height=380,
            disabled=["tienda_id", "tienda", "modelo_color_id", "origen"],
            column_config={
                "desbloquear": st.column_config.CheckboxColumn("Desbloquear"),
                "tienda_id": "Código",
                "tienda": "Tienda",
                "modelo_color_id": "Modelo-color",
                "origen": "Origen del bloqueo",
            },
            key="editor_desbloqueo",
        )
        marcados = editado.loc[editado["desbloquear"]]
        motivo_d = st.text_input("Motivo del desbloqueo", key="dbl_motivo")
        st.button(
            f"Desbloquear {len(marcados):,} seleccionados",
            icon=":material/lock_open:",
            disabled=not (editable and len(marcados)),
            key="btn_desbloquear",
            on_click=_desbloquear,
            args=(marcados,),
        )

# ---------------------------------------------------------------- historial
with st.expander("Historial de cambios manuales", icon=":material/history:"):
    hist = bloqueos_manuales()
    if hist.empty:
        st.caption("Todavía no hay bloqueos ni desbloqueos manuales.")
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
