"""Comparar una distribución de Forusight con el reporte de distribución de Neogística.

Sólo lectura: el reporte de Neogística NO entra al cálculo de Forusight; aquí sólo se pone al
lado para ver dónde y por qué difieren (mismas tiendas, mismos SKU de las marcas de la corrida).
"""

from __future__ import annotations

import io

import numpy as np
import pandas as pd

Q = "Cantidad Pedida Final [un]"
COLS = {
    "Stock Físico [un]": "fisico",
    "Stock Trán. Int. [un]": "transito",
    "Nivel Máximo [un]": "nivel",
    "Punto Reorden [un]": "reorden",
    "Stock Mínimo Total": "stock_minimo",
    "Pronóstico Demanda [un/semana]": "pronostico",
    Q: "cantidad",
}
DESC = ["Nombre Centro", "Marca", "Modelo", "Color", "Talla", "Temporada comercial"]


def _clave(df: pd.DataFrame) -> pd.MultiIndex:
    t = df["Código Centro"].astype(str).str.strip().str.lstrip("0")
    s = (
        df["Código SKU"]
        .astype(str)
        .str.strip()
        .str.lstrip("0")
        .str.replace(r"\.0$", "", regex=True)
    )
    return pd.MultiIndex.from_arrays([t, s], names=["tienda", "sku"])


def _num(s) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def leer_archivo_forusight(contenido: bytes) -> pd.DataFrame:
    """Hoja «Distribución» de un archivo Forusight descargado (encabezado en la fila 7)."""
    return pd.read_excel(
        io.BytesIO(contenido),
        sheet_name="Distribución",
        header=6,
        dtype={"Código SKU": str, "Código Centro": str},
    )


def comparar(forusight: pd.DataFrame, neo: pd.DataFrame) -> dict:
    """Compara tienda × SKU. El reporte se limita a las tiendas y marcas del archivo de
    Forusight. Devuelve resumen, por tienda y detalle (filas con distinta cantidad)."""
    f = forusight.copy()
    f.index = _clave(f)
    f = f[~f.index.duplicated(keep="last")]
    n = neo.copy()
    n.index = _clave(n)
    n = n[~n.index.duplicated(keep="last")]
    tiendas = set(f.index.get_level_values(0))
    marcas = set(f["Marca"].astype(str).str.upper()) if "Marca" in f else None
    dentro = n.index.get_level_values(0).isin(tiendas)
    if marcas and "Marca" in n:
        dentro &= n["Marca"].astype(str).str.upper().isin(marcas).to_numpy()
    if "Grupo Requerimiento" in n:  # las cargas manuales no las genera ningún sugerido
        dentro &= ~n["Grupo Requerimiento"].astype(str).str.startswith("Carga").to_numpy()
    n = n.loc[dentro]

    idx = f.index.union(n.index)
    d = pd.DataFrame(index=idx)
    for col, nombre in COLS.items():
        d[nombre] = _num(f[col]).reindex(idx) if col in f else np.nan
        d[nombre + "_neo"] = _num(n[col]).reindex(idx) if col in n else np.nan
    if "Stock Trán. Prov. [un]" in n:  # el tránsito del reporte es interno + proveedor
        d["transito_neo"] = d["transito_neo"].fillna(0) + _num(n["Stock Trán. Prov. [un]"]).reindex(
            idx
        ).fillna(0)
    for c in DESC:
        d[c] = (f[c].reindex(idx) if c in f else pd.Series(pd.NA, index=idx)).fillna(
            n[c].reindex(idx) if c in n else pd.NA
        )
    d["pendiente_neo"] = (
        _num(n.get("Pendiente Reposición")).reindex(idx) if "Pendiente Reposición" in n else 0
    )
    d["motivo_neo"] = (
        n["Motivo Pendiente Reposición"].reindex(idx)
        if "Motivo Pendiente Reposición" in n
        else pd.NA
    )
    d["motivo_forusight"] = f["Motivo Forusight"].reindex(idx) if "Motivo Forusight" in f else pd.NA
    d["en_forusight"] = idx.isin(f.index)
    d["en_neo"] = idx.isin(n.index)
    q, qn = d["cantidad"].fillna(0), d["cantidad_neo"].fillna(0)
    d["diferencia"] = q - qn
    d["causa"] = _causas(d)

    ambos = d["en_forusight"] & d["en_neo"]

    def igual(a: str) -> float:
        x, y = d.loc[ambos, a], d.loc[ambos, a + "_neo"]
        ok = x.notna() & y.notna()
        return float(np.isclose(x[ok], y[ok]).mean()) if ok.any() else float("nan")

    con_envio = ambos & ((q > 0) | (qn > 0))
    resumen = {
        "unidades_forusight": float(q.sum()),
        "unidades_neogistica": float(qn.sum()),
        "filas_comparadas": int(ambos.sum()),
        "cantidad_igual_con_envio": float(np.isclose(q[con_envio], qn[con_envio]).mean())
        if con_envio.any()
        else float("nan"),
        "stock_fisico_igual": igual("fisico"),
        "transito_igual": igual("transito"),
        "nivel_igual": igual("nivel"),
    }
    por_tienda = (
        d.assign(q=q, qn=qn)
        .groupby("Nombre Centro", dropna=False)
        .agg(forusight=("q", "sum"), neogistica=("qn", "sum"))
        .assign(diferencia=lambda x: x["forusight"] - x["neogistica"])
        .sort_values("diferencia", ascending=False)
        .reset_index()
    )
    causas = (
        d.loc[d["diferencia"] != 0]
        .assign(
            mas=lambda x: x["diferencia"].clip(lower=0),
            menos=lambda x: (-x["diferencia"]).clip(lower=0),
        )
        .groupby("causa")
        .agg(
            filas=("diferencia", "size"),
            forusight_envia_mas=("mas", "sum"),
            forusight_envia_menos=("menos", "sum"),
        )
        .reset_index()
        .sort_values("forusight_envia_mas", ascending=False)
    )
    detalle = d.loc[d["diferencia"] != 0].reset_index()
    orden = ["tienda", "sku", *DESC, "causa", "diferencia", "cantidad", "cantidad_neo"]
    orden += [c for c in detalle.columns if c not in orden]
    return {
        "resumen": resumen,
        "por_tienda": por_tienda,
        "causas": causas,
        "detalle": detalle[orden].sort_values(["causa", "tienda", "sku"]),
    }


