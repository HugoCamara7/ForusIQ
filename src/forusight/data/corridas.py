"""Corridas diarias guardadas: dónde quedan y cómo se llaman.

La corrida programada (``scripts/corrida_diaria.py``, todos los días a las 6:30 de Lima) deja
su Excel en el repositorio privado de datos, bajo ``<prefix>/corridas/AAAA-MM/``. El panel de
la app lee de la misma carpeta. La regla vive aquí, en un solo sitio: si el script guardara
con un nombre y el panel buscara otro, los archivos estarían y nadie los vería.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pandas as pd

CARPETA = "corridas"
#: Perú es UTC-5 fijo (sin horario de verano desde 1994). El runner corre en UTC: sin zona,
#: el «hoy» de las 6:30 de Lima sería el de las 11:30 UTC, que coincide, pero a las 19:00 de
#: Lima ya sería el día siguiente.
LIMA = timezone(timedelta(hours=-5), "America/Lima")
SUFIJO_PRELIMINAR = "_PRELIMINAR"
_PATRON = re.compile(r"^(\d{4}-\d{2}-\d{2})_Distribucion_Forusight(_PRELIMINAR)?\.xlsx$")


def ahora_lima() -> datetime:
    return datetime.now(LIMA)


def hoy_lima() -> pd.Timestamp:
    """Fecha de hoy en Lima, sin hora ni zona. Streamlit Cloud y el runner corren en UTC: con
    ``pd.Timestamp.today()``, desde las 19:00 de Lima la app ya vivía en «mañana» (pedía el
    cierre de hoy, que la carga diaria trae a las 3:00, y armaba la ruta del día siguiente)."""
    return pd.Timestamp(ahora_lima().date())


def carpeta_del_mes(fecha) -> str:
    return f"{CARPETA}/{pd.Timestamp(fecha):%Y-%m}"


def nombre_diario(fecha, preliminar: bool = False) -> str:
    """``2026-10-05_Distribucion_Forusight.xlsx``: la fecha va en ISO para que el listado
    alfabético sea también el cronológico."""
    sufijo = SUFIJO_PRELIMINAR if preliminar else ""
    return f"{pd.Timestamp(fecha):%Y-%m-%d}_Distribucion_Forusight{sufijo}.xlsx"


def ruta_diaria(fecha, preliminar: bool = False) -> str:
    return f"{carpeta_del_mes(fecha)}/{nombre_diario(fecha, preliminar)}"


def leer_nombre(nombre: str) -> tuple[pd.Timestamp, bool] | None:
    """(fecha, preliminar) de un archivo de corrida; None si el nombre no es de una corrida."""
    m = _PATRON.match(str(nombre or ""))
    if not m:
        return None
    return pd.Timestamp(m.group(1)), bool(m.group(2))


def ya_guardada(archivos: list[dict], fecha) -> bool:
    """¿Ya hay corrida DEFINITIVA de esa fecha? Una preliminar no cuenta: si más tarde la carga
    diaria llega, el intento siguiente debe guardar la buena."""
    dia = pd.Timestamp(fecha).normalize()
    for a in archivos:
        leido = leer_nombre(a.get("name", ""))
        if leido and leido[0] == dia and not leido[1]:
            return True
    return False


def corridas_listadas(archivos: list[dict]) -> list[dict]:
    """Archivos de corrida, del más reciente al más antiguo, con su fecha y si es preliminar."""
    out = []
    for a in archivos:
        leido = leer_nombre(a.get("name", ""))
        if leido:
            out.append({**a, "fecha": leido[0], "preliminar": leido[1]})
    return sorted(out, key=lambda a: (a["fecha"], not a["preliminar"]), reverse=True)
