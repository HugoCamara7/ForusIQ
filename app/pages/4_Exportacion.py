import streamlit as st
from app.components.estado import SETTINGS, resultado_o_aviso

from forusight.export.excel import a_csv, a_excel, a_formato_display

st.title("Exportación")
res = resultado_o_aviso()

if res is not None:
    ss = st.session_state
    detalle = res.detalle
    aprobada = ss.get("aprobacion_confirmada") == res.run_id and ss.aprobacion is not None
    columna = "cantidad"
    if aprobada:
        cant = ss.aprobacion.set_index(["tienda_id", "sku"])["cantidad_aprobada"]
        detalle = detalle.join(cant, on=["tienda_id", "sku"])
        detalle["cantidad_aprobada"] = detalle["cantidad_aprobada"].fillna(0).astype(int)
        columna = "cantidad_aprobada"
        st.success("Se exporta la distribución **aprobada**.")
    else:
        st.warning("La corrida no está aprobada: se exporta la **propuesta**.")

    vista = a_formato_display(detalle, SETTINGS.cd_id, columna)
    st.dataframe(
        vista.loc[vista["Cantidad a distribuir"] > 0], hide_index=True, width="stretch", height=420
    )
    c1, c2 = st.columns(2)
    c1.download_button(
        "Descargar Excel",
        a_excel(detalle, SETTINGS.cd_id, res.resumen, columna),
        file_name=f"forusight_{res.run_id}.xlsx",
        width="stretch",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    c2.download_button(
        "Descargar CSV",
        a_csv(detalle, SETTINGS.cd_id, columna),
        file_name=f"forusight_{res.run_id}.csv",
        mime="text/csv",
        width="stretch",
    )
    st.caption("Formato WMS/ERP pendiente de definición con Neogistica.")
