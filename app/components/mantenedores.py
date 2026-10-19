"""Pestañas de Parámetros: mantenedor de temporadas y de stock mínimo (propios de Forus)."""

from __future__ import annotations

import io

import pandas as pd
import streamlit as st

from app.components.estado import guardar_mantenedor, maestro_planificacion, mantenedor
from app.components.login import puede, usuario_actual
from forusight.data import mantenedores as MT
from forusight.data.corridas import hoy_lima


def _excel(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


def _leer_excel(archivo) -> pd.DataFrame:
    d = pd.read_excel(archivo, dtype=str)
    d.columns = [str(c).strip().lower() for c in d.columns]
    return d


def tab_temporadas() -> None:
    from forusight.data import temporadas as TEMP

    st.caption(
        "La temporada comercial decide qué se repone: VERANO 2026 en adelante y ESCOLAR 2026 "
        "(Formulario → surtido), la temporada mínima de cada tienda y las colecciones activas "
        "por marca × clase (página Temporadas por tienda) y, en las liquidadoras DH, SE y FB, INVIERNO 2026 hacia atrás. Aquí asignas o "
        "corriges la temporada de un modelo-color; manda sobre el maestro guardado y vale "
        "desde la próxima corrida."
    )
    hist = mantenedor(MT.RUTA_TEMPORADAS, MT.COLS_TEMPORADAS)
    manual = MT.temporadas_vigentes(hist)
    base = TEMP.por_defecto()
    vigente = TEMP.combinar(base, manual)

    buscar = st.text_input(
        "Buscar modelo o modelo-color",
        placeholder="HP10201162490 o HP10201162490-N11",
        key="mt_buscar",
    )
    if buscar.strip():
        q = buscar.strip().upper()
        res = vigente[vigente.index.astype(str).str.contains(q, regex=False)]
        tabla = pd.DataFrame(
            {
                "Modelo-color": res.index,
                "Temporada": res.to_numpy(),
                "Origen": ["Mantenedor" if m in manual.index else "Maestro" for m in res.index],
            }
        )
        if tabla.empty:
            st.info(
                "No está en el maestro: un modelo nuevo sin temporada se repone. Asígnale una abajo."
            )
        else:
            st.dataframe(tabla.head(500), hide_index=True, width="stretch")

    conocidas = sorted(
        set(vigente.dropna().astype(str)) | {"VERANO 2026", "INVIERNO 2027", "ESCOLAR 2026"}
    )
    with st.form("mt_form_temp", clear_on_submit=True):
        c1, c2 = st.columns([3, 2])
        modelos = c1.text_area(
            "Modelos-color (uno por línea)", height=110, placeholder="HP10201162490-N11"
        )
        temp = c2.selectbox("Temporada", conocidas, index=conocidas.index("VERANO 2026"))
        otra = c2.text_input("u otra temporada", placeholder="VERANO 2027")
        ok = st.form_submit_button(
            "Guardar temporada", type="primary", disabled=not puede("parametros")
        )
    if ok:
        lista = [m for m in modelos.replace(",", "\n").splitlines() if m.strip()]
        if not lista:
            st.warning("Escribe al menos un modelo-color.")
        else:
            nuevos = MT.nuevos_temporadas(lista, otra or temp, usuario_actual(), hoy_lima().date())
            donde = guardar_mantenedor(
                MT.RUTA_TEMPORADAS, MT.COLS_TEMPORADAS, nuevos, usuario_actual(), "temporadas"
            )
            st.success(f"{len(nuevos)} modelo(s)-color con {otra or temp}. Guardado en {donde}.")

    with st.expander("Cargar Excel de temporadas"):
        st.caption("Columnas: «Mod-Col» y «Temporada». Una fila por modelo-color.")
        c1, c2 = st.columns([3, 1])
        arch = c1.file_uploader("Excel", type=["xlsx"], key="mt_temp_xlsx")
        c2.download_button(
            "Plantilla",
            _excel(pd.DataFrame({"Mod-Col": ["HP10201162490-N11"], "Temporada": ["VERANO 2026"]})),
            file_name="Plantilla_Temporadas.xlsx",
            key="mt_temp_plantilla",
        )
        if arch is not None and st.button(
            "Cargar", key="mt_temp_cargar", disabled=not puede("parametros")
        ):
            d = _leer_excel(arch)
            col_mc = next((c for c in d.columns if "mod" in c), None)
            col_t = next((c for c in d.columns if "temp" in c), None)
            if not col_mc or not col_t:
                st.error("El Excel necesita las columnas «Mod-Col» y «Temporada».")
            else:
                d = d.dropna(subset=[col_mc, col_t])
                nuevos = pd.concat(
                    [
                        MT.nuevos_temporadas(
                            g[col_mc].tolist(), t, usuario_actual(), hoy_lima().date()
                        )
                        for t, g in d.groupby(col_t)
                    ],
                    ignore_index=True,
                )
                donde = guardar_mantenedor(
                    MT.RUTA_TEMPORADAS,
                    MT.COLS_TEMPORADAS,
                    nuevos,
                    usuario_actual(),
                    "temporadas (Excel)",
                )
                st.success(f"{len(nuevos)} modelos-color cargados. Guardado en {donde}.")

    with st.expander(f"Historial de cambios ({len(hist):,})"):
        st.dataframe(hist.iloc[::-1], hide_index=True, width="stretch")
        st.download_button(
            "Descargar historial",
            _excel(hist),
            file_name="Historial_Temporadas.xlsx",
            key="mt_temp_hist",
        )


def tab_stock_minimo() -> None:
    st.caption(
        "Stock mínimo por tienda × talla: el piso del nivel máximo. Casi no cambia: se usa el "
        "último valor guardado y aquí lo ajustas. Lo que cambies manda sobre el maestro y vale "
        "desde la próxima corrida."
    )
    hist = mantenedor(MT.RUTA_STOCK_MINIMO, MT.COLS_STOCK_MINIMO)
    manual = MT.stock_minimo_vigente(hist)
    c1, c2 = st.columns(2)
    tienda = c1.text_input("Código de tienda", placeholder="8", key="mt_smt_tienda")
    skus = c2.text_input(
        "SKU (uno o varios, separados por coma)", placeholder="5389545, 4417904", key="mt_smt_sku"
    )
    if tienda.strip() or skus.strip():
        claves = maestro_planificacion().claves[["tienda_id", "sku", "smt"]].copy()
        claves[["tienda_id", "sku"]] = claves[["tienda_id", "sku"]].astype(str)
        actual = claves.merge(
            manual.rename(columns={"stock_minimo": "manual"}), on=["tienda_id", "sku"], how="outer"
        )
        actual["stock_minimo"] = actual["manual"].fillna(actual["smt"]).fillna(1)
        actual["origen"] = actual["manual"].notna().map({True: "Mantenedor", False: "Maestro"})
        sel = pd.Series(True, index=actual.index)
        if tienda.strip():
            sel &= actual["tienda_id"].eq(tienda.strip().lstrip("0"))
        lista = [s.strip().lstrip("0") for s in skus.split(",") if s.strip()]
        if lista:
            sel &= actual["sku"].isin(lista)
        vista = (
            actual.loc[sel, ["tienda_id", "sku", "stock_minimo", "origen"]]
            .head(2000)
            .reset_index(drop=True)
        )
        if lista and tienda.strip():  # tienda × SKU sin clave: se puede crear
            faltan = [s for s in lista if s not in set(vista["sku"])]
            if faltan:
                extra = pd.DataFrame(
                    {
                        "tienda_id": tienda.strip().lstrip("0"),
                        "sku": faltan,
                        "stock_minimo": 1,
                        "origen": "Nuevo",
                    }
                )
                vista = pd.concat([vista, extra], ignore_index=True)
        st.caption(
            f"{len(vista):,} tienda × talla (máx. 2.000). Cambia la columna Stock mínimo y guarda."
        )
        editado = st.data_editor(
            vista,
            hide_index=True,
            width="stretch",
            disabled=["tienda_id", "sku", "origen"],
            column_config={
                "tienda_id": "Tienda",
                "sku": "SKU",
                "stock_minimo": st.column_config.NumberColumn("Stock mínimo", min_value=0, step=1),
                "origen": "Origen",
            },
            key=f"mt_smt_editor_{tienda}_{skus}",
        )
        cambios = editado.loc[
            ~pd.to_numeric(editado["stock_minimo"]).eq(pd.to_numeric(vista["stock_minimo"]))
            | vista["origen"].eq("Nuevo")
        ]
        if st.button(
            f"Guardar {len(cambios)} cambio(s)",
            type="primary",
            disabled=cambios.empty or not puede("parametros"),
            key="mt_smt_guardar",
        ):
            nuevos = MT.nuevos_stock_minimo(cambios, usuario_actual(), hoy_lima().date())
            donde = guardar_mantenedor(
                MT.RUTA_STOCK_MINIMO, MT.COLS_STOCK_MINIMO, nuevos, usuario_actual(), "stock mínimo"
            )
            st.success(f"{len(nuevos)} tienda × talla actualizadas. Guardado en {donde}.")

    with st.expander("Cargar Excel de stock mínimo"):
        st.caption("Columnas: «Tienda», «SKU» y «Stock mínimo».")
        c1, c2 = st.columns([3, 1])
        arch = c1.file_uploader("Excel", type=["xlsx"], key="mt_smt_xlsx")
        c2.download_button(
            "Plantilla",
            _excel(pd.DataFrame({"Tienda": ["8"], "SKU": ["5389545"], "Stock mínimo": [2]})),
            file_name="Plantilla_Stock_Minimo.xlsx",
            key="mt_smt_plantilla",
        )
        if arch is not None and st.button(
            "Cargar", key="mt_smt_cargar", disabled=not puede("parametros")
        ):
            d = _leer_excel(arch)
            col_t = next((c for c in d.columns if "tienda" in c), None)
            col_s = next((c for c in d.columns if "sku" in c), None)
            col_m = next((c for c in d.columns if "m" in c and "nimo" in c), None)
            if not (col_t and col_s and col_m):
                st.error("El Excel necesita las columnas «Tienda», «SKU» y «Stock mínimo».")
            else:
                filas = d.rename(columns={col_t: "tienda_id", col_s: "sku", col_m: "stock_minimo"})
                nuevos = MT.nuevos_stock_minimo(filas, usuario_actual(), hoy_lima().date())
                donde = guardar_mantenedor(
                    MT.RUTA_STOCK_MINIMO,
                    MT.COLS_STOCK_MINIMO,
                    nuevos,
                    usuario_actual(),
                    "stock mínimo (Excel)",
                )
                st.success(f"{len(nuevos)} tienda × talla cargadas. Guardado en {donde}.")

    with st.expander(f"Historial de cambios ({len(hist):,})"):
        st.dataframe(hist.iloc[::-1], hide_index=True, width="stretch")
        st.download_button(
            "Descargar historial",
            _excel(hist),
            file_name="Historial_Stock_Minimo.xlsx",
            key="mt_smt_hist",
        )
