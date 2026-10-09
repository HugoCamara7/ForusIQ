"""Panel «Corridas guardadas»: los Excel que dejó la corrida diaria de las 6:30.

Se listan del repositorio privado de datos (la misma carpeta y el mismo nombre que usa
``scripts/corrida_diaria.py``, vía ``forusight.data.corridas``). El listado se cachea 5
minutos -- el panel se dibuja en cada clic y un viaje a GitHub por clic deja la pantalla en
gris -- y cada archivo se baja sólo al pulsar su botón.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.components.estado import _store_bloqueos, huella_config
from forusight.data import corridas as C

MESES_A_LISTAR = 2
MAXIMO_EN_PANEL = 7 * C.SEMANAS_A_GUARDAR * 2  # definitiva + preliminar


@st.cache_data(ttl=300, show_spinner=False, max_entries=4)
def _listado(huella: str, mes: str) -> list[dict]:
    store = _store_bloqueos()
    if store is None:
        return []
    archivos = []
    for i in range(MESES_A_LISTAR):
        fecha = pd.Timestamp(mes) - pd.DateOffset(months=i)
        try:
            archivos += store.listar(C.carpeta_del_mes(fecha))
        except Exception:  # sin GitHub el panel no puede tumbar el dashboard
            continue
    return [
        {k: a.get(k) for k in ("name", "path", "sha", "fecha", "preliminar")}
        for a in C.corridas_listadas(archivos)
    ]


@st.cache_data(max_entries=8, show_spinner=False)
def _contenido(path: str, sha: str) -> bytes:
    # ``sha`` va en la clave: si el archivo se reemplaza, la caché no sirve el viejo.
    store = _store_bloqueos()
    return (store.leer(path) if store is not None else None) or b""


def panel() -> None:
    if _store_bloqueos() is None:
        return
    hoy = C.ahora_lima()
    lista = _listado(huella_config(), f"{hoy:%Y-%m}-01")
    with st.expander(f"Corridas guardadas ({len(lista)})", icon=":material/history:"):
        st.caption(
            "Todos los días a las 6:30 se corre la distribución sola y el Excel queda aquí con "
            "su fecha. Se guardan sólo las de esta semana (lunes a domingo): el lunes se "
            "borran las de la semana anterior. PRELIMINAR = se corrió sin el cierre de ayer "
            "en la carga diaria."
        )
        if not lista:
            st.info("Todavía no hay corridas guardadas.")
            return
        for a in lista[:MAXIMO_EN_PANEL]:
            etiqueta = f"{a['fecha']:%d/%m/%Y}" + (" · PRELIMINAR" if a["preliminar"] else "")
            st.download_button(
                etiqueta,
                data=lambda a=a: _contenido(a["path"], a["sha"] or ""),
                file_name=a["name"],
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                icon=":material/download:",
                key=f"corrida_{a['name']}",
                on_click="ignore",
                width="stretch",
            )
