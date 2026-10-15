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


# ------------------------------------------------- temporada mínima por tienda (módulo Temporadas)

#: Temporadas fuera de parámetros: desde qué temporada comercial se repone cada tienda. Una
#: tienda sin fila (o con la última acción QUITAR) usa el parámetro general
#: ``surtido.temporada_desde``. Las liquidadoras no se configuran aquí: reponen por jerarquía.
RUTA_TEMPORADA_TIENDA = "mantenedores/temporada_minima_tienda.csv"
COLS_TEMPORADA_TIENDA = ["tienda_id", "temporada_minima", "accion", "usuario", "fecha"]
ASIGNAR, QUITAR = "ASIGNAR", "QUITAR"
PLANTILLA_TEMPORADA_TIENDA = ["Cod Tienda", "Temporada minima"]
_ALIAS_TIENDA = {
    "codtienda",
    "codigotienda",
    "codigodetienda",
    "coddetienda",
    "tienda",
    "tiendaid",
    "codcentro",
    "codigocentro",
    "centro",
}
_ALIAS_TEMPORADA = {
    "temporadaminima",
    "temporadaminimadereposicion",
    "temporadadesde",
    "desdetemporada",
    "temporada",
    "temporadacomercial",
}


def _norm_col(c) -> str:
    import unicodedata

    t = unicodedata.normalize("NFKD", str(c)).encode("ascii", "ignore").decode().lower()
    return "".join(ch for ch in t if ch.isalnum())


def _norm_temporada(x) -> str:
    return " ".join(str(x or "").strip().upper().split())


def leer_carga_temporadas(contenido: bytes, nombre: str = "") -> pd.DataFrame:
    """Excel/CSV con Cod Tienda y Temporada mínima → fila, tienda (texto), temporada (texto).

    No descarta nada: la validación (:func:`validar_temporadas_tienda`) explica cada fila."""
    if str(nombre).lower().endswith(".csv"):
        df = pd.read_csv(io.BytesIO(contenido), dtype=str, sep=None, engine="python")
    else:
        df = pd.read_excel(io.BytesIO(contenido), dtype=str)
    cols = {_norm_col(c): c for c in df.columns}
    ti = next((cols[a] for a in cols if a in _ALIAS_TIENDA), None)
    te = next((cols[a] for a in cols if a in _ALIAS_TEMPORADA), None)
    if ti is None or te is None:
        raise ValueError("El archivo debe tener las columnas «Cod Tienda» y «Temporada minima».")
    out = pd.DataFrame(
        {
            "fila": range(2, len(df) + 2),  # fila de Excel (1 = cabecera)
            "tienda": df[ti].fillna("").astype(str).str.strip(),
            "temporada": df[te].fillna("").map(_norm_temporada),
        }
    )
    return out.loc[out["tienda"].ne("") | out["temporada"].ne("")].reset_index(drop=True)


def temporada_tienda_vigente(hist: pd.DataFrame | None) -> dict[str, str]:
    """tienda_id → temporada mínima (última acción de cada tienda; QUITAR la saca)."""
    if hist is None or hist.empty:
        return {}
    d = hist.assign(
        tienda_id=hist["tienda_id"].astype(str).str.strip(),
        temporada_minima=hist["temporada_minima"].map(_norm_temporada),
    )
    d = d.loc[d["tienda_id"].ne("")].drop_duplicates("tienda_id", keep="last")
    d = d.loc[d["accion"].astype(str).str.upper().ne(QUITAR) & d["temporada_minima"].ne("")]
    return dict(zip(d["tienda_id"], d["temporada_minima"], strict=True))


