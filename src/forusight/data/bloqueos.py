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

#: Maestro de bloqueos (reporte 1003 «Modelos Bloqueados» de Neogística del 14/10/2026, las 4
#: partes): tienda_id, modelo_color_id, motivo y temporada comercial de cada bloqueo. Sobre él se
#: aplican los bloqueos y desbloqueos cargados por Excel en el módulo Bloqueos.
POR_DEFECTO = Path(__file__).resolve().parents[1] / "config" / "bloqueos.csv.gz"
FECHA_POR_DEFECTO = "14/10/2026"
COLUMNAS_MAESTRO = ["tienda_id", "modelo_color_id", "motivo", "temporada"]


@lru_cache(maxsize=1)
def por_defecto() -> pd.DataFrame:
    if not POR_DEFECTO.exists():
        return pd.DataFrame(columns=[*COLUMNAS_MAESTRO, "origen"])
    d = pd.read_csv(POR_DEFECTO, dtype=str, keep_default_na=False)
    d = d.reindex(columns=COLUMNAS_MAESTRO, fill_value="")
    return d.assign(origen="reporte de bloqueos del " + FECHA_POR_DEFECTO)


def maestro_a_csv(d: pd.DataFrame) -> bytes:
    """Bloqueos leídos del reporte (:func:`leer` + :func:`unir`) → contenido de bloqueos.csv.gz:
    una fila por tienda × modelo-color, ordenada, con motivo y temporada."""
    import gzip

    out = d.reindex(columns=COLUMNAS_MAESTRO).fillna("").astype(str)
    out = out.drop_duplicates(["tienda_id", "modelo_color_id"])
    num = pd.to_numeric(out["tienda_id"], errors="coerce").fillna(10**9)
    out = out.assign(_n=num).sort_values(["_n", "tienda_id", "modelo_color_id"]).drop(columns="_n")
    return gzip.compress(out.to_csv(index=False).encode("utf-8"), mtime=0)


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
PLANTILLA_COLUMNAS = ["COD MOD COL", "Cod Tienda"]
_ALIAS_MC = {
    "modcol",
    "modelocolor",
    "codmodcol",
    "codigomodcol",
    "codigomodelocolor",
    "codigomodelocolors",
    "mc",
}
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


def _norm_col(c) -> str:
    import unicodedata

    t = unicodedata.normalize("NFKD", str(c)).encode("ascii", "ignore").decode().lower()
    return "".join(ch for ch in t if ch.isalnum())


def plantilla_excel() -> bytes:
    """Excel de ejemplo con las columnas COD MOD COL y Cod Tienda."""
    buf = io.BytesIO()
    pd.DataFrame(
        {"COD MOD COL": ["HP10201162490-N11", "HP10201162490-N11"], "Cod Tienda": ["8", ""]}
    ).to_excel(buf, index=False, sheet_name="Bloqueos")
    return buf.getvalue()


def a_excel_carga(bloqueos: pd.DataFrame) -> bytes:
    """Bloqueos (tienda_id, modelo_color_id) → Excel en el formato de carga (COD MOD COL,
    Cod Tienda). «Todas las tiendas» se escribe con Cod Tienda vacío, como lo lee
    :func:`leer_carga`."""
    buf = io.BytesIO()
    tienda = bloqueos["tienda_id"].astype(str)
    pd.DataFrame(
        {
            "COD MOD COL": bloqueos["modelo_color_id"].astype(str).to_numpy(),
            "Cod Tienda": tienda.where(tienda.ne(TODAS), "").to_numpy(),
        }
    ).to_excel(buf, index=False, sheet_name="Bloqueos")
    return buf.getvalue()


def _es_tienda_todas(x: str) -> bool:
    return x == "" or x == TODAS or x.upper() in ("TODAS", "NAN", "<NA>")


