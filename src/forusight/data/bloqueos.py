"""Reporte 1003 - Modelos Bloqueados (Neogística): modelo-color bloqueado por tienda.

Cada fila es un SKU de un modelo-color bloqueado en un centro («Colección No Activa en
Tienda», bloqueos pedidos por el negocio, concentración…). Un modelo-color bloqueado en una
tienda no se repone ahí, aunque la tienda lo haya vendido.
"""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

import pandas as pd

#: Bloqueos por defecto (reporte 1003 del 28/09/2026, las 4 partes). Se usan cuando no se
#: sube un reporte más reciente en la barra lateral.
POR_DEFECTO = Path(__file__).resolve().parents[1] / "config" / "bloqueos.csv.gz"
FECHA_POR_DEFECTO = "28/09/2026"


@lru_cache(maxsize=1)
def por_defecto() -> pd.DataFrame:
    if not POR_DEFECTO.exists():
        return pd.DataFrame(columns=["tienda_id", "modelo_color_id", "motivo"])
    d = pd.read_csv(POR_DEFECTO, dtype=str)
    return d.assign(motivo="reporte 1003 del " + FECHA_POR_DEFECTO)


COLUMNAS = {
    "Código centro": "tienda_id",
    "Código modelo color (s)": "modelo_color_id",
    "Motivo bloqueo": "motivo",
    "Temporada comercial": "temporada",
    "Código sku": "sku",
}


#: Orden de columnas del reporte 1003 (para las partes que vienen sin cabecera).
POSICIONES = {"modelo_color_id": 0, "sku": 1, "tienda_id": 2, "temporada": 13, "motivo": 14}


def _es_modelo_color(x) -> bool:
    t = str(x or "")
    return "-" in t and " " not in t.strip() and len(t) >= 5


def leer(contenido: bytes) -> pd.DataFrame:
    """Un archivo del reporte de bloqueos → tienda_id, modelo_color_id, motivo (sin repetidos)."""
    from python_calamine import CalamineWorkbook

    wb = CalamineWorkbook.from_filelike(io.BytesIO(contenido))
    for hoja in wb.sheet_names:
        filas = wb.get_sheet_by_name(hoja).to_python()
        enc = next(
            (i for i, f in enumerate(filas[:30]) if "Código modelo color (s)" in map(str, f)), None
        )
        if enc is not None:
            cab = [str(c) for c in filas[enc]]
            idx = {COLUMNAS[c]: cab.index(c) for c in COLUMNAS if c in cab}
            datos = filas[enc + 1 :]
        else:  # partes 2, 3… del reporte: sin cabecera, mismas columnas en el mismo orden
            datos = [f for f in filas if len(f) > 14 and _es_modelo_color(f[0]) and f[2] != ""]
            if not datos:
                continue
            idx = dict(POSICIONES)
        d = pd.DataFrame({k: [f[i] if i < len(f) else None for f in datos] for k, i in idx.items()})
        return normalizar(d)
    raise ValueError("El archivo no tiene la columna «Código modelo color (s)» del reporte 1003.")


def normalizar(d: pd.DataFrame) -> pd.DataFrame:
    from forusight.data.fuentes import codigo_tienda

    out = pd.DataFrame(
        {
            "tienda_id": d["tienda_id"].map(codigo_tienda),
            "modelo_color_id": d["modelo_color_id"].astype("string").str.strip().str.upper(),
            "motivo": d.get("motivo", pd.Series("", index=d.index)).astype("string"),
        }
    ).dropna(subset=["modelo_color_id"])
    out = out.loc[out["tienda_id"].ne("")]
    return out.drop_duplicates(["tienda_id", "modelo_color_id"]).reset_index(drop=True)


def unir(partes: list[pd.DataFrame]) -> pd.DataFrame:
    if not partes:
        return pd.DataFrame(columns=["tienda_id", "modelo_color_id", "motivo"])
    return (
        pd.concat(partes, ignore_index=True)
        .drop_duplicates(["tienda_id", "modelo_color_id"])
        .reset_index(drop=True)
    )


def claves(bloq: pd.DataFrame) -> pd.Series:
    return bloq["tienda_id"].astype(str) + "|" + bloq["modelo_color_id"].astype(str)


def fuera_de_surtido(
    tienda: pd.Series, modelo_color: pd.Series, temporada: pd.Series | None, bloq, temporadas
) -> tuple[pd.Series, pd.Series]:
    """(bloqueado, fuera_de_temporada) por fila. ``temporadas`` vacío = todas."""
    clave = tienda.astype(str) + "|" + modelo_color.astype("string").str.upper()
    bloqueado = clave.isin(set(claves(bloq))) if bloq is not None and len(bloq) else clave.eq("\0")
    activas = {str(t).strip().upper() for t in temporadas or [] if str(t).strip()}
    if activas and temporada is not None:
        t = temporada.astype("string").str.strip().str.upper()
        fuera = t.notna() & ~t.isin(activas)
    else:
        fuera = pd.Series(False, index=tienda.index)
    return bloqueado.fillna(False).astype(bool), fuera.fillna(False).astype(bool)
