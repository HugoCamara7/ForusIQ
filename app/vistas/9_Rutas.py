"""Mantenedor de rutas: qué días sale el camión del CD a cada centro comercial y las excepciones
por fecha (feriados, imprevistos). Lo usan la corrida de la pantalla y la de las 6:30."""

import pandas as pd
import streamlit as st
from app.components.estado import (
    guardar_excepciones,
    guardar_ruta_semanal,
    rutas_vigentes,
)
from app.components.login import puede, usuario_actual
from app.components.ui import hero, kpi_row, section

from forusight.data import calendario as CAL
from forusight.data import rutas as RU
from forusight.data.corridas import hoy_lima

ss = st.session_state
base, excepciones = rutas_vigentes()
tiendas = RU.tiendas_por_mall(base)
hoy = hoy_lima()
editable = puede("aprobar")
nombres_tienda = tiendas.groupby("mall")["nombre_tienda"].apply(lambda s: ", ".join(sorted(s)))

hero(
    "Rutas de despacho",
    "Qué días sale el camión del CD a cada centro comercial. Todas las tiendas de un mall "
    "reciben el mismo día. Si un feriado o un imprevisto cambia la ruta, regístralo abajo: la "
    "corrida de ese día lo toma sola.",
    eyebrow="Rutas · feriados · imprevistos",
)
proximas = excepciones.loc[pd.to_datetime(excepciones["fecha"]) >= hoy]
hoy_salen = [m for m in base["mall"] if RU.despacha(m, hoy, base, excepciones)]
kpi_row(
    [
        (
            f"Hoy {CAL.dia_semana(hoy)} {hoy:%d/%m}",
            f"{len(hoy_salen)} malls",
            ", ".join(hoy_salen) or "sin despacho",
            "truck",
        ),
        ("Malls con ruta", f"{int(base['dias'].ne('').sum())}", "ruta semanal", "store"),
        ("Excepciones próximas", f"{len(proximas)}", "feriados e imprevistos", "alert"),
    ]
)
if flash := ss.pop("flash_rutas", None):
    (st.success if flash[0] == "ok" else st.error)(flash[1])
if not editable:
    st.info("Tu rol permite ver las rutas, no cambiarlas (rol `aprobador` o `admin`).")


# ---------------------------------------------------------------- próximos días
with st.container(key="card_rutas_calendario"):
    section(
        "Próximos 14 días",
        "✓ despacha · ✓ extra = despacho fuera de su día · ✗ excepción = le tocaba y no sale",
        "clock",
    )
    st.dataframe(RU.semana(base, excepciones, hoy, 14), hide_index=True, width="stretch")

    dia = st.date_input("Ver un día", value=hoy.date(), format="DD/MM/YYYY", key="rt_dia")
    salen = [m for m in base["mall"] if RU.despacha(m, dia, base, excepciones)]
    detalle = pd.DataFrame(
        {
            "Mall": salen,
            "Por qué": [
                (
                    f"Excepción: {e['motivo'] or RU.ACCIONES[e['accion']]}"
                    if (e := RU.excepcion_de(m, dia, excepciones)) is not None
                    else f"Ruta semanal ({RU.dias_de(m, base)})"
                )
                for m in salen
            ],
            "Tiendas": [nombres_tienda.get(m, "") for m in salen],
        }
    )
    if detalle.empty:
        st.warning(f"El {pd.Timestamp(dia):%d/%m/%Y} no sale el camión a ningún mall.")
    else:
        st.dataframe(detalle, hide_index=True, width="stretch")


# ---------------------------------------------------------------- excepciones
def _guardar_nuevas(nuevas: pd.DataFrame, texto: str) -> None:
    try:
        todo = RU.agregar(excepciones, nuevas)
        ss.flash_rutas = (
            "ok",
            f"{texto} Guardado en {guardar_excepciones(todo, usuario_actual())}.",
        )
    except Exception as exc:
        ss.flash_rutas = ("error", f"No se pudo guardar: {exc}")