def leer_carga_detalle(contenido: bytes, nombre: str = "") -> pd.DataFrame:
    """Excel/CSV de carga → una fila por fila del archivo, sin descartar nada: fila (de Excel),
    mod_col y tienda tal cual, modelo_color_id y tienda_id normalizados (sin tienda = «*»)."""
    from forusight.data.fuentes import codigo_tienda

    if str(nombre).lower().endswith(".csv"):
        df = pd.read_csv(io.BytesIO(contenido), dtype=str, sep=None, engine="python")
    else:
        df = pd.read_excel(io.BytesIO(contenido), dtype=str)
    cols = {_norm_col(c): c for c in df.columns}
    mc = next((cols[a] for a in cols if a in _ALIAS_MC), None)
    if mc is None:
        raise ValueError(
            "El archivo debe tener la columna «COD MOD COL» (y opcionalmente «Cod Tienda»)."
        )
    ti = next((cols[a] for a in cols if a in _ALIAS_TIENDA), None)
    crudo_mc = df[mc].fillna("").astype(str).str.strip()
    crudo_t = df[ti].fillna("").astype(str).str.strip() if ti else pd.Series("", index=df.index)
    out = pd.DataFrame(
        {
            "fila": range(2, len(df) + 2),  # fila de Excel (1 = cabecera)
            "mod_col": crudo_mc,
            "tienda": crudo_t,
            "modelo_color_id": crudo_mc.str.upper().str.replace(r"\s+", "", regex=True),
            "tienda_id": [TODAS if _es_tienda_todas(x) else codigo_tienda(x) for x in crudo_t],
        }
    )
    # filas totalmente vacías (formato de Excel arrastrado): no son datos
    return out.loc[out["mod_col"].ne("") | out["tienda"].ne("")].reset_index(drop=True)


RESULTADO_OK = "OK"


def validar_carga(
    detalle: pd.DataFrame,
    accion: str,
    efectivos: pd.DataFrame | None,
    tiendas: set[str] | None = None,
) -> pd.DataFrame:
    """Valida la carga antes de guardarla. Devuelve ``detalle`` con ``resultado``: «OK…» se
    guarda; «Rechazada…» es un error del archivo; «Omitida…» no cambiaría nada.

    - Rechazada: falta COD MOD COL, formato que no es MODELO-COLOR, tienda que no existe, o
      desbloquear en una tienda un modelo-color bloqueado para todas las tiendas.
    - Omitida: fila repetida, bloquear lo que ya está bloqueado, desbloquear lo que no lo está.
    Así no se duplican bloqueos ni se tocan registros que no corresponden."""
    d = detalle.copy()
    res = pd.Series("", index=d.index, dtype=object)
    mc, t = d["modelo_color_id"].astype(str), d["tienda_id"].astype(str)
    res[mc.eq("")] = "Rechazada: falta COD MOD COL"
    libre = res.eq("")
    res[libre & ~mc.map(_es_modelo_color)] = (
        "Rechazada: COD MOD COL inválido (formato MODELO-COLOR)"
    )
    if tiendas:
        libre = res.eq("")
        res[libre & t.ne(TODAS) & ~t.isin(tiendas)] = "Rechazada: la tienda no existe en el maestro"
    libre = res.eq("")
    clave = t + "|" + mc
    res[libre & clave.where(libre).duplicated(keep="first")] = (
        "Omitida: fila repetida en el archivo"
    )

    ef = (
        efectivos
        if efectivos is not None
        else pd.DataFrame(columns=["tienda_id", "modelo_color_id"])
    )
    ef_t, ef_mc = ef["tienda_id"].astype(str), ef["modelo_color_id"].astype(str)
    claves_ef = set(ef_t + "|" + ef_mc)
    en_todas = set(ef_mc[ef_t.eq(TODAS)])
    con_bloqueo = set(ef_mc)
    libre = res.eq("")
    if accion == BLOQUEAR:
        ya = clave.isin(claves_ef) | (t.ne(TODAS) & mc.isin(en_todas))
        res[libre & ya] = "Omitida: ya está bloqueado"
        res[res.eq("")] = "OK: se bloquea"
    else:
        tienda_bajo_todas = t.ne(TODAS) & mc.isin(en_todas) & ~clave.isin(claves_ef)
        res[libre & tienda_bajo_todas] = (
            "Rechazada: está bloqueado para todas las tiendas; desbloquéalo con Cod Tienda vacío"
        )
        libre = res.eq("")
        no_esta = (t.ne(TODAS) & ~clave.isin(claves_ef)) | (t.eq(TODAS) & ~mc.isin(con_bloqueo))
        res[libre & no_esta] = "Omitida: no está bloqueado"
        res[res.eq("")] = "OK: se desbloquea"
    d["resultado"] = res
    return d


def validas(validada: pd.DataFrame) -> pd.DataFrame:
    """Filas de :func:`validar_carga` que se guardan (tienda_id, modelo_color_id)."""
    ok = validada["resultado"].astype(str).str.startswith(RESULTADO_OK)
    return validada.loc[ok, ["tienda_id", "modelo_color_id"]].reset_index(drop=True)


