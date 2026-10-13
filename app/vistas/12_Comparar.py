"""Comparar una distribución de Forusight con el reporte de Neogística (sólo lectura)."""

import pandas as pd
import streamlit as st
from app.components.ui import hero, kpi_row

from forusight.export import comparar as C

hero(
    "Comparar con Neogística",
    "Sólo para revisar: el reporte de Neogística no entra al cálculo de Forusight. Pon lado a "
    "lado la distribución de Forusight y el reporte del mismo día para ver dónde y por qué difieren.",
    eyebrow="Revisión",
)
ss = st.session_state
c1, c2 = st.columns(2)
usar_corrida = ss.get("resultado") is not None and c1.toggle(
    "Usar la corrida actual de Inicio", value=True, key="cmp_corrida"
)
arch_fs = (
    None if usar_corrida else c1.file_uploader("Archivo Forusight", type=["xlsx"], key="cmp_fs")
)
arch_neo = c2.file_uploader("Reporte de Neogística del mismo día", type=["xlsx"], key="cmp_neo")

listo = arch_neo is not None and (usar_corrida or arch_fs is not None)
if listo and st.button("Comparar", type="primary", icon=":material/compare_arrows:", key="cmp_btn"):
    from app.components.archivo import cantidad_aprobada, tabla_archivo
    from app.components.estado import ajustes, entradas_de_la_corrida

    from forusight.data import reporte as R

    try:
        with st.spinner("Comparando…"):
            if usar_corrida:
                fs = tabla_archivo(
                    ss.resultado,
                    entradas_de_la_corrida(),
                    ss.params,
                    ajustes().cd_id,
                    cantidad_aprobada(),
                )
            else:
                fs = C.leer_archivo_forusight(arch_fs.getvalue())
            neo, _ = R.leer_reporte(arch_neo.getvalue())
            ss.comparacion_neo = C.comparar(fs, neo)
    except Exception as exc:
        st.error(f"No se pudo comparar: {exc}")

comp = ss.get("comparacion_neo")
if comp:
    r = comp["resumen"]

    def pct(x: float) -> str:
        return "—" if pd.isna(x) else f"{x:.1%}"

    kpi_row(
        [
            ("Forusight", f"{r['unidades_forusight']:,.0f}", "unidades a enviar", "truck"),
            ("Neogística", f"{r['unidades_neogistica']:,.0f}", "unidades a enviar", "truck"),
            ("Cantidad igual", pct(r["cantidad_igual_con_envio"]), "filas con envío", "check"),
            (
                "Tránsito igual",
                pct(r["transito_igual"]),
                f"{r['filas_comparadas']:,} filas",
                "package",
            ),
        ]
    )
    st.caption(
        f"Stock físico igual: {pct(r['stock_fisico_igual'])} · nivel máximo igual: "
        f"{pct(r['nivel_igual'])}. Se comparan las mismas tiendas y marcas del archivo de "
        "Forusight; las «Carga Pedidos» del reporte (cargas manuales) se dejan fuera."
    )
    st.markdown("**Por qué difieren**")
    st.dataframe(comp["causas"], hide_index=True, width="stretch")
    st.markdown("**Por tienda**")
    st.dataframe(comp["por_tienda"], hide_index=True, width="stretch")
    with st.expander(f"Detalle: {len(comp['detalle']):,} tienda × talla con distinta cantidad"):
        st.dataframe(comp["detalle"], hide_index=True, width="stretch")
    st.download_button(
        "Descargar comparación (Excel)",
        C.a_excel(comp),
        file_name="Comparacion_Forusight_Neogistica.xlsx",
        icon=":material/download:",
        key="cmp_dl",
    )
