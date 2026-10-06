"""Maestro de planificación tomado de los reportes de distribución (Neogística).

El nivel máximo del reporte sale de parámetros del planificador que no están en BigQuery:

* **SMT** (Stock Mínimo Total) por tienda × SKU: el piso del nivel (manda en el 83 % de las filas;
  97,9 % igual de un reporte a otro con 2 días de diferencia).
* **k** por categoría (marca | clase | prenda | género): pronóstico semanal del modelo-color en
  la tienda = k × venta de sus 4 últimas semanas cerradas (exacto en el 96-99 % de los casos).
* **Unidad de empaque** (UE) por SKU (6/12/24 en accesorios).

Cada reporte que se carga actualiza el maestro (gana la fecha más reciente por clave). El maestro
por defecto va en ``config/planificacion_claves.csv.gz`` y ``config/planificacion_k.csv``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

CONFIG = Path(__file__).resolve().parents[1] / "config"
RUTA_CLAVES = CONFIG / "planificacion_claves.csv.gz"
RUTA_K = CONFIG / "planificacion_k.csv"
#: Tienda que Neogística no planifica → tienda parecida de la que toma SMT, UE y categoría.
RUTA_ESPEJOS = CONFIG / "tiendas_espejo.csv"

COLS_CLAVES = [
    "tienda_id",
    "sku",
    "smt",
    "nivel",
    "rop",
    "ue",
    "almacenamiento",
    "categoria_k",
    "fecha",
]
COLS_K = ["categoria_k", "k", "fecha"]
#: k base por clase (fines de septiembre 2026), si la categoría no está en el maestro.
K_BASE = {"CALZADO": 0.325, "ACCESORIOS": 0.325, "VESTUARIO": 0.35}
K_DEFECTO = 0.325


@dataclass
class Maestro:
    claves: pd.DataFrame  # COLS_CLAVES
    k: pd.DataFrame  # COLS_K
    dia: pd.Timestamp | None = None  # día de la corrida (vigente)

    @property
    def vacio(self) -> bool:
        return self.claves.empty and self.k.empty


def vacio() -> Maestro:
    return Maestro(pd.DataFrame(columns=COLS_CLAVES), pd.DataFrame(columns=COLS_K))


def _txt(s: pd.Series) -> pd.Series:
    return s.astype("string").str.strip().str.upper()


def clave_categoria(marca, clase, prenda, genero) -> pd.Series:
    partes = [_txt(pd.Series(x)).fillna("") for x in (marca, clase, prenda, genero)]
    return partes[0] + "|" + partes[1] + "|" + partes[2] + "|" + partes[3]


def _moda(s: pd.Series):
    return s.value_counts().index[0]


def desde_reporte(df: pd.DataFrame, fecha) -> Maestro:
    """Maestro de un reporte de distribución (filas de ``reporte.leer_reporte``)."""
    from forusight.data import reporte as R
    from forusight.data.fuentes import sku_canonico

    fecha = pd.Timestamp(fecha).normalize()
    tienda = df[R.CENTRO].astype("string").str.strip().str.lstrip("0")
    sku = sku_canonico(df[R.SKU].astype("string")).astype("string")
    ue = R._num(df[R.UE]) if R.UE in df else pd.Series(1.0, index=df.index)
    claves = pd.DataFrame(
        {
            "tienda_id": tienda,
            "sku": sku,
            "smt": R._num(df["Stock Mínimo Total"]) if "Stock Mínimo Total" in df else np.nan,
            "nivel": R._num(df[R.MAX]),
            "rop": R._num(df[R.ROP]),
            "ue": ue.where(ue >= 1, 1.0),
            # la tienda no tiene espacio para ese grupo: el reporte no envía (motivo Almacenamiento)
            "almacenamiento": (
                df[R.MOT].astype("string").str.strip().eq("Almacenamiento").fillna(False)
                if R.MOT in df
                else False
            ),
            # categoría del reporte (la prenda de ARTI no siempre se llama igual: ZAPATILLA/S…)
            "categoria_k": clave_categoria(
                *[
                    df.get(c, pd.Series(pd.NA, index=df.index))
                    for c in ("Marca", "Clase", "Prenda", "Género")
                ]
            ).to_numpy(),
            "fecha": fecha,
        }
    ).drop_duplicates(["tienda_id", "sku"], keep="last")

    # k: pronóstico del modelo-color en la tienda / venta de sus 4 últimas semanas cerradas.
    wk = R.semanas(df)
    k = pd.DataFrame(columns=COLS_K)
    if len(wk) >= 4 and R.PRONOSTICO in df:
        mc = (
            tienda
            + "|"
            + df["Código Modelo"].astype("string")
            + "|"
            + df["Código Color"].astype("string")
        )
        g = pd.DataFrame(
            {
                "mc": mc,
                "f": R._num(df[R.PRONOSTICO]),
                "s4": sum(R._num(df[w]).clip(lower=0) for w in wk[-4:]),
                "cat": clave_categoria(df["Marca"], df["Clase"], df["Prenda"], df["Género"]),
            }
        )
        a = g.groupby("mc").agg(f=("f", "sum"), s4=("s4", "sum"), cat=("cat", "first"))
        a = a.loc[(a["s4"] > 0) & (a["f"] > 0)]
        a["k"] = (a["f"] / a["s4"]).round(5)
        if len(a):
            k = a.groupby("cat")["k"].agg(_moda).rename_axis("categoria_k").reset_index()
            k["fecha"] = fecha
    return Maestro(claves.reset_index(drop=True), k[COLS_K])


def combinar(*maestros: Maestro | None) -> Maestro:
    """Une maestros: por clave gana el de fecha más reciente."""
    ms = [m for m in maestros if m is not None and not m.vacio]
    if not ms:
        return vacio()
    c = pd.concat([m.claves for m in ms if len(m.claves)], ignore_index=True)
    k = (
        pd.concat([m.k for m in ms if len(m.k)], ignore_index=True)
        if any(len(m.k) for m in ms)
        else pd.DataFrame(columns=COLS_K)
    )
    c["fecha"] = pd.to_datetime(c["fecha"])
    c = c.sort_values("fecha", kind="mergesort").drop_duplicates(["tienda_id", "sku"], keep="last")
    if len(k):
        k["fecha"] = pd.to_datetime(k["fecha"])
        k = k.sort_values("fecha", kind="mergesort").drop_duplicates("categoria_k", keep="last")
    return Maestro(c.reset_index(drop=True), k.reset_index(drop=True))


def por_defecto() -> Maestro:
    """Maestro guardado en config (se regenera con ``guardar``)."""
    if not RUTA_CLAVES.exists():
        return vacio()
    c = pd.read_csv(
        RUTA_CLAVES, dtype={"tienda_id": "string", "sku": "string"}, parse_dates=["fecha"]
    )
    k = (
        pd.read_csv(RUTA_K, dtype={"categoria_k": "string"}, parse_dates=["fecha"])
        if RUTA_K.exists()
        else pd.DataFrame(columns=COLS_K)
    )
    for col, v in (("almacenamiento", False), ("categoria_k", pd.NA)):
        if col not in c:
            c[col] = v
    return Maestro(c[COLS_CLAVES], k[COLS_K])


def guardar(m: Maestro, claves: Path = RUTA_CLAVES, ruta_k: Path = RUTA_K) -> None:
    c = m.claves.copy()
    c["fecha"] = pd.to_datetime(c["fecha"]).dt.date
    c.to_csv(claves, index=False, compression="gzip")
    k = m.k.copy()
    k["fecha"] = pd.to_datetime(k["fecha"]).dt.date
    k.to_csv(ruta_k, index=False)


def a_bytes(m: Maestro) -> bytes:
    """Un solo CSV gzip con claves y k (columna ``tipo``) para guardarlo en GitHub."""
    import gzip

    c = m.claves.assign(tipo="clave")
    k = m.k.assign(tipo="k")
    t = pd.concat([c, k], ignore_index=True)
    t["fecha"] = pd.to_datetime(t["fecha"]).dt.date
    return gzip.compress(t.to_csv(index=False).encode())


def desde_bytes(contenido: bytes) -> Maestro:
    import gzip
    import io

    t = pd.read_csv(
        io.BytesIO(gzip.decompress(contenido)),
        dtype={"tienda_id": "string", "sku": "string", "categoria_k": "string"},
        parse_dates=["fecha"],
    )
    for col, v in (("almacenamiento", False), ("categoria_k", pd.NA)):
        if col not in t:
            t[col] = v
    c = t.loc[t["tipo"].eq("clave"), COLS_CLAVES].reset_index(drop=True)
    k = t.loc[t["tipo"].eq("k"), COLS_K].reset_index(drop=True)
    return Maestro(c, k)


def vigente(m: Maestro, dia, dias_maximos: int) -> Maestro:
    """Sólo claves y k con antigüedad <= dias_maximos respecto del día de la corrida."""
    dia = pd.Timestamp(dia).normalize()
    c = m.claves.loc[(dia - pd.to_datetime(m.claves["fecha"])).dt.days.between(0, dias_maximos)]
    return Maestro(c, m.k, dia)


def espejos() -> pd.DataFrame:
    """tienda_id → espejo_id (``config/tiendas_espejo.csv``)."""
    if not RUTA_ESPEJOS.exists():
        return pd.DataFrame(columns=["tienda_id", "nombre_tienda", "espejo_id", "nombre_espejo"])
    return pd.read_csv(RUTA_ESPEJOS, dtype=str).dropna(subset=["tienda_id", "espejo_id"])


def con_espejos(m: Maestro, tabla: pd.DataFrame | None = None) -> Maestro:
    """Una tienda sin claves toma las de su espejo: SMT, UE y categoría del planificador, sin
    nivel ni reorden (los calcula la fórmula con la venta de la propia tienda). Sin espejo, el
    SMT sería la moda del SKU en todas las tiendas, casi todas A: alto para una tienda chica.
    Columna ``espejo``: de qué tienda salió la clave."""
    tabla = espejos() if tabla is None else tabla
    c = m.claves
    if c.empty or tabla.empty:
        return m
    ids = set(c["tienda_id"].astype(str))
    copias = []
    for t, e in zip(tabla["tienda_id"].astype(str), tabla["espejo_id"].astype(str), strict=True):
        if t in ids or e not in ids:  # con claves propias manda lo suyo
            continue
        x = c.loc[c["tienda_id"].astype(str).eq(e)].copy()
        x["tienda_id"] = t
        x[["nivel", "rop"]] = np.nan
        x["almacenamiento"] = pd.NA
        x["espejo"] = e
        copias.append(x)
    if not copias:
        return m
    return Maestro(pd.concat([c, *copias], ignore_index=True), m.k, m.dia)