def validar_temporadas_tienda(
    carga: pd.DataFrame,
    accion: str,
    vigente: dict[str, str],
    tiendas: set[str],
    liquidadoras: set[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(cambios a guardar: tienda_id, temporada_minima; observaciones por fila).

    Se rechaza: tienda vacía o que no existe, tienda liquidadora (repone por jerarquía),
    temporada que no es VERANO/INVIERNO con año (al asignar), una tienda repetida con temporadas
    distintas. Se omite lo que no cambia nada (misma temporada, o quitar una tienda que ya usa
    el parámetro general) y las filas repetidas."""
    from forusight.data.bloqueos import es_temporada_minima
    from forusight.data.fuentes import codigo_tienda

    d = carga.copy()
    d["tienda_id"] = [codigo_tienda(t) if str(t).strip() else "" for t in d["tienda"]]
    estado = pd.Series("", index=d.index, dtype=object)
    estado[d["tienda_id"].eq("")] = "Rechazada: falta el código de tienda"
    sin = estado.eq("")
    estado[sin & ~d["tienda_id"].isin(tiendas)] = "Rechazada: la tienda no existe en el maestro"
    sin = estado.eq("")
    estado[sin & d["tienda_id"].isin(liquidadoras)] = (
        "Rechazada: tienda liquidadora (DH, SE, FB): repone por jerarquía, no se configura"
    )
    if accion == ASIGNAR:
        sin = estado.eq("")
        # el Excel descargado trae vacías las tiendas que siguen el parámetro general
        estado[sin & d["temporada"].eq("")] = (
            "Omitida: sin temporada (sigue como está; para quitar una usa «Volver al general»)"
        )
        sin = estado.eq("")
        valida = d["temporada"].map(es_temporada_minima)
        estado[sin & ~valida] = (
            "Rechazada: temporada inválida (usa VERANO AAAA o INVIERNO AAAA, p. ej. VERANO 2026)"
        )
        # una misma tienda con dos temporadas distintas en el archivo: no se adivina cuál vale
        sin = estado.eq("")
        distintas = d.loc[sin].groupby("tienda_id")["temporada"].transform("nunique").gt(1)
        estado[distintas.index[distintas.to_numpy()]] = (
            "Rechazada: la tienda aparece con temporadas distintas en el archivo"
        )
    sin = estado.eq("")
    clave = d["tienda_id"] if accion == QUITAR else d["tienda_id"] + "|" + d["temporada"]
    repetida = sin & clave.where(sin).duplicated(keep="first")
    estado[repetida] = "Omitida: fila repetida en el archivo"
    sin = estado.eq("")
    actual = d["tienda_id"].map(vigente)
    if accion == ASIGNAR:
        igual = sin & actual.eq(d["temporada"]).fillna(False)
        estado[igual] = "Omitida: la tienda ya tiene esa temporada mínima"
    else:
        igual = sin & actual.isna()
        estado[igual] = "Omitida: la tienda ya usa el parámetro general"
    ok = estado.eq("")
    estado[ok] = "OK: se asigna" if accion == ASIGNAR else "OK: vuelve al parámetro general"
    d["actual"] = actual.fillna("(parámetro general)")
    d["resultado"] = estado
    cambios = d.loc[ok, ["tienda_id", "temporada"]].rename(
        columns={"temporada": "temporada_minima"}
    )
    if accion == QUITAR:
        cambios["temporada_minima"] = ""
    return cambios.reset_index(drop=True), d[
        ["fila", "tienda", "tienda_id", "temporada", "actual", "resultado"]
    ]


def nuevos_temporada_tienda(
    cambios: pd.DataFrame, accion: str, usuario: str, fecha
) -> pd.DataFrame:
    return cambios.assign(accion=accion, usuario=usuario, fecha=str(fecha))[
        COLS_TEMPORADA_TIENDA
    ].reset_index(drop=True)


def plantilla_temporada_tienda(filas: pd.DataFrame | None = None) -> bytes:
    """Excel de carga (Cod Tienda, Temporada minima). Con ``filas`` (tienda_id,
    temporada_minima) sale con la configuración vigente, lista para editar y volver a subir."""
    if filas is None:
        filas = pd.DataFrame({"tienda_id": ["8", "12"], "temporada_minima": ["VERANO 2026"] * 2})
    buf = io.BytesIO()
    out = pd.DataFrame(
        {
            "Cod Tienda": filas["tienda_id"].astype(str).to_numpy(),
            "Temporada minima": filas["temporada_minima"].fillna("").astype(str).to_numpy(),
        }
    )
    if "nombre_tienda" in filas:  # sólo de referencia: la carga no la lee
        out["Nombre tienda"] = filas["nombre_tienda"].astype(str).to_numpy()
    out.to_excel(buf, index=False, sheet_name="Temporadas")
    return buf.getvalue()
