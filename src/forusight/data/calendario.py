"""Calendario de reposición y prioridad de tiendas.

* ``config/rutas_mall.csv`` — rutas de despacho de Forus: el camión sale por **mall**, así que
  todas las tiendas de un mismo centro comercial (de cualquier cadena) reciben los mismos
  días. Jockey Plaza LU/MI/VI, Larcomar y Plaza San Miguel LU/JU, Plaza Norte y Mega Plaza
  MA/JU, Salaverry MA/VI, provincia LU/MI/VI, etc.
* ``config/calendario_tiendas.csv`` — por tienda: su mall, lead time y período de revisión
  (días). Una tienda nueva sin fila toma el mall de su centro comercial (maestro de tiendas),
  de su zona (provincia) o de su nombre, y su revisión = 7 / despachos por semana.
* ``config/prioridad_tiendas.csv`` — prioridad por venta (A, B, C) por marca.

En un día dado sólo reciben las tiendas cuyo mall tiene ruta ese día; la cobertura de cada
tienda es su lead time + su período de revisión (como el reporte), no un valor fijo.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pandas as pd

CONFIG = Path(__file__).resolve().parents[1] / "config"
DIAS = ["LU", "MA", "MI", "JU", "VI", "SA", "DO"]


@lru_cache(maxsize=1)
def calendario() -> pd.DataFrame:
    return pd.read_csv(CONFIG / "calendario_tiendas.csv", dtype={"codigo_tienda": str})


@lru_cache(maxsize=1)
def prioridades() -> pd.DataFrame:
    return pd.read_csv(CONFIG / "prioridad_tiendas.csv", dtype={"codigo_tienda": str})


@lru_cache(maxsize=1)
def rutas() -> pd.DataFrame:
    return pd.read_csv(CONFIG / "rutas_mall.csv", dtype=str)


def mall_de(*textos) -> str:
    """Mall (ruta) cuyo nombre aparece en alguno de los textos, en orden ('' si ninguno)."""
    r = rutas()
    for texto in textos:
        t = str(texto or "").upper() if not pd.isna(texto) else ""
        if not t:
            continue
        for mall, patrones in zip(r["mall"], r["patrones"], strict=True):
            if any(re.search(rf"\b{re.escape(p)}\b", t) for p in patrones.split("|")):
                return mall
    return ""


def dias_de_mall(mall: str) -> str:
    """Días de despacho del mall ('LU,MI,VI'); '' si no tiene ruta."""
    d = rutas().drop_duplicates("mall").set_index("mall")["dias"]
    return str(d.get(mall, "")) if mall else ""


def dia_semana(fecha) -> str:
    return DIAS[pd.Timestamp(fecha).weekday()]


def aplicar(
    dim_tienda: pd.DataFrame, fecha, params, marcas: list[str] | None = None
) -> pd.DataFrame:
    """Agrega ``leadtime_dias``, ``revision_dias``, ``recibe_hoy`` y ``prioridad``."""
    c = params.calendario
    out = dim_tienda.copy()
    cal = calendario().set_index("codigo_tienda")
    dia = dia_semana(fecha)
    ids = out["tienda_id"].astype(str)
    out["leadtime_dias"] = (
        ids.map(cal["leadtime_dias"]).fillna(c.leadtime_dias_defecto).astype(float)
    )
    out["revision_dias"] = (
        ids.map(cal["revision_dias"]).fillna(c.revision_dias_defecto).astype(float)
    )
    # El mall manda: el del calendario; si no, centro comercial, zona o nombre de la tienda.
    col = lambda n: out[n] if n in out else pd.Series("", index=out.index)  # noqa: E731
    del_cal = ids.map(cal["mall"]).fillna("").astype(str)
    out["mall"] = [
        m or mall_de(cc, z, n)
        for m, cc, z, n in zip(
            del_cal, col("centro_comercial"), col("zona"), col("nombre"), strict=True
        )
    ]
    dias = out["mall"].map(dias_de_mall)
    out["dias_reposicion"] = dias
    # Revisión de una tienda fuera del calendario: 7 días / despachos por semana de su mall.
    sin_cal = ~ids.isin(cal.index) & dias.ne("")
    out.loc[sin_cal, "revision_dias"] = [round(7 / len(d.split(",")), 2) for d in dias[sin_cal]]
    # Tienda sin mall con ruta: no sabemos cuándo repone → recibe cualquier día.
    out["recibe_hoy"] = [(not d) or (dia in d.split(",")) for d in dias] if c.aplicar else True
    pr = prioridades()
    pedidas = {m.strip().upper() for m in (marcas or [])}
    if pedidas:
        pr = pr[pr["marca"].str.upper().isin(pedidas)]
    out["prioridad"] = ids.map(
        pr.drop_duplicates("codigo_tienda").set_index("codigo_tienda")["prioridad"]
    )
    return out
