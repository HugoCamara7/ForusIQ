import streamlit as st
from app.components.estado import SETTINGS, resultado_o_aviso
from app.components.filtros import filtros_detalle
from app.components.motivo import panel_motivo
from app.components.tablas import tabla_recomendaciones
from app.components.ui import hero

hero(
    "Recomendaciones",
    "Tienda → modelo → talla → cantidad, con el motivo de cada fila. Filtra y revisa antes de aprobar.",
    eyebrow="Detalle",
)
res = resultado_o_aviso()


@st.fragment
def vista(detalle):
    """Fragmento: filtrar o seleccionar sólo re-ejecuta esta sección."""
    filtrado = filtros_detalle(detalle, key="rec")
    st.caption(f"{len(filtrado):,} filas · {int(filtrado['cantidad'].sum()):,} unidades")
    evento = tabla_recomendaciones(filtrado, SETTINGS.cd_id, key="rec_tabla")
    filas = evento.selection.rows if evento is not None else []
    if filas:
        panel_motivo(filtrado.iloc[filas[0]])
    else:
        st.caption("Selecciona una fila para ver el motivo completo.")


if res is not None:
    vista(res.detalle)
