"""Mantenedor de rutas en un solo cuadro: qué días sale el camión del CD a cada centro comercial.

«Esta semana» muestra las fechas reales (con feriados y cambios); lo que se marca o desmarca vale
sólo para esa fecha. «Ruta fija» son los días de siempre. Lo usan la corrida de la pantalla y la
de las 6:30."""

import pandas as pd
import streamlit as st
from app.components.estado import (
    guardar_excepciones,
    guardar_ruta_semanal,
    rutas_vigentes,
)
from app.components.login import puede, usuario_actual
from app.components.ui import hero

from forusight.data import calendario as CAL
from forusight.data import rutas as RU
from forusight.data.corridas import hoy_lima

ss = st.session_state
base, excepciones = rutas_vigentes()
tiendas = RU.tiendas_por_mall(base)
hoy = hoy_lima()
ss.setdefault("rt_fecha", RU.fechas_semana(hoy)[0].date())
editable = puede("aprobar")
nombres_tienda = tiendas.groupby("mall")["nombre_tienda"].apply(lambda s: ", ".join(sorted(s)))

hero(
    "Rutas de despacho",
    "Qué días sale el camión del CD a cada centro comercial. Todas las tiendas de un mall "
    "reciben el mismo día. La corrida de cada día toma la ruta sola.",
    eyebrow="Rutas · feriados",
)
if flash := ss.pop("flash_rutas", None):
    (st.success if flash[0] == "ok" else st.error)(flash[1])
if not editable:
    st.info("Tu rol permite ver las rutas, no cambiarlas (rol `aprobador` o `admin`).")


def _mover_semana(dias: int) -> None:
    ss.rt_fecha = (pd.Timestamp(ss.rt_fecha) + pd.Timedelta(days=dias)).date()


def _a_lunes() -> None:
    ss.rt_fecha = RU.fechas_semana(ss.rt_fecha)[0].date()


def _guardar_feriados(fechas: list) -> None:
    elegidos = [f for f in fechas if RU.etiqueta(f) in (ss.get("rt_feriados") or [])]
    try:
        todo = RU.marcar_feriados(excepciones, fechas, elegidos, usuario_actual())
        donde = guardar_excepciones(todo, usuario_actual())
        texto = ", ".join(RU.etiqueta(f) for f in elegidos) or "ninguno"
        ss.flash_rutas = ("ok", f"Feriados de la semana: {texto}. Guardado en {donde}.")
    except Exception as exc:
        ss.flash_rutas = ("error", f"No se pudo guardar: {exc}")


def _guardar_semana_fechas(fecha, tabla: pd.DataFrame) -> None:
    try:
        todo = RU.aplicar_semana(base, excepciones, fecha, tabla, usuario_actual())
        donde = guardar_excepciones(todo, usuario_actual())
        ss.flash_rutas = ("ok", f"Cambios de la semana guardados en {donde}.")
    except Exception as exc:
        ss.flash_rutas = ("error", f"No se pudo guardar: {exc}")


def _guardar_semana(tabla: pd.DataFrame) -> None:
    try:
        nueva = pd.DataFrame(
            {
                "mall": tabla["Mall"],
                "patrones": tabla["Reconoce"],
                "dias": tabla[CAL.DIAS].apply(
                    lambda f: ",".join(d for d in CAL.DIAS if bool(f[d])), axis=1
                ),
            }
        )
        donde = guardar_ruta_semanal(nueva, usuario_actual())
        ss.flash_rutas = ("ok", f"Ruta fija guardada en {donde}.")
    except Exception as exc:
        ss.flash_rutas = ("error", f"No se pudo guardar la ruta fija: {exc}")


def _tabla_fija(b: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Mall": b["mall"],
            **{d: b["dias"].map(lambda s, d=d: d in str(s).split(",")) for d in CAL.DIAS},
            "Reconoce": b["patrones"],
        }
    )


