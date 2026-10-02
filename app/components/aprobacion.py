"""Aprobación de la corrida: se aprueba la propuesta tal cual (el análisis se hace en el Excel).

No se guarda: lo aprobado se carga en el sistema con el archivo y la próxima corrida lo ve como
tránsito en las tablas de pedidos."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.components.login import usuario_actual


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


def aprobar(res) -> tuple[str, str]:
    """Aprueba la propuesta de la corrida (sin guardarla). Devuelve (nivel, mensaje)."""
    ss = st.session_state
    if ss.get("aprobacion") is None or ss.aprobacion["run_id"].iat[0] != res.run_id:
        ss.aprobacion = base_aprobacion(res.detalle)
    usuario = usuario_actual()
    nivel, msg = "ok", "Aprobada. Al cargar el archivo en el sistema, cuenta como tránsito."
    ss.aprobacion_confirmada = res.run_id
    ss.aprobacion_info = {
        "usuario": usuario,
        "hora": pd.Timestamp.now(tz="America/Lima").strftime("%d/%m/%Y %H:%M"),
        "unidades": int(ss.aprobacion["cantidad_aprobada"].sum()),
        "mensaje": msg,
        "nivel": nivel,
    }
    return nivel, msg