def maestro_completo(
    efectivos: pd.DataFrame,
    manuales: pd.DataFrame | None,
    tiendas: pd.DataFrame | None = None,
    temporadas: pd.Series | None = None,
) -> pd.DataFrame:
    """Maestro de bloqueos vigente (reporte + cargas por Excel), una fila por tienda ×
    modelo-color, con nombre y cadena de la tienda, temporada, motivo, origen y quién/cuándo
    (para los cargados por Excel). ``tiendas``: codigo_tienda, nombre_tienda, cadena."""
    e = efectivos.copy()
    for c in ("motivo", "temporada", "origen"):
        if c not in e:
            e[c] = ""
    e = e.fillna("")
    v = vigentes(manuales)
    v = v.loc[v["accion"].eq(BLOQUEAR), ["tienda_id", "modelo_color_id", "usuario", "fecha"]]
    e = e.merge(v, on=["tienda_id", "modelo_color_id"], how="left")
    if temporadas is not None and len(temporadas):
        falta = e["temporada"].astype(str).eq("")
        e.loc[falta, "temporada"] = e.loc[falta, "modelo_color_id"].map(temporadas)
    nombre = cadena = pd.Series("", index=e.index)
    if tiendas is not None and len(tiendas):
        t = tiendas.drop_duplicates("codigo_tienda").set_index("codigo_tienda")
        nombre = e["tienda_id"].astype(str).map(t["nombre_tienda"])
        cadena = e["tienda_id"].astype(str).map(t["cadena"])
    todas = e["tienda_id"].astype(str).eq(TODAS)
    out = pd.DataFrame(
        {
            "COD MOD COL": e["modelo_color_id"],
            "Cod Tienda": e["tienda_id"].where(~todas, ""),
            "Tienda": nombre.where(~todas, "TODAS LAS TIENDAS"),
            "Cadena": cadena.where(~todas, ""),
            "Temporada comercial": e["temporada"],
            "Motivo bloqueo": e["motivo"],
            "Origen": e["origen"].astype(str).str.replace(r"^manual: ", "Excel: ", regex=True),
            "Usuario": e["usuario"],
            "Fecha carga": e["fecha"],
        }
    ).fillna("")
    num = pd.to_numeric(out["Cod Tienda"], errors="coerce").fillna(-1)
    return (
        out.assign(_n=num)
        .sort_values(["_n", "COD MOD COL"])
        .drop(columns="_n")
        .reset_index(drop=True)
    )


def maestro_excel(maestro: pd.DataFrame, fecha: str = "") -> bytes:
    """Maestro completo → Excel: hoja «Maestro bloqueos» (con autofiltro; se puede editar y
    volver a subir: COD MOD COL y Cod Tienda son las columnas de carga) y «Resumen» por tienda."""
    buf = io.BytesIO()
    resumen = (
        maestro.groupby(["Cod Tienda", "Tienda", "Cadena"], dropna=False)
        .agg(**{"Modelos-color bloqueados": ("COD MOD COL", "nunique")})
        .reset_index()
    )
    resumen = (
        resumen.assign(_n=pd.to_numeric(resumen["Cod Tienda"], errors="coerce").fillna(-1))
        .sort_values("_n")
        .drop(columns="_n")
    )
    import xlsxwriter

    # Directo con xlsxwriter, fila a fila y en memoria constante: ~225 mil filas en segundos
    # (pandas.to_excel tarda varias veces más).
    wb = xlsxwriter.Workbook(buf, {"constant_memory": True, "in_memory": True})
    negrita = wb.add_format({"bold": True, "bg_color": "#1F3A93", "font_color": "#FFFFFF"})
    ws = wb.add_worksheet("Maestro bloqueos")
    for i, ancho in enumerate([20, 10, 26, 8, 16, 40, 34, 26, 16]):
        ws.set_column(i, i, ancho)
    ws.freeze_panes(1, 0)
    ws.autofilter(0, 0, max(len(maestro), 1), len(maestro.columns) - 1)
    ws.write_row(0, 0, list(maestro.columns), negrita)
    for n, fila in enumerate(maestro.astype(str).itertuples(index=False, name=None), start=1):
        ws.write_row(n, 0, fila)
    wr = wb.add_worksheet("Resumen")
    wr.write(0, 0, f"Maestro de bloqueos al {fecha}".strip() + f" · {len(maestro):,} filas")
    wr.set_column(0, 0, 10)
    wr.set_column(1, 1, 28)
    wr.set_column(3, 3, 24)
    wr.write_row(2, 0, list(resumen.columns), negrita)
    for n, fila in enumerate(resumen.itertuples(index=False, name=None), start=3):
        wr.write_row(n, 0, [x if isinstance(x, int | float) else str(x) for x in fila])
    wb.close()
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
    if "origen" not in b:
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