with st.container(key="card_rutas"):
    c_modo, c_ant, c_fecha, c_sig = st.columns(
        [3.2, 0.42, 1.1, 0.42], gap="small", vertical_alignment="bottom"
    )
    modo = c_modo.segmented_control(
        "Ver",
        ["Esta semana", "Ruta fija"],
        default="Esta semana",
        key="rt_modo",
        help="«Esta semana»: fechas reales, con feriados y cambios. «Ruta fija»: los días de "
        "siempre de cada mall.",
    )
    por_fechas = modo != "Ruta fija"
    if por_fechas:
        c_ant.button(
            "",
            icon=":material/chevron_left:",
            on_click=_mover_semana,
            args=(-7,),
            key="rt_ant",
            help="Semana anterior",
            width="stretch",
        )
        c_fecha.date_input("Semana del", key="rt_fecha", format="DD/MM/YYYY", on_change=_a_lunes)
        c_sig.button(
            "",
            icon=":material/chevron_right:",
            on_click=_mover_semana,
            args=(7,),
            key="rt_sig",
            help="Semana siguiente",
            width="stretch",
        )

    if por_fechas:
        fechas = RU.fechas_semana(ss.rt_fecha)
        etiquetas = [RU.etiqueta(f) for f in fechas]
        fer = [RU.etiqueta(f) for f in RU.feriados(excepciones, fechas)]
        ss.rt_feriados = fer  # el control refleja siempre lo guardado
        st.pills(
            "Feriados (no sale el camión a ningún mall)",
            etiquetas,
            selection_mode="multi",
            key="rt_feriados",
            on_change=_guardar_feriados,
            args=(fechas,),
            disabled=not editable,
            help="Un clic marca o quita el feriado y se guarda al instante (🚫 en el cuadro). "
            "Para mover el despacho de un mall, marca su casilla en otro día y guarda. "
            "● = hoy.",
        )
        tabla = RU.tabla_semana(base, excepciones, ss.rt_fecha)
        tabla["Tiendas"] = tabla["Mall"].map(nombres_tienda).fillna("")
        columnas = {
            e: st.column_config.CheckboxColumn(
                f"🚫 {e}" if e in fer else ("● " + e if f == hoy else e), width=84
            )
            for e, f in zip(etiquetas, fechas, strict=True)
        }
        editada = st.data_editor(
            tabla,
            hide_index=True,
            width="stretch",
            disabled=["Mall", "Tiendas"] if editable else True,
            column_config={**columnas, "Tiendas": st.column_config.TextColumn(width="large")},
            key=f"rt_editor_{fechas[0]:%Y%m%d}_{RU.huella(base, excepciones)}",
        )
        cambios = RU.cambios_semana(base, excepciones, ss.rt_fecha)
        st.caption(
            "Cambios de esta semana: " + " · ".join(cambios)
            if cambios
            else "Esta semana sigue la ruta fija, sin cambios."
        )
        st.button(
            "Guardar cambios de la semana",
            type="primary",
            icon=":material/save:",
            disabled=not editable or editada.equals(tabla),
            on_click=_guardar_semana_fechas,
            args=(ss.rt_fecha, editada.drop(columns="Tiendas")),
            key="btn_guardar_fechas",
        )
    else:
        fija = _tabla_fija(base)
        fija["Tiendas"] = base["mall"].map(nombres_tienda).fillna("")
        editada = st.data_editor(
            fija,
            hide_index=True,
            width="stretch",
            num_rows="dynamic" if editable else "fixed",
            disabled=["Tiendas"] if editable else True,
            column_config={
                **{d: st.column_config.CheckboxColumn(d, width="small") for d in CAL.DIAS},
                "Reconoce": st.column_config.TextColumn(
                    help="Textos del centro comercial o del nombre de la tienda que la asignan "
                    "a ese mall (separados por |)"
                ),
                "Tiendas": st.column_config.TextColumn("Tiendas", width="large"),
            },
            key="rt_editor_semana",
        )
        c1, c2 = st.columns(2)
        c1.button(
            "Guardar ruta fija",
            type="primary",
            icon=":material/save:",
            disabled=not editable,
            on_click=_guardar_semana,
            args=(editada,),
            key="btn_guardar_semana",
        )
        c2.button(
            "Volver a la ruta original",
            icon=":material/restart_alt:",
            disabled=not editable,
            on_click=lambda: _guardar_semana(_tabla_fija(RU.base_por_defecto())),
            key="btn_ruta_original",
        )

    sin_mall = tiendas.loc[tiendas["mall"].eq(""), "nombre_tienda"].tolist()
    if sin_mall:
        st.warning(
            f"{len(sin_mall)} tienda(s) no caen en ningún mall y reciben cualquier día: "
            f"{', '.join(sorted(sin_mall))}. Agrega su centro comercial en «Ruta fija» → "
            "«Reconoce»."
        )
