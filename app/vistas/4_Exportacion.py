import pandas as pd
import streamlit as st
from app.components.estado import SETTINGS, entradas_de_la_corrida, resultado_o_aviso
from app.components.ui import hero

from forusight.export.excel import a_csv, a_excel
from forusight.export.neogistica import a_excel_neogistica, construir_tabla, nombre_archivo

hero(
    "Exportación",
    "Archivo en el formato de los reportes de Neogística, listo para enviar.",
    eyebrow="Neogística",
)
res = resultado_o_aviso()

if res is not None:
    ss = st.session_state
    detalle = res.detalle
    cantidad = None
    aprobada = ss.get("aprobacion_confirmada") == res.run_id and ss.aprobacion is not None
    if aprobada:
        cant = ss.aprobacion.set_index(["tienda_id", "sku"])["cantidad_aprobada"]
        cantidad = detalle.join(cant, on=["tienda_id", "sku"])["cantidad_aprobada"]
        cantidad = cantidad.fillna(detalle["cantidad"]).astype(int)
        st.success("Se exporta la distribución **aprobada**.")
    else:
        st.warning("La corrida no está aprobada: se exporta la **propuesta**.")

    entradas = entradas_de_la_corrida()
    if entradas is None:
        st.info("Vuelve a ejecutar la corrida para generar el archivo.")
        st.stop()
    tabla = construir_tabla(
        detalle,
        entradas.ventas,
        entradas.dim_producto,
        entradas.dim_tienda,
        ss.params,
        res.fecha_corte,
        SETTINGS.cd_id,
        cantidad,
    )
    envio = tabla["Cantidad Pedida Final [un]"] > 0
    c = st.columns(4)
    c[0].metric("Filas evaluadas", f"{len(tabla):,}")
    c[1].metric("Filas con envío", f"{int(envio.sum()):,}")
    c[2].metric("Unidades", f"{int(tabla['Cantidad Pedida Final [un]'].sum()):,}")
    c[3].metric("Pendiente", f"{int(tabla['Pendiente Reposición'].sum()):,}")

    solo_envio = st.toggle("Ver sólo filas con envío", value=True)
    st.dataframe(
        tabla.loc[envio] if solo_envio else tabla, hide_index=True, width="stretch", height=420
    )
    hoy = pd.Timestamp.today()
    st.download_button(
        "Descargar archivo formato Neogística",
        a_excel_neogistica(tabla, hoy),
        file_name=nombre_archivo(hoy),
        type="primary",
        width="stretch",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    st.caption(
        "Mismo formato que el reporte de Neogística (Hoja1 + hoja Resumen por tienda). "
        "Sólo incluye columnas con dato real y filas con actividad."
    )

    with st.expander("Otros formatos (detalle Forusight con motivo)"):
        col = "cantidad"
        det = detalle
        if cantidad is not None:
            det = detalle.assign(cantidad_aprobada=cantidad)
            col = "cantidad_aprobada"
        c1, c2 = st.columns(2)
        c1.download_button(
            "Excel con motivos",
            a_excel(det, SETTINGS.cd_id, res.resumen, col),
            file_name=f"forusight_{res.run_id}.xlsx",
            width="stretch",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        c2.download_button(
            "CSV",
            a_csv(det, SETTINGS.cd_id, col),
            file_name=f"forusight_{res.run_id}.csv",
            mime="text/csv",
            width="stretch",
        )