def _agregar_excepcion() -> None:
    try:
        nuevas = RU.nuevas_excepciones(
            [ss.rt_exc_fecha],
            list(ss.rt_exc_malls),
            ss.rt_exc_accion,
            ss.rt_exc_motivo,
            usuario_actual(),
        )
    except ValueError as exc:
        ss.flash_rutas = ("error", str(exc))
        return
    _guardar_nuevas(nuevas, f"{RU.ACCIONES[ss.rt_exc_accion]} el {ss.rt_exc_fecha:%d/%m/%Y}.")
    ss.rt_exc_malls, ss.rt_exc_motivo = [], ""


def _mover() -> None:
    try:
        nuevas = RU.mover_despacho(
            ss.rt_mov_desde,
            ss.rt_mov_hacia,
            list(ss.rt_mov_malls),
            ss.rt_mov_motivo,
            usuario_actual(),
        )
    except ValueError as exc:
        ss.flash_rutas = ("error", str(exc))
        return
    _guardar_nuevas(
        nuevas, f"Despacho del {ss.rt_mov_desde:%d/%m} movido al {ss.rt_mov_hacia:%d/%m}."
    )
    ss.rt_mov_malls, ss.rt_mov_motivo = [], ""


def _quitar(fechas_malls: pd.DataFrame) -> None:
    clave = excepciones["fecha"].dt.strftime("%Y-%m-%d") + "|" + excepciones["mall"]
    quitar = set(fechas_malls["fecha"].dt.strftime("%Y-%m-%d") + "|" + fechas_malls["mall"])
    try:
        resto = excepciones.loc[~clave.isin(quitar)].reset_index(drop=True)
        donde = guardar_excepciones(resto, usuario_actual())
        ss.flash_rutas = ("ok", f"Se quitaron {len(quitar)} excepciones. Guardado en {donde}.")
    except Exception as exc:
        ss.flash_rutas = ("error", f"No se pudo guardar: {exc}")


opciones_malls = [RU.TODOS] + list(base["mall"])
formato_mall = lambda m: "Todos los malls" if m == RU.TODOS else m  # noqa: E731

with st.container(key="card_rutas_excepciones"):
    section(
        "Feriados e imprevistos",
        "Cambian la ruta sólo en esa fecha; la ruta semanal no se toca",
        "alert",
    )
    t_mover, t_una = st.tabs(["Mover un despacho (feriado)", "No despacha / despacho extra"])
    with t_mover:
        st.caption(
            "Ejemplo: el jueves 08/10 es feriado y el despacho de Plaza Norte sale el miércoles "
            "07/10. Se registran dos excepciones: 08/10 no despacha y 07/10 despacho extra."
        )
        c1, c2 = st.columns(2)
        c1.date_input("Fecha que no sale", key="rt_mov_desde", format="DD/MM/YYYY", value=hoy)
        c2.date_input("Nueva fecha", key="rt_mov_hacia", format="DD/MM/YYYY", value=hoy)
        st.multiselect(
            "Malls",
            list(base["mall"]),
            key="rt_mov_malls",
            placeholder="Elige los malls que cambian de día",
        )
        st.text_input(
            "Motivo", key="rt_mov_motivo", placeholder="p. ej. Feriado Combate de Angamos"
        )
        st.button(
            "Mover despacho",
            type="primary",
            icon=":material/swap_horiz:",
            disabled=not (editable and ss.get("rt_mov_malls")),
            on_click=_mover,
            key="btn_mover",
        )
    with t_una:
        c1, c2 = st.columns(2)
        c1.date_input("Fecha", key="rt_exc_fecha", format="DD/MM/YYYY", value=hoy)
        c2.radio(
            "Ese día",
            list(RU.ACCIONES),
            format_func=RU.ACCIONES.get,
            key="rt_exc_accion",
            horizontal=True,
        )
        st.multiselect(
            "Malls",
            opciones_malls,
            format_func=formato_mall,
            key="rt_exc_malls",
            placeholder="Elige malls (o «Todos los malls» para un feriado nacional)",
        )
        st.text_input("Motivo", key="rt_exc_motivo", placeholder="p. ej. Paro de transportistas")
        st.button(
            "Registrar excepción",
            type="primary",
            icon=":material/event_busy:",
            disabled=not (editable and ss.get("rt_exc_malls")),
            on_click=_agregar_excepcion,
            key="btn_excepcion",
        )

    if excepciones.empty:
        st.caption("No hay excepciones registradas.")
    else:
        vista = excepciones.assign(
            Quitar=False,
            Fecha=[f"{CAL.dia_semana(f)} {f:%d/%m/%Y}" for f in excepciones["fecha"]],
            Mall=excepciones["mall"].map(formato_mall),
            Acción=excepciones["accion"].map(RU.ACCIONES),
        ).sort_values("fecha", ascending=False)
        ver_pasadas = st.toggle("Ver también las pasadas", key="rt_pasadas")
        if not ver_pasadas:
            vista = vista.loc[vista["fecha"] >= hoy]
        editado = st.data_editor(
            vista[["Quitar", "Fecha", "Mall", "Acción", "motivo", "usuario", "registrado"]],
            hide_index=True,
            width="stretch",
            disabled=["Fecha", "Mall", "Acción", "motivo", "usuario", "registrado"],
            column_config={"motivo": "Motivo", "usuario": "Usuario", "registrado": "Registrado"},
            key="rt_editor_exc",
        )
        marcadas = vista.loc[editado["Quitar"].to_numpy(), ["fecha", "mall"]]
        st.button(
            f"Quitar {len(marcadas)} excepción(es)",
            icon=":material/delete:",
            disabled=not (editable and len(marcadas)),
            on_click=_quitar,
            args=(marcadas,),
            key="btn_quitar_exc",
        )


