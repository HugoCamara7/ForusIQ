import streamlit as st
from app.components.archivo import boton_archivo, cantidad_aprobada, tabla_archivo
from app.components.estado import SETTINGS, entradas_de_la_corrida, resultado_o_aviso
from app.components.ui import hero, kpi_row, section

from forusight.export.excel import a_csv, a_excel

hero(
    "Exportación",
    "Archivo Forusight con la distribución por tienda, modelo y talla, listo para enviar.",
    eyebrow="Archivo Forusight",
)
res = resultado_o_aviso()

if res is not None:
    ss = st.session_state
    entradas = entradas_de_la_corrida()
    if entradas is None:
        st.info("Vuelve a ejecutar la corrida para generar el archivo.")
        st.stop()
    cantidad = cantidad_aprobada()
    tabla = tabla_archivo(res, entradas, ss.params, SETTINGS.cd_id, cantidad)
    envio = tabla["Cantidad Pedida Final [un]"] > 0
    kpi_row(
        [
            (
                "Unidades",
                f"{int(tabla['Cantidad Pedida Final [un]'].sum()):,}",
                "a enviar",
                "truck",
            ),
            ("Filas con envío", f"{int(envio.sum()):,}", "tienda × talla", "table"),
            ("Tiendas", f"{tabla.loc[envio, 'Código Centro'].nunique()}", "reciben", "store"),
            (
                "Pendiente",
                f"{int(tabla['Pendiente Reposición'].sum()):,}",
                "necesidad sin stock en el CD",
                "clock",
            ),
        ]
    )
    st.caption(
        ("Distribución **aprobada**." if cantidad is not None else "Propuesta de la corrida.")
        + " El archivo trae la hoja de distribución y una hoja Resumen por tienda."
    )
    boton_archivo("archivo_exportacion")

    if getattr(entradas, "reporte", None) is not None:
        from forusight.data.reporte import coincidencia

        c = coincidencia(entradas.reporte, res.detalle)
        section("Comparación con el reporte del día", "Cantidad a enviar, fila por fila", "check")
        kpi_row(
            [
                (
                    "Coincidencia",
                    f"{c['pct_filas']:.2%}",
                    f"{c['filas_iguales']:,} de {c['filas']:,} filas",
                    "check-circle",
                ),
                ("Unidades reporte", f"{c['unidades_reporte']:,}", "cantidad original", "file"),
                (
                    "Unidades Forusight",
                    f"{c['unidades_forusight']:,}",
                    "cantidad recalculada",
                    "truck",
                ),
            ]
        )

    from forusight.engine.disponibilidad import control_cd

    ctrl = control_cd(res.detalle, cantidad)
    ctrl = ctrl.loc[ctrl["enviado"] > 0]
    exceso = ctrl.loc[ctrl["queda"] < 0]
    pend = (ss.get("diagnostico") or {}).get("pendientes") or {}
    section(
        "Control de stock del CD",
        "Lo enviado nunca supera el stock disponible de cada SKU",
        "shield",
    )
    kpi_row(
        [
            (
                "SKU que sobrepasan",
                f"{len(exceso)}",
                "debe ser 0" if len(exceso) else "ninguno supera el stock del CD",
                "check-circle" if not len(exceso) else "alert",
            ),
            (
                "SKU con envío",
                f"{len(ctrl):,}",
                f"{int(ctrl['enviado'].sum()):,} unidades",
                "package",
            ),
            (
                "Envíos pendientes descontados",
                f"{pend.get('unidades', 0):,}",
                f"{pend.get('skus', 0):,} SKU · {pend.get('tiendas', 0)} tiendas",
                "clock",
            ),
        ]
    )
    if len(exceso):
        st.error("Estos SKU superan el stock del CD: revisa las cantidades aprobadas.")
        st.dataframe(exceso, hide_index=True, width="stretch")
    with st.expander("Ver control por SKU"):
        st.dataframe(
            ctrl.sort_values("queda"),
            hide_index=True,
            width="stretch",
            column_config={
                "sku": "SKU",
                "stock_cd": "Stock CD disponible",
                "enviado": "Enviado",
                "tiendas": "Tiendas",
                "queda": "Queda en CD",
            },
        )

    st.dataframe(tabla.loc[envio], hide_index=True, width="stretch", height=420)

    with st.expander("Otros formatos (detalle con motivo)"):
        col, det = "cantidad", res.detalle
        if cantidad is not None:
            det, col = det.assign(cantidad_aprobada=cantidad), "cantidad_aprobada"
        c1, c2 = st.columns(2)
        c1.download_button(
            "Excel con motivos",
            lambda: a_excel(det, SETTINGS.cd_id, res.resumen, col),
            file_name=f"forusight_{res.run_id}.xlsx",
            width="stretch",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        c2.download_button(
            "CSV",
            lambda: a_csv(det, SETTINGS.cd_id, col),
            file_name=f"forusight_{res.run_id}.csv",
            mime="text/csv",
            width="stretch",
        )
