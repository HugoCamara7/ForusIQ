"""Constantes y utilidades compartidas del motor."""

from __future__ import annotations

import numpy as np
import pandas as pd

from forusight.config.settings import EngineParams

# Estados de disponibilidad
NUNCA_TUVO = "NUNCA_TUVO"
EXPOSICION_INSUFICIENTE = "EXPOSICION_INSUFICIENTE"
TUVO_SIN_VENTA = "TUVO_SIN_VENTA"
TUVO_Y_VENDE = "TUVO_Y_VENDE"
QUIEBRE = "QUIEBRE"

ESTADOS_CON_EVIDENCIA = (TUVO_Y_VENDE, QUIEBRE, TUVO_SIN_VENTA)  # la venta observada es informativa
ESTADOS_CON_HISTORIA_VENTA = (TUVO_Y_VENDE, QUIEBRE)
ESTADOS_SIN_EVIDENCIA = (NUNCA_TUVO, EXPOSICION_INSUFICIENTE)

KEY_MC = ["tienda_id", "modelo_color_id"]
KEY_SKU = ["tienda_id", "sku"]


def resolver_fecha_corte(
    ventas: pd.DataFrame, fecha_corte: pd.Timestamp | str | None
) -> pd.Timestamp:
    """Inicio de la semana en curso (no cerrada). Por defecto: última semana + 7 días."""
    if fecha_corte is not None:
        return pd.Timestamp(fecha_corte).normalize()
    if ventas.empty:
        raise ValueError("Sin ventas no se puede inferir fecha_corte; pásela explícitamente")
    return pd.Timestamp(ventas["semana_inicio"].max()).normalize() + pd.Timedelta(days=7)


def semana_relativa(semana_inicio: pd.Series, fecha_corte: pd.Timestamp) -> pd.Series:
    """1 = última semana cerrada, 2 = la anterior, ... (<= 0: semana en curso o futura)."""
    return ((fecha_corte - pd.to_datetime(semana_inicio)).dt.days // 7).astype("int64")


def bloque_semana(rel: pd.Series, params: EngineParams) -> pd.Series:
    """Bloque 1 = 1..b (4S), 2 = b+1..2b (5-8S), 3 = resto hasta semanas_analisis (9-12S)."""
    b = params.horizonte.semanas_bloque_reciente
    return pd.Series(np.select([rel <= b, rel <= 2 * b], [1, 2], 3), index=rel.index)


def factor_exposicion(dias: pd.Series, params: EngineParams) -> pd.Series:
    """Factor de corrección por exposición parcial: 1 / max(expo, piso), con tope."""
    d = params.demanda
    expo = dias.astype(float) / 7.0
    return np.minimum(1.0 / np.maximum(expo, d.exposicion_minima), d.tope_correccion)


def safe_div(num, den, default: float = 0.0):
    num = np.asarray(num, dtype=float)
    den = np.asarray(den, dtype=float)
    out = np.full(np.broadcast(num, den).shape, default, dtype=float)
    np.divide(num, den, out=out, where=den != 0)
    return out


def ratio_a_score(r) -> np.ndarray:
    """Mapea un índice relativo (1 = promedio) a [0, 1): r / (1 + r)."""
    r = np.clip(np.asarray(r, dtype=float), 0, None)
    return r / (1.0 + r)


def clave_talla(talla: pd.Series) -> pd.Series:
    """Clave comparable de talla: "390" (formato Forus, 39.0) == "39" == "39.0"; "075" == 7.5.

    Los códigos de 3 dígitos se leen con un decimal implícito (390 → 39.0, 105 → 10.5).
    Las tallas no numéricas (S, M, L) se comparan en mayúsculas.
    """
    t = talla.astype("string").str.strip().str.upper()
    tres = t.str.fullmatch(r"\d{3}").fillna(False).astype(bool)
    num = pd.to_numeric(t.str.replace(",", ".", regex=False), errors="coerce").astype("Float64")
    num = num.where(~tres, num / 10)
    return num.map(lambda x: f"{x:g}", na_action="ignore").astype("string").fillna(t)
