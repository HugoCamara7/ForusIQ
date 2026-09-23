import streamlit as st
from app.components.estado import resultado_o_aviso
from app.components.kpis import mostrar_kpis

from forusight.engine.reasons import DESCRIPCION_CODIGOS

st.title("Dashboard")
res = resultado_o_aviso()
if res is not None:
    det = res.detalle
    mostrar_kpis(res.resumen, det)

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Unidades por tienda")
        por_tienda = det.groupby("tienda_id")["cantidad"].sum().sort_values(ascending=False)
        st.bar_chart(por_tienda, horizontal=True, y_label="", x_label="Unidades")
    with c2:
        st.subheader("Unidades por categoría")
        st.bar_chart(det.groupby("categoria")["cantidad"].sum(), x_label="", y_label="Unidades")

    st.subheader("Filas por motivo")
    motivos = (
        det.groupby("motivo_codigo")
        .agg(filas=("sku", "size"), unidades=("cantidad", "sum"))
        .sort_values("filas", ascending=False)
        .reset_index()
    )
    motivos["descripcion"] = motivos["motivo_codigo"].map(DESCRIPCION_CODIGOS)
    st.dataframe(motivos, hide_index=True, width="stretch")
