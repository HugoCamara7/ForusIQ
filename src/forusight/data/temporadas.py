"""Temporada comercial por modelo-color.

La columna de temporada de ARTI no es la temporada comercial (coincide con el reporte de
distribución sólo en el 14 % de los SKU), así que la temporada sale de los reportes de
Neogística y del reporte 1003 de bloqueos: cada modelo-color tiene una sola temporada
comercial. ``config/temporadas.csv.gz`` guarda las de los reportes del 02 al 28/09/2026; los
reportes que se suben en la app la completan y actualizan.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd

ARCHIVO = Path(__file__).resolve().parents[1] / "config" / "temporadas.csv.gz"


@lru_cache(maxsize=1)
def por_defecto() -> pd.Series:
    if not ARCHIVO.exists():
        return pd.Series(dtype="string")
    d = pd.read_csv(ARCHIVO, dtype=str)
    return pd.Series(d["temporada"].to_numpy(), index=d["modelo_color_id"].to_numpy())


def desde_reporte(df: pd.DataFrame) -> pd.Series:
    """Reporte de distribución → modelo-color: temporada comercial."""
    if "Temporada comercial" not in df:
        return pd.Series(dtype="string")
    mc = (
        df["Código Modelo"].astype("string") + "-" + df["Código Color"].astype("string")
    ).str.upper()
    t = df["Temporada comercial"].astype("string").str.strip().str.upper()
    s = pd.Series(t.to_numpy(), index=mc.to_numpy()).dropna()
    return s[~s.index.duplicated(keep="last")]


def combinar(*fuentes: pd.Series) -> pd.Series:
    """La última fuente manda (más reciente)."""
    partes = [f.dropna() for f in fuentes if f is not None and len(f)]
    if not partes:
        return pd.Series(dtype="string")
    s = pd.concat(partes)
    return s[~s.index.duplicated(keep="last")]


def aplicar(
    dim_producto: pd.DataFrame, maestro: pd.Series, usar_arti: bool = False
) -> pd.DataFrame:
    """dim_producto con la temporada comercial del maestro (ARTI sólo como respaldo)."""
    out = dim_producto.copy()
    t = out["modelo_color_id"].astype("string").str.upper().map(maestro)
    arti = out["temporada"] if "temporada" in out else pd.Series(pd.NA, index=out.index)
    out["temporada_comercial"] = t.fillna(arti) if usar_arti else t
    out["temporada"] = out["temporada_comercial"]
    return out
