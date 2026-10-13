"""Mantenedores propios de Forus: temporada comercial por modelo-color y stock mínimo por
tienda × SKU.

Cada cambio queda en un historial (usuario y fecha) guardado en GitHub, como los bloqueos
manuales; el último cambio de cada clave es el que vale y manda sobre el maestro guardado.
"""

from __future__ import annotations

import io

import pandas as pd

RUTA_TEMPORADAS = "mantenedores/temporadas.csv"
RUTA_STOCK_MINIMO = "mantenedores/stock_minimo.csv"
COLS_TEMPORADAS = ["modelo_color_id", "temporada", "usuario", "fecha"]
COLS_STOCK_MINIMO = ["tienda_id", "sku", "stock_minimo", "usuario", "fecha"]


def vacio(cols: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=cols)


def leer(contenido: bytes | None, cols: list[str]) -> pd.DataFrame:
    if not contenido:
        return vacio(cols)
    d = pd.read_csv(io.BytesIO(contenido), dtype=str, encoding="utf-8-sig").fillna("")
    return d.reindex(columns=cols, fill_value="")


def a_csv(d: pd.DataFrame) -> bytes:
    return d.to_csv(index=False).encode("utf-8-sig")


def _norm(s: pd.Series) -> pd.Series:
    return s.astype("string").str.strip().str.upper()


def nuevos_temporadas(
    modelos_color: list[str], temporada: str, usuario: str, fecha
) -> pd.DataFrame:
    mc = [m.strip().upper() for m in modelos_color if str(m).strip()]
    return pd.DataFrame(
        {
            "modelo_color_id": mc,
            "temporada": str(temporada).strip().upper(),
            "usuario": usuario,
            "fecha": str(fecha),
        }
    )[COLS_TEMPORADAS]


def nuevos_stock_minimo(filas: pd.DataFrame, usuario: str, fecha) -> pd.DataFrame:
    """filas: tienda_id, sku, stock_minimo."""
    from forusight.data.fuentes import codigo_tienda, sku_canonico

    d = pd.DataFrame(
        {
            "tienda_id": filas["tienda_id"].astype(str).map(codigo_tienda),
            "sku": sku_canonico(filas["sku"].astype("string")),
            "stock_minimo": pd.to_numeric(filas["stock_minimo"], errors="coerce"),
        }
    ).dropna(subset=["stock_minimo"])
    d = d.loc[d["tienda_id"].ne("") & d["sku"].notna() & (d["stock_minimo"] >= 0)]
    d["stock_minimo"] = d["stock_minimo"].round().astype(int).astype(str)
    return d.assign(usuario=usuario, fecha=str(fecha))[COLS_STOCK_MINIMO]


def temporadas_vigentes(hist: pd.DataFrame | None) -> pd.Series:
    """modelo_color_id → temporada (último cambio de cada modelo-color)."""
    if hist is None or hist.empty:
        return pd.Series(dtype="string")
    d = hist.assign(mc=_norm(hist["modelo_color_id"]), t=_norm(hist["temporada"]))
    d = d.loc[d["mc"].notna() & d["mc"].ne("") & d["t"].notna() & d["t"].ne("")]
    d = d.drop_duplicates("mc", keep="last")
    return pd.Series(d["t"].to_numpy(), index=d["mc"].to_numpy())


def stock_minimo_vigente(hist: pd.DataFrame | None) -> pd.DataFrame:
    """tienda_id, sku, stock_minimo (último cambio de cada tienda × SKU)."""
    if hist is None or hist.empty:
        return pd.DataFrame(columns=["tienda_id", "sku", "stock_minimo"])
    d = hist.assign(
        tienda_id=hist["tienda_id"].astype(str).str.strip(),
        sku=hist["sku"].astype(str).str.strip(),
        stock_minimo=pd.to_numeric(hist["stock_minimo"], errors="coerce"),
    ).dropna(subset=["stock_minimo"])
    d = d.drop_duplicates(["tienda_id", "sku"], keep="last")
    return d[["tienda_id", "sku", "stock_minimo"]].reset_index(drop=True)


def aplicar_stock_minimo(maestro, hist: pd.DataFrame | None, fecha):
    """Maestro de planificación con el stock mínimo del mantenedor (manda sobre el guardado).
    Una tienda × SKU que no estaba en el maestro se agrega con ese stock mínimo."""
    from dataclasses import replace

    from forusight.data import planificacion as PLAN

    v = stock_minimo_vigente(hist)
    if v.empty:
        return maestro
    c = maestro.claves.copy() if maestro is not None else PLAN.vacio().claves
    llave = ["tienda_id", "sku"]
    c = c.merge(v.rename(columns={"stock_minimo": "_smt"}), on=llave, how="outer")
    nuevas = c["smt"].isna() & c["_smt"].notna() if "smt" in c else c["_smt"].notna()
    c["smt"] = c["_smt"].where(c["_smt"].notna(), c.get("smt"))
    c.loc[nuevas, "fecha"] = pd.Timestamp(fecha)
    c = c.drop(columns="_smt")
    if maestro is None:
        return PLAN.Maestro(c[PLAN.COLS_CLAVES], PLAN.vacio().k)
    return replace(maestro, claves=c)