#: Calendario comercial de Forus: INVIERNO de un año va de marzo a septiembre y VERANO de
#: septiembre a marzo del año siguiente, así que INVIERNO 2026 < VERANO 2026 < INVIERNO 2027
#: (el orden NO es el del año calendario). ESCOLAR de un año es la campaña escolar que se vende
#: dentro del VERANO de ese mismo año: va con él (ESCOLAR 2026 se repone junto con VERANO 2026
#: y, para las liquidadoras, también es posterior a INVIERNO 2026).
_PASO_TEMPORADA = {"INVIERNO": 0, "VERANO": 1, "ESCOLAR": 1}


def orden_temporada(nombre) -> int | None:
    """«VERANO 2026» → 4053, «INVIERNO 2027» → 4054: INVIERNO 2026 < VERANO 2026 = ESCOLAR 2026
    < INVIERNO 2027. None si no es VERANO/INVIERNO/ESCOLAR con año (FUTURA, NO COLECCION…)."""
    import re

    m = re.fullmatch(r"(VERANO|INVIERNO|ESCOLAR)\s+(\d{4})", str(nombre or "").strip().upper())
    if not m:
        return None
    return int(m.group(2)) * 2 + _PASO_TEMPORADA[m.group(1)]


def es_temporada_minima(nombre) -> bool:
    """Temporada válida como mínima de una tienda: VERANO o INVIERNO con año de 4 dígitos."""
    import re

    return bool(re.fullmatch(r"(VERANO|INVIERNO)\s+\d{4}", str(nombre or "").strip().upper()))


def tiendas_liquidadoras(dim_tienda: pd.DataFrame | None, cadenas) -> set[str]:
    """Códigos de las tiendas de cadenas liquidadoras (DH, SE, FB).

    Usa la marca ``tienda_liquidadora`` del motor si la trae; si no, la cadena (o el prefijo del
    nombre, como en los reportes). Las tiendas que no vienen se buscan en el catálogo de tiendas."""
    from forusight.data.cadenas import catalogo_tiendas

    liq = {str(c).strip().upper() for c in cadenas or [] if str(c).strip()}
    if not liq:
        return set()
    cat = catalogo_tiendas()
    out = set(cat.loc[cat["cadena"].astype(str).str.upper().isin(liq), "codigo_tienda"].astype(str))
    if dim_tienda is None or not len(dim_tienda) or "tienda_id" not in dim_tienda:
        return out
    ids = dim_tienda["tienda_id"].astype(str)
    if "tienda_liquidadora" in dim_tienda:
        es = dim_tienda["tienda_liquidadora"].fillna(False).astype(bool)
    else:
        nombre = dim_tienda.get("nombre", pd.Series("", index=dim_tienda.index))
        cadena = dim_tienda.get("cadena", pd.Series(pd.NA, index=dim_tienda.index))
        cadena = cadena.astype("string").str.strip().str.upper()
        cadena = cadena.fillna(
            nombre.astype("string").str.extract(r"^\s*(\S+)", expand=False).str.upper()
        )
        es = cadena.isin(liq).fillna(False)
    # lo que dice la corrida manda sobre el catálogo para las tiendas que trae
    return (out - set(ids)) | set(ids[es.to_numpy()])


def regla_temporadas(surtido, dim_tienda: pd.DataFrame | None, cadenas) -> dict:
    """Argumentos de :func:`fuera_de_surtido` además del parámetro general: temporada mínima
    por tienda (módulo Temporadas), colecciones activas por marca × clase (reporte 1037) y
    reposición por jerarquía de las liquidadoras."""
    return {
        "desde_por_tienda": dict(getattr(surtido, "temporada_desde_tienda", {}) or {}),
        "colecciones": dict(getattr(surtido, "colecciones_activas", {}) or {}),
        "liquidadoras": tiendas_liquidadoras(dim_tienda, cadenas),
        "hasta_liquidadoras": getattr(surtido, "temporada_hasta_liquidadoras", None),
    }


