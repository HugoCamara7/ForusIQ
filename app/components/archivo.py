"""Archivo Forusight en un clic: se genera al presionar el botón (en otro hilo) y queda en
caché por corrida y aprobación, así que la segunda descarga es inmediata."""

from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

from app.components.estado import ajustes, entradas_de_la_corrida
from forusight.data.corridas import hoy_lima
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
    return a_excel_forusight(tabla, hoy_lima(), stock_al=_res.resumen.get("stock_al"))


def tabla_archivo(res, entradas, params, cd_id, cantidad) -> pd.DataFrame:
    if getattr(entradas, "reporte", None) is not None:  # corrida sobre el reporte del día
        from app.components.estado import bloqueos_de, clave_bloqueos
        from forusight.data.bloqueos import fuera_de_surtido
        from forusight.data.reporte import tabla_para_archivo

        rep = entradas.reporte
        mc = rep["Código Modelo"].astype("string") + "-" + rep["Código Color"].astype("string")
        bloq, fuera = fuera_de_surtido(
            rep["Código Centro"].astype(str).str.lstrip("0"),
            mc,
            rep.get("Temporada comercial"),
            bloqueos_de(clave_bloqueos()),
            params.surtido.temporadas_reponer,
        )
        return tabla_para_archivo(rep, res.detalle, cantidad, (bloq | fuera).to_numpy())
    return construir_tabla(
        res.detalle,
        entradas.ventas,
        # con la temporada del maestro: la que usó el motor (la de ARTI puede ser otra)
        res.productos if getattr(res, "productos", None) is not None else entradas.dim_producto,
        # tiendas con el calendario aplicado (lead time y período de revisión de cada una)
        res.tiendas if getattr(res, "tiendas", None) is not None else entradas.dim_tienda,
        params,
        res.fecha_corte,
        cd_id,
        cantidad,
        getattr(entradas, "stock_tienda", None),
    )


def _aprobar_al_descargar(res) -> None:
    """Callback del botón: aprueba la propuesta tal cual (corre antes del script)."""
    from app.components.aprobacion import aprobar

    nivel, msg = aprobar(res)
    if nivel == "ok":
        st.toast(msg, icon=":material/verified:")


def boton_archivo(key: str, en_barra: bool = False) -> None:
    """Un solo botón: aprueba y descarga el archivo Forusight."""
    from app.components.login import puede

    ss = st.session_state
    res = ss.get("resultado")
    entradas = entradas_de_la_corrida() if res is not None else None
    if res is None or entradas is None:
        return
    aprobada = ss.get("aprobacion_confirmada") == res.run_id
    puede_aprobar = puede("aprobar")
    # se aprueba la propuesta tal cual: el archivo es el mismo antes y después de aprobar
    cantidad = cantidad_aprobada()
    aprob = (
        hashlib.sha1(pd.util.hash_pandas_object(cantidad, index=False).values).hexdigest()
        if cantidad is not None
        else "propuesta"
    )
    params, cd_id = ss.params, ajustes().cd_id

    def generar() -> bytes:
        return _excel(res.run_id, aprob, res, entradas, params, cd_id, cantidad)

    if aprobada:
        etiqueta, ayuda = (
            "Descargar archivo aprobado",
            "La distribución ya está aprobada.",
        )
    elif puede_aprobar:
        etiqueta = "Aprobar y descargar"
        ayuda = "Aprueba la distribución tal cual y descarga el archivo Forusight. Al cargarlo en "
        ayuda += "el sistema, los pedidos cuentan como tránsito en la próxima corrida."
    else:
        etiqueta, ayuda = "Descargar propuesta", "Tu rol no aprueba: descarga la propuesta."
    extra = (
        {"on_click": _aprobar_al_descargar, "args": (res,)}
        if puede_aprobar and not aprobada
        else {"on_click": "ignore"}  # descargar no necesita volver a correr toda la app
    )
    (st.sidebar if en_barra else st).download_button(
        etiqueta,
        data=generar,
        file_name=nombre_archivo(hoy_lima()),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        icon=":material/verified:" if aprobada else ":material/download:",
        width="stretch",
        key=key,
        help=ayuda,
        **extra,
    )
