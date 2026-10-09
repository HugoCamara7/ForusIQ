"""Página «Descargas»: los Excel que dejó la corrida diaria de las 6:30 (semana en curso).

Se listan del repositorio privado de datos (la misma carpeta y el mismo nombre que usa
``scripts/corrida_diaria.py``, vía ``forusight.data.corridas``). El listado se cachea 5
minutos -- el panel se dibuja en cada clic y un viaje a GitHub por clic deja la pantalla en
gris -- y cada archivo se baja sólo al pulsar su botón.
"""

from __future__ import annotations

from html import escape

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


DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def pagina() -> None:
    from app.components.ui import html, issue_box

    if _store_bloqueos() is None:
        issue_box(
            "info",
            "Sin repositorio de datos",
            "Las corridas de las 6:30 se guardan en el repositorio privado de datos: configúralo "
            "en los secrets ([ticketing] o [forusight] github_repository / github_token).",
        )
        return
    hoy = C.ahora_lima()
    lista = _listado(huella_config(), f"{hoy:%Y-%m}-01")
    if not lista:
        issue_box(
            "info",
            "Todavía no hay corridas esta semana",
            "Cada día a las 6:30 la distribución se corre sola y el Excel aparece aquí.",
        )
        return
    with st.container(key="card_descargas"):
        for a in lista[:MAXIMO_EN_PANEL]:
            f = a["fecha"]
            es_hoy = f.normalize() == pd.Timestamp(hoy.date())
            c_txt, c_btn = st.columns([3, 1.2], vertical_alignment="center")
            with c_txt:
                etiquetas = ('<span class="dl-tag dl-hoy">Hoy</span>' if es_hoy else "") + (
                    '<span class="dl-tag dl-pre">Preliminar</span>' if a["preliminar"] else ""
                )
                html(
                    f'<div class="dl-fila"><b>{DIAS[f.weekday()]} {f:%d/%m/%Y}</b>'
                    f"{etiquetas}<span>{escape(a['name'])}</span></div>"
                )
            with c_btn:
                st.download_button(
                    "Descargar",
                    data=lambda a=a: _contenido(a["path"], a["sha"] or ""),
                    file_name=a["name"],
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    icon=":material/download:",
                    key=f"corrida_{a['name']}",
                    on_click="ignore",
                    width="stretch",
                )