def fuera_de_surtido(
    tienda: pd.Series,
    modelo_color: pd.Series,
    temporada: pd.Series | None,
    bloq,
    temporadas,
    desde: str | None = None,
    *,
    desde_por_tienda: dict[str, str] | None = None,
    liquidadoras=None,
    hasta_liquidadoras: str | None = None,
    colecciones: dict[str, list[str]] | None = None,
    marca: pd.Series | None = None,
    clase: pd.Series | None = None,
) -> tuple[pd.Series, pd.Series]:
    """(bloqueado, fuera_de_temporada) por fila.

    Qué temporadas se reponen en cada tienda (en este orden de prioridad):

    1. **Liquidadoras** (DH, SE, FB; ``liquidadoras``) con ``hasta_liquidadoras``: reposición
       por jerarquía, sólo temporadas hasta esa inclusive (INVIERNO 2026 hacia atrás). No
       reciben VERANO 2026, ESCOLAR 2026, temporadas posteriores ni FUTURA. Ni el parámetro
       general ni la temporada mínima por tienda las cambian.
    2. **Temporada mínima de la tienda** (``desde_por_tienda``): esa temporada y las
       posteriores (ESCOLAR del mismo año incluida). De ``temporadas`` sólo se suman las que no
       tienen orden comercial (p. ej. FUTURA) o no son anteriores a la mínima.
    3. **Parámetro general**: las de ``temporadas`` y, con ``desde``, toda temporada de esa en
       adelante. Sin ninguna de las dos = todas.

    A 2 y 3 se suman las **colecciones activas** de la marca × clase (``colecciones``, reporte
    SAP 1037; necesita ``marca`` y ``clase`` por fila): las de TODO y las propias de la tienda.

    Sin temporada conocida (modelo nuevo): se repone en todas las tiendas."""
    mc = modelo_color.astype("string").str.upper()
    tienda_txt = tienda.astype(str)
    clave = tienda_txt + "|" + mc
    if bloq is not None and len(bloq):
        todas = set(bloq.loc[bloq["tienda_id"].astype(str).eq(TODAS), "modelo_color_id"])
        bloqueado = clave.isin(set(claves(bloq))) | mc.isin(todas)
    else:
        bloqueado = pd.Series(False, index=tienda.index)
    activas = {str(t).strip().upper() for t in temporadas or [] if str(t).strip()}
    minimo = orden_temporada(desde) if desde else None
    por_tienda = {
        str(k).strip(): orden_temporada(v)
        for k, v in (desde_por_tienda or {}).items()
        if orden_temporada(v) is not None
    }
    tope_liq = orden_temporada(hasta_liquidadoras) if hasta_liquidadoras else None
    liq = {str(x) for x in liquidadoras or []} if tope_liq is not None else set()
    hay_regla = activas or minimo is not None or por_tienda or liq or colecciones
    if not hay_regla or temporada is None:
        fuera = pd.Series(False, index=tienda.index)
        return bloqueado.fillna(False).astype(bool), fuera

    t = temporada.astype("string").str.strip().str.upper()
    unicas = {x: orden_temporada(x) for x in t.dropna().unique()}
    orden = pd.to_numeric(t.map(unicas), errors="coerce")
    # 3. parámetro general
    if activas or minimo is not None:
        ok = t.isin(activas).fillna(False)
        if minimo is not None:
            ok |= orden.ge(minimo).fillna(False)
    else:
        ok = pd.Series(True, index=tienda.index)
    # 2. temporada mínima de la tienda
    if por_tienda:
        min_t = pd.to_numeric(tienda_txt.map(por_tienda), errors="coerce")
        con_min = min_t.notna()
        extra = t.isin(activas).fillna(False) & (orden.isna() | orden.ge(min_t))
        ok_t = orden.ge(min_t).fillna(False) | extra.fillna(False)
        ok = ok.where(~con_min, ok_t)
    # colecciones activas de la marca × clase en la tienda (se suman)
    if colecciones:
        from forusight.data.colecciones import activa

        ok |= activa(tienda_txt, clase, marca, t, colecciones).to_numpy()
    # 1. liquidadoras: reposición por jerarquía (hasta INVIERNO 2026)
    if liq:
        es_liq = tienda_txt.isin(liq)
        ok = ok.where(~es_liq, orden.le(tope_liq).fillna(False))
    fuera = t.notna() & ~ok.astype(bool)
    return bloqueado.fillna(False).astype(bool), fuera.fillna(False).astype(bool)