def _causas(d: pd.DataFrame) -> pd.Series:
    """Primera diferencia de insumos que explica la distinta cantidad (en orden)."""

    def dif(a: str) -> pd.Series:
        x, y = d[a].fillna(0), d[a + "_neo"].fillna(0)
        return ~np.isclose(x, y)

    reglas = [
        (~d["en_neo"], "Neogística no tiene esa tienda × talla"),
        (
            ~d["en_forusight"],
            "Forusight no evalúa esa tienda × talla (temporada, bloqueo o surtido)",
        ),
        (
            d["motivo_neo"].isin(["Almacenamiento", "Stock CD"])
            & (d["pendiente_neo"].fillna(0) > 0),
            "Neogística no envió por " + d["motivo_neo"].astype(str),
        ),
        (dif("fisico"), "Distinto stock físico"),
        (dif("transito"), "Distinto tránsito"),
        (dif("nivel"), "Distinto nivel máximo (stock mínimo o pronóstico)"),
        (
            d["motivo_forusight"].astype(str).str.contains("ruta anterior", na=False),
            "Forusight repone lo vendido desde la ruta anterior",
        ),
    ]
    out = pd.Series("Reparto del CD u otra regla", index=d.index)
    for cond, texto in reversed(reglas):
        c = pd.Series(cond, index=d.index).fillna(False).astype(bool).to_numpy()
        out = out.where(~c, texto)
    return out


def a_excel(comp: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as w:
        pd.DataFrame([comp["resumen"]]).T.rename(columns={0: "valor"}).to_excel(
            w, sheet_name="Resumen"
        )
        comp["causas"].to_excel(w, sheet_name="Causas", index=False)
        comp["por_tienda"].to_excel(w, sheet_name="Por tienda", index=False)
        comp["detalle"].to_excel(w, sheet_name="Detalle", index=False)
    return buf.getvalue()
