"""Colecciones activas por tienda × clase × marca (reporte SAP 1037 «Colecciones Activas Marca»).

Cada fila dice qué colección (temporada comercial) sigue activa para una clase y marca, en una
tienda (Código centro) o en todas (``TODO``). Las tiendas de línea que mantienen temporadas
anteriores reponen, además de lo que dicen el parámetro general o su temporada mínima, las
colecciones activas de su marca × clase: las de ``TODO`` y las propias de la tienda (se suman).
Las liquidadoras no: siguen con la reposición por jerarquía.

``config/colecciones_activas.csv`` guarda el reporte del 15/10/2026; un reporte nuevo subido en
la página Temporadas por tienda lo reemplaza completo.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pandas as pd

ARCHIVO = Path(__file__).resolve().parents[1] / "config" / "colecciones_activas.csv"
TODAS = "TODO"
COLS = ["tienda_id", "clase", "marca", "coleccion"]
_ALIAS = {
    "codigocentro": "tienda_id",
    "codcentro": "tienda_id",
    "centro": "tienda_id",
    "codtienda": "tienda_id",
    "codigotienda": "tienda_id",
    "clase": "clase",
    "marca": "marca",
    "coleccionactiva": "coleccion",
    "coleccion": "coleccion",
    "temporada": "coleccion",
    "temporadacomercial": "coleccion",
}


def _texto(x) -> str:
    return " ".join(str(x if x is not None else "").strip().upper().split())


def _tienda(x) -> str:
    from forusight.data.fuentes import codigo_tienda

    t = _texto(x)
    return TODAS if t in {"TODO", "TODAS", "*"} else codigo_tienda(t)


def clave(tienda, clase, marca) -> str:
    return f"{_tienda(tienda)}|{_texto(clase)}|{_texto(marca)}"


def a_mapa(d: pd.DataFrame | None) -> dict[str, list[str]]:
    """Tabla (tienda_id, clase, marca, coleccion) → {"tienda|CLASE|MARCA": [colecciones]}."""
    if d is None or d.empty:
        return {}
    out: dict[str, set[str]] = {}
    for t, c, m, col in d[COLS].itertuples(index=False):
        if all(_texto(x) for x in (t, c, m, col)):
            out.setdefault(clave(t, c, m), set()).add(_texto(col))
    return {k: sorted(v) for k, v in sorted(out.items())}


def a_tabla(mapa: dict[str, list[str]] | None) -> pd.DataFrame:
    filas = [
        (*k.split("|", 2), col) for k, cols in (mapa or {}).items() for col in cols if "|" in k
    ]
    return pd.DataFrame(filas, columns=COLS)


def por_defecto() -> dict[str, list[str]]:
    """Mapa del reporte guardado en config (sin pandas: lo usa el default de los parámetros)."""
    if not ARCHIVO.exists():
        return {}
    out: dict[str, set[str]] = {}
    with ARCHIVO.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if all(_texto(r.get(c)) for c in COLS):
                k = f"{_texto(r['tienda_id'])}|{_texto(r['clase'])}|{_texto(r['marca'])}"
                out.setdefault(k, set()).add(_texto(r["coleccion"]))
    return {k: sorted(v) for k, v in sorted(out.items())}


def _norm_col(c) -> str:
    import unicodedata

    t = unicodedata.normalize("NFKD", str(c)).encode("ascii", "ignore").decode().lower()
    return "".join(ch for ch in t if ch.isalnum())


def leer_reporte(contenido: bytes, nombre: str = "") -> tuple[pd.DataFrame, str]:
    """Reporte 1037 (Excel de SAP con títulos arriba, o CSV) → (tabla, fecha del reporte).

    La cabecera se busca en las primeras filas (Código centro, Clase, Marca, Coleccion activa);
    la fecha sale de la fila «Fecha:» del encabezado (vacía si no la trae)."""
    if str(nombre).lower().endswith(".csv"):
        crudo = pd.read_csv(
            io.BytesIO(contenido), dtype=str, header=None, sep=None, engine="python"
        )
    else:
        crudo = pd.read_excel(io.BytesIO(contenido), dtype=str, header=None)
    fecha, fila = "", None
    for i, r in crudo.head(30).iterrows():
        vals = [str(v) for v in r.tolist() if not pd.isna(v)]
        if vals and _norm_col(vals[0]) == "fecha" and len(vals) > 1:
            fecha = vals[1].strip()
        if {_ALIAS.get(_norm_col(v)) for v in vals} >= set(COLS):
            fila = i
            break
    if fila is None:
        raise ValueError(
            "No encontré la cabecera del reporte: debe tener las columnas «Código centro», "
            "«Clase», «Marca» y «Coleccion activa»."
        )
    cab = [_ALIAS.get(_norm_col(v)) if not pd.isna(v) else None for v in crudo.loc[fila]]
    d = crudo.loc[fila + 1 :].copy()
    d.columns = [c or f"_{j}" for j, c in enumerate(cab)]
    d = d.loc[:, ~d.columns.duplicated()][COLS].fillna("")
    for c in COLS:
        d[c] = d[c].map(_texto)
    d["tienda_id"] = d["tienda_id"].map(lambda x: _tienda(x) if x else "")
    d = d.loc[d.ne("").all(axis=1)].drop_duplicates()
    return d.sort_values(COLS).reset_index(drop=True), fecha


def activa(
    tienda: pd.Series,
    clase: pd.Series | None,
    marca: pd.Series | None,
    temporada: pd.Series,
    mapa: dict[str, list[str]] | None,
) -> pd.Series:
    """Por fila: la temporada es una colección activa de su marca × clase en esa tienda (filas
    propias de la tienda o de ``TODO``)."""
    if not mapa or clase is None or marca is None:
        return pd.Series(False, index=tienda.index)
    pares = {(k, col) for k, cols in mapa.items() for col in cols}
    c = clase.astype("string").fillna("").map(_texto)
    m = marca.astype("string").fillna("").map(_texto)
    t = temporada.astype("string").fillna("").map(_texto)
    resto = "|" + c + "|" + m
    propia = tienda.astype(str).str.strip() + resto
    todas = TODAS + resto
    ok = [
        (p, x) in pares or (a, x) in pares
        for p, a, x in zip(propia.tolist(), todas.tolist(), t.tolist(), strict=True)
    ]
    return pd.Series(ok, index=tienda.index) & t.ne("")


# --------------------------------------------------------------------------- historial (app)
#: Cada carga del reporte guarda todas sus filas con la misma ``carga``; vale la última.
RUTA = "mantenedores/colecciones_activas.csv"
COLS_HIST = [*COLS, "fecha_reporte", "carga", "usuario", "fecha"]


def nuevos(tabla: pd.DataFrame, fecha_reporte: str, usuario: str, fecha) -> pd.DataFrame:
    """Filas a agregar al historial por una carga del reporte."""
    carga = pd.Timestamp.now(tz="America/Lima").strftime("%Y%m%d%H%M%S")
    return tabla[COLS].assign(
        fecha_reporte=fecha_reporte, carga=carga, usuario=usuario, fecha=str(fecha)
    )[COLS_HIST]


def vigente(hist: pd.DataFrame | None) -> pd.DataFrame | None:
    """Filas de la última carga del reporte; None si nunca se subió (vale config)."""
    if hist is None or hist.empty:
        return None
    ultima = hist["carga"].astype(str).iloc[-1]
    return hist.loc[hist["carga"].astype(str).eq(ultima)].reset_index(drop=True)


def resumen(mapa: dict[str, list[str]] | None) -> pd.DataFrame:
    """Una fila por tienda × clase × marca con sus colecciones activas (para mostrar)."""
    filas = [(*k.split("|", 2), ", ".join(v)) for k, v in (mapa or {}).items() if "|" in k]
    return pd.DataFrame(filas, columns=["tienda_id", "clase", "marca", "colecciones"])
