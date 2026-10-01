"""Reporte de bloqueos de Neogística («Modelos Bloqueados»): modelo-color bloqueado por tienda.

Cada fila es un SKU de un modelo-color bloqueado en un centro («Colección No Activa en
Tienda», bloqueos pedidos por el negocio, concentración…). Un modelo-color bloqueado en una
tienda no se repone ahí, aunque la tienda lo haya vendido.
"""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

import pandas as pd

#: Bloqueos por defecto (reporte de bloqueos del 28/09/2026, las 4 partes). Se usan cuando no se
#: sube un reporte más reciente en la barra lateral.
POR_DEFECTO = Path(__file__).resolve().parents[1] / "config" / "bloqueos.csv.gz"
FECHA_POR_DEFECTO = "28/09/2026"


@lru_cache(maxsize=1)
def por_defecto() -> pd.DataFrame:
    if not POR_DEFECTO.exists():
        return pd.DataFrame(columns=["tienda_id", "modelo_color_id", "motivo"])
    d = pd.read_csv(POR_DEFECTO, dtype=str)
    return d.assign(motivo="reporte de bloqueos del " + FECHA_POR_DEFECTO)


COLUMNAS = {
    "Código centro": "tienda_id",
    "Código modelo color (s)": "modelo_color_id",
    "Motivo bloqueo": "motivo",
    "Temporada comercial": "temporada",
    "Código sku": "sku",
}


#: Orden de columnas del reporte de bloqueos (para las partes que vienen sin cabecera).
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
    raise ValueError(
        "El archivo no tiene la columna «Código modelo color (s)» del reporte de bloqueos."
    )


