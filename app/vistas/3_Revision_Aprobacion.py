import pandas as pd
import streamlit as st
from app.components.estado import SETTINGS, repositorio, resultado_o_aviso
from app.components.filtros import filtros_detalle
from app.components.login import puede, usuario_actual

from forusight.data.repository import SinAlmacenamiento

st.title("Revisión y aprobación")
res = resultado_o_aviso()


def base_aprobacion(detalle: pd.DataFrame) -> pd.DataFrame:
    d = detalle.loc[detalle["necesidad"] > 0]
    return pd.DataFrame(
        {
            "run_id": d["run_id"],
            "tienda_id": d["tienda_id"],
            "modelo_color_id": d["modelo_color_id"],
            "sku": d["sku"],
            "talla": d["talla"],
            "categoria": d["categoria"],
            "motivo_codigo": d["motivo_codigo"],
            "cantidad": d["cantidad"],
            "necesidad": d["necesidad"],
            "stock_cd_disponible": d["stock_cd_disponible"],
            "cantidad_propuesta": d["cantidad"],
            "cantidad_aprobada": d["cantidad"],
            "comentario": "",
        }
    ).reset_index(drop=True)


def violaciones_cd(aprob: pd.DataFrame) -> pd.DataFrame:
    g = aprob.groupby("sku").agg(
        aprobado=("cantidad_aprobada", "sum"), disponible=("stock_cd_disponible", "first")
    )
    return g.loc[g["aprobado"] > g["disponible"]]


if res is not None:
    ss = st.session_state
    if ss.aprobacion is None or ss.aprobacion["run_id"].iat[0] != res.run_id:
        ss.aprobacion = base_aprobacion(res.detalle)

    vista = filtros_detalle(ss.aprobacion, key="apr")
    with st.form("form_aprobacion"):
        editado = st.data_editor(
            vista,
            column_order=[
                "tienda_id",
                "sku",
                "talla",
                "motivo_codigo",
                "necesidad",
                "stock_cd_disponible",
                "cantidad_propuesta",
                "cantidad_aprobada",
                "comentario",
            ],
            disabled=[c for c in vista.columns if c not in ("cantidad_aprobada", "comentario")],
            column_config={
                "cantidad_aprobada": st.column_config.NumberColumn(
                    "Aprobada", min_value=0, step=1, format="%d"
                ),
                "stock_cd_disponible": st.column_config.NumberColumn(f"Stock CD {SETTINGS.cd_id}"),
            },
            hide_index=True,
            width="stretch",
            key="editor_aprobacion",
            height=460,
        )
        aplicar = st.form_submit_button("Aplicar cambios")
    if aplicar:
        ss.aprobacion.loc[editado.index, ["cantidad_aprobada", "comentario"]] = editado[
            ["cantidad_aprobada", "comentario"]
        ]
        st.toast("Cambios aplicados (aún no confirmados)")

    aprob = ss.aprobacion
    malas = violaciones_cd(aprob)
    c1, c2, c3 = st.columns(3)
    c1.metric("Propuesto", int(aprob["cantidad_propuesta"].sum()))
    c2.metric("Aprobado", int(aprob["cantidad_aprobada"].sum()))
    c3.metric(
        "Filas modificadas", int((aprob["cantidad_aprobada"] != aprob["cantidad_propuesta"]).sum())
    )
    if not malas.empty:
        st.error("La aprobación supera el stock disponible del CD en estos SKU:")
        st.dataframe(malas, width="stretch")

    if not puede("aprobar"):
        st.info("Tu rol permite revisar, no aprobar. Pide el rol `aprobador` en [app_auth.roles].")
    if st.button(
        "Confirmar aprobación",
        type="primary",
        disabled=not malas.empty or not puede("aprobar"),
    ):
        try:
            destino = repositorio(ss.fuente).guardar_aprobacion(res.run_id, aprob, usuario_actual())
            ss.aprobacion_confirmada = res.run_id
            st.success(f"Aprobación de la corrida {res.run_id} guardada en {destino}.")
        except SinAlmacenamiento as exc:
            ss.aprobacion_confirmada = res.run_id  # vale para exportar aunque no se guarde
            st.info(str(exc))
        except Exception as exc:
            ss.aprobacion_confirmada = res.run_id
            st.warning(
                f"La aprobación quedó confirmada en esta sesión, pero no se pudo guardar: {exc}"
            )

    if ss.get("aprobacion_confirmada") == res.run_id:
        st.download_button(
            "Descargar aprobación (CSV)",
            aprob.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"aprobacion_{res.run_id}.csv",
            mime="text/csv",
        )
