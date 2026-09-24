"""Archivo Forusight en un clic: se genera al presionar el botón (en otro hilo) y queda en
caché por corrida y aprobación, así que la segunda descarga es inmediata."""

from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

from app.components.estado import ajustes, entradas_de_la_corrida
from forusight.export.archivo import a_excel_forusight, construir_tabla, nombre_archivo


def cantidad_aprobada() -> pd.Series | None:
    """Cantidades aprobadas de la corrida actual (None si se exporta la propuesta)."""
    ss = st.session_state
    res = ss.get("resultado")
    if res is None or ss.get("aprobacion_confirmada") != res.run_id or ss.get("aprobacion") is None:
        return None
    cant = ss.aprobacion.set_index(["tienda_id", "sku"])["cantidad_aprobada"]
    det = res.detalle
    return (
        det.join(cant, on=["tienda_id", "sku"])["cantidad_aprobada"]
        .fillna(det["cantidad"])
        .astype(int)
    )


@st.cache_data(max_entries=6, show_spinner=False)
def _excel(run_id: str, aprob: str, _res, _entradas, _params, cd_id: str, _cantidad) -> bytes:
    tabla = tabla_archivo(_res, _entradas, _params, cd_id, _cantidad)
    return a_excel_forusight(tabla, pd.Timestamp.today())


def tabla_archivo(res, entradas, params, cd_id, cantidad) -> pd.DataFrame:
    return construir_tabla(
        res.detalle,
        entradas.ventas,
        entradas.dim_producto,
        entradas.dim_tienda,
        params,
        res.fecha_corte,
        cd_id,
        cantidad,
    )


def boton_archivo(key: str, en_barra: bool = False) -> None:
    ss = st.session_state
    res = ss.get("resultado")
    entradas = entradas_de_la_corrida() if res is not None else None
    if res is None or entradas is None:
        return
    cantidad = cantidad_aprobada()
    aprob = (
        hashlib.sha1(pd.util.hash_pandas_object(cantidad, index=False).values).hexdigest()
        if cantidad is not None
        else "propuesta"
    )
    params, cd_id = ss.params, ajustes().cd_id

    def generar() -> bytes:
        return _excel(res.run_id, aprob, res, entradas, params, cd_id, cantidad)

    (st.sidebar if en_barra else st).download_button(
        "Descargar archivo Forusight" if not en_barra else "Descargar archivo",
        data=generar,
        file_name=nombre_archivo(pd.Timestamp.today()),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        icon=":material/download:",
        width="stretch",
        key=key,
        help="Aprobado" if cantidad is not None else "Propuesta de la corrida (sin aprobar)",
    )