def normalizar(d: pd.DataFrame) -> pd.DataFrame:
    from forusight.data.fuentes import codigo_tienda

    out = pd.DataFrame(
        {
            "tienda_id": d["tienda_id"].map(codigo_tienda),
            "modelo_color_id": d["modelo_color_id"].astype("string").str.strip().str.upper(),
            "motivo": d.get("motivo", pd.Series("", index=d.index)).astype("string"),
            "temporada": d.get("temporada", pd.Series(pd.NA, index=d.index))
            .astype("string")
            .str.strip()
            .str.upper(),
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


# ------------------------------------------------------------------ mantenedor de bloqueos

#: Ruta (dentro del almacenamiento de GitHub) de los bloqueos y desbloqueos manuales.
RUTA_MANUALES = "bloqueos/bloqueos_manuales.csv"
COLUMNAS_MANUALES = ["tienda_id", "modelo_color_id", "accion", "motivo", "usuario", "fecha"]
BLOQUEAR, DESBLOQUEAR = "BLOQUEAR", "DESBLOQUEAR"
TODAS = "*"  # tienda_id "*" = el modelo-color en todas las tiendas


def manuales_vacio() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNAS_MANUALES)


def leer_manuales(contenido: bytes | None) -> pd.DataFrame:
    if not contenido:
        return manuales_vacio()
    d = pd.read_csv(io.BytesIO(contenido), dtype=str, encoding="utf-8-sig").fillna("")
    return d.reindex(columns=COLUMNAS_MANUALES, fill_value="")


def nuevos_manuales(
    tiendas: list[str], modelos: list[str], accion: str, motivo: str, usuario: str
) -> pd.DataFrame:
    from forusight.data.fuentes import codigo_tienda

    ahora = pd.Timestamp.now(tz="America/Lima").strftime("%Y-%m-%d %H:%M")
    filas = [
        {
            "tienda_id": TODAS if t == TODAS else codigo_tienda(t),
            "modelo_color_id": str(m).strip().upper(),
            "accion": accion,
            "motivo": motivo.strip(),
            "usuario": usuario,
            "fecha": ahora,
        }
        for t in tiendas
        for m in modelos
        if str(m).strip()
    ]
    return pd.DataFrame(filas, columns=COLUMNAS_MANUALES)


#: Carga masiva por Excel: una fila por modelo-color (y tienda; vacía = todas las tiendas).
PLANTILLA_COLUMNAS = ["Mod-Col", "Cod Tienda"]
_ALIAS_MC = {"modcol", "modelocolor", "codmodcol", "codigomodelocolor", "codigomodelocolors", "mc"}
_ALIAS_TIENDA = {"codtienda", "codigotienda", "tienda", "codcentro", "codigocentro", "centro"}


def _norm_col(c) -> str:
    import unicodedata

    t = unicodedata.normalize("NFKD", str(c)).encode("ascii", "ignore").decode().lower()
    return "".join(ch for ch in t if ch.isalnum())


def plantilla_excel() -> bytes:
    """Excel de ejemplo con las columnas Mod-Col y Cod Tienda."""
    buf = io.BytesIO()
    pd.DataFrame(
        {"Mod-Col": ["HP10201162490-N11", "HP10201162490-N11"], "Cod Tienda": ["8", ""]}
    ).to_excel(buf, index=False, sheet_name="Bloqueos")
    return buf.getvalue()


def leer_carga(contenido: bytes, nombre: str = "") -> pd.DataFrame:
    """Excel/CSV con Mod-Col y Cod Tienda → (tienda_id, modelo_color_id). Sin tienda = «*»."""
    from forusight.data.fuentes import codigo_tienda

    if str(nombre).lower().endswith(".csv"):
        df = pd.read_csv(io.BytesIO(contenido), dtype=str, sep=None, engine="python")
    else:
        df = pd.read_excel(io.BytesIO(contenido), dtype=str)
    cols = {_norm_col(c): c for c in df.columns}
    mc = next((cols[a] for a in cols if a in _ALIAS_MC), None)
    if mc is None:
        raise ValueError(
            "El archivo debe tener la columna «Mod-Col» (y opcionalmente «Cod Tienda»)."
        )
    ti = next((cols[a] for a in cols if a in _ALIAS_TIENDA), None)
    out = pd.DataFrame(
        {
            "modelo_color_id": df[mc].astype("string").str.strip().str.upper(),
            "tienda_id": df[ti].astype("string").str.strip() if ti else pd.NA,
        }
    )
    out = out.loc[out["modelo_color_id"].fillna("").ne("")]
    t = out["tienda_id"].fillna("").astype(str)
    out["tienda_id"] = [
        TODAS if (x == "" or x == TODAS or x.upper() in ("TODAS", "NAN")) else codigo_tienda(x)
        for x in t
    ]
    return out.drop_duplicates().reset_index(drop=True)[["tienda_id", "modelo_color_id"]]


def nuevos_desde_carga(carga: pd.DataFrame, accion: str, motivo: str, usuario: str) -> pd.DataFrame:
    ahora = pd.Timestamp.now(tz="America/Lima").strftime("%Y-%m-%d %H:%M")
    return carga.assign(accion=accion, motivo=str(motivo).strip(), usuario=usuario, fecha=ahora)[
        COLUMNAS_MANUALES
    ].reset_index(drop=True)


def vigentes(manuales: pd.DataFrame) -> pd.DataFrame:
    """Última acción por tienda × modelo-color (la más reciente manda)."""
    if manuales is None or manuales.empty:
        return manuales_vacio()
    return manuales.drop_duplicates(["tienda_id", "modelo_color_id"], keep="last").reset_index(
        drop=True
    )


def aplicar_manuales(base: pd.DataFrame | None, manuales: pd.DataFrame | None) -> pd.DataFrame:
    """Bloqueos efectivos = reporte de bloqueos + bloqueos manuales − desbloqueos manuales.

    Un desbloqueo con tienda «*» libera el modelo-color en todas las tiendas; un bloqueo con
    tienda «*» queda como fila «*» (fuera_de_surtido la aplica a todas)."""
    b = base.copy() if base is not None else pd.DataFrame(columns=["tienda_id", "modelo_color_id"])
    b = b.assign(origen=b.get("motivo", "reporte de bloqueos"))
    v = vigentes(manuales)
    if v.empty:
        return b
    clave = b["tienda_id"].astype(str) + "|" + b["modelo_color_id"].astype(str)
    des = v.loc[v["accion"].eq(DESBLOQUEAR)]
    quita = set(des["tienda_id"] + "|" + des["modelo_color_id"])
    todas = set(des.loc[des["tienda_id"].eq(TODAS), "modelo_color_id"])
    b = b.loc[~clave.isin(quita) & ~b["modelo_color_id"].isin(todas)]
    blo = v.loc[v["accion"].eq(BLOQUEAR), ["tienda_id", "modelo_color_id", "motivo"]]
    blo = blo.assign(origen="manual: " + blo["motivo"].where(blo["motivo"].ne(""), "sin motivo"))
    return (
        pd.concat([b, blo], ignore_index=True)
        .drop_duplicates(["tienda_id", "modelo_color_id"], keep="last")
        .reset_index(drop=True)
    )


def fuera_de_surtido(
    tienda: pd.Series, modelo_color: pd.Series, temporada: pd.Series | None, bloq, temporadas
) -> tuple[pd.Series, pd.Series]:
    """(bloqueado, fuera_de_temporada) por fila. ``temporadas`` vacío = todas."""
    mc = modelo_color.astype("string").str.upper()
    clave = tienda.astype(str) + "|" + mc
    if bloq is not None and len(bloq):
        todas = set(bloq.loc[bloq["tienda_id"].astype(str).eq(TODAS), "modelo_color_id"])
        bloqueado = clave.isin(set(claves(bloq))) | mc.isin(todas)
    else:
        bloqueado = pd.Series(False, index=tienda.index)
    activas = {str(t).strip().upper() for t in temporadas or [] if str(t).strip()}
    if activas and temporada is not None:
        t = temporada.astype("string").str.strip().str.upper()
        fuera = t.notna() & ~t.isin(activas)
    else:
        fuera = pd.Series(False, index=tienda.index)
    return bloqueado.fillna(False).astype(bool), fuera.fillna(False).astype(bool)
