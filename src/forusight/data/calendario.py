"""Calendario de reposición y prioridad de tiendas.

* ``config/calendario_tiendas.csv`` — por tienda: lead time y período de revisión (días) y
  los días en que se repone (LU, MA, MI, JU, VI, SA, DO). Derivado de los reportes de
  distribución (02, 03, 07, 23 y 24/09/2026): período 2,3 días = lunes, miércoles y viernes;
  3,5 días = lunes y jueves; 7 días = un día por semana.
* ``config/rutas_dia.csv`` — rutas de despacho por día (hoja de rutas de Forus): qué zona
  sale cada día (p. ej. Jockey LU/MI/VI, Plaza Norte MA/JU, provincia LU/MI/VI). Manda sobre
  los días del calendario y cubre por nombre a las tiendas que no están en él.
* ``config/prioridad_tiendas.csv`` — prioridad por venta (A, B, C) por marca.

En un día dado sólo reciben las tiendas que reponen ese día; la cobertura de cada tienda es
su lead time + su período de revisión (como el reporte), no un valor fijo.
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
    return pd.read_csv(CONFIG / "rutas_dia.csv", dtype=str)


def dias_por_ruta(nombre: str) -> str:
    """Días de despacho según la ruta cuya zona aparece en el nombre de la tienda ('' si no)."""
    texto = str(nombre or "").upper()
    for patron, dias in zip(rutas()["patron"], rutas()["dias"], strict=True):
        if re.search(rf"\b{re.escape(patron)}\b", texto):
            return dias
    return ""


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
    # Tienda sin días en el calendario: no sabemos cuándo repone → recibe cualquier día.
    dias = ids.map(cal["dias"]).fillna("").astype(str)
    nombres = out["nombre"] if "nombre" in out else pd.Series("", index=out.index)
    por_ruta = pd.Series([dias_por_ruta(n) for n in nombres], index=out.index)
    dias = dias.where(dias.ne(""), por_ruta)
    # Revisión de una tienda fuera del calendario: 7 días / despachos por semana de su ruta.
    sin_cal = ~ids.isin(cal.index) & por_ruta.ne("")
    out.loc[sin_cal, "revision_dias"] = [round(7 / len(d.split(",")), 2) for d in por_ruta[sin_cal]]
    out["recibe_hoy"] = [(not d) or (dia in d.split(",")) for d in dias] if c.aplicar else True
    pr = prioridades()
    pedidas = {m.strip().upper() for m in (marcas or [])}
    if pedidas:
        pr = pr[pr["marca"].str.upper().isin(pedidas)]
    out["prioridad"] = ids.map(
        pr.drop_duplicates("codigo_tienda").set_index("codigo_tienda")["prioridad"]
    )
    return out