# ---------------------------------------------------------------- ruta semanal
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
        ss.flash_rutas = ("ok", f"Ruta semanal guardada en {donde}.")
    except Exception as exc:
        ss.flash_rutas = ("error", f"No se pudo guardar la ruta semanal: {exc}")


with st.container(key="card_rutas_semana"):
    section(
        "Ruta semanal",
        "Días fijos de cada mall. «Reconoce»: textos del centro comercial o del nombre de la "
        "tienda que la asignan a ese mall (separados por |)",
        "grid",
    )
    semana = pd.DataFrame(
        {
            "Mall": base["mall"],
            **{d: base["dias"].map(lambda s, d=d: d in str(s).split(",")) for d in CAL.DIAS},
            "Reconoce": base["patrones"],
            "Tiendas": base["mall"].map(nombres_tienda).fillna(""),
        }
    )
    editada = st.data_editor(
        semana,
        hide_index=True,
        width="stretch",
        num_rows="dynamic" if editable else "fixed",
        disabled=["Tiendas"] if editable else True,
        column_config={
            **{d: st.column_config.CheckboxColumn(d, width="small") for d in CAL.DIAS},
            "Tiendas": st.column_config.TextColumn("Tiendas", width="large"),
        },
        key="rt_editor_semana",
    )
    sin_mall = tiendas.loc[tiendas["mall"].eq(""), "nombre_tienda"].tolist()
    if sin_mall:
        st.warning(
            f"{len(sin_mall)} tienda(s) no caen en ningún mall y reciben cualquier día: "
            f"{', '.join(sorted(sin_mall))}. Agrega su centro comercial en «Reconoce»."
        )
    c1, c2 = st.columns(2)
    c1.button(
        "Guardar ruta semanal",
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
        on_click=lambda: _guardar_semana(
            pd.DataFrame(
                {
                    "Mall": RU.base_por_defecto()["mall"],
                    **{
                        d: RU.base_por_defecto()["dias"].map(lambda s, d=d: d in str(s).split(","))
                        for d in CAL.DIAS
                    },
                    "Reconoce": RU.base_por_defecto()["patrones"],
                }
            )
        ),
        key="btn_ruta_original",
    )
