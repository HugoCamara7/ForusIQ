"""Lectura directa de las tablas fuente de Forus → contratos canónicos del motor.

Fuentes (rutas en [bigquery] de los secrets, columnas por mapeo):
  - ventas_table          venta → VentaSemanalSchema (agregada por semana en el servidor)
  - product_master_table  ARTI → DimProductoSchema (filtrado por marca, p. ej. AZALEIA)
  - stock_table           stock por fecha de corte (p. ej. stg_pe_central_stock_bi):
                            · última foto → StockTiendaSchema y, en la bodega del CD, StockCD
                            · historial   → dias_con_stock por semana (pendiente a)
  - STOCK CD.xlsx (opcional, subido en la app): disponible y reservas del CD 320,
    el mismo archivo que usa Repo Control Center. Si está, reemplaza al stock del CD.

Costo: filtro de fecha parametrizado en el WHERE, agregación en el servidor, semijoin con
ARTI por marca, columnas explícitas y dry run con tope de GB antes de cada consulta.
"""

from __future__ import annotations

import datetime as dt
import io
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from forusight.data.bq_client import validar_columna, validar_tabla

# ------------------------------------------------------------------ normalización


def codigo_tienda(valor: Any) -> str:
    """`018`, `18`, `18.0` y ` 18 ` colapsan al mismo código (igual que Repo Control Center)."""
    t = str(valor if valor is not None else "").strip()
    if t.endswith(".0"):
        t = t[:-2]
    return str(int(t)) if t.isdigit() else t.upper()


def texto(s: pd.Series) -> pd.Series:
    """Quita espacios, el apóstrofo que Forus antepone para forzar texto y el `.0` de ids."""
    out = s.astype("string").str.strip().str.replace(r"^'+", "", regex=True)
    out = out.str.replace(r"^(\d+)\.0$", r"\1", regex=True)
    return out.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "<NA>": pd.NA})


def talla_orden(tallas: pd.Series) -> pd.Series:
    """Orden numérico de la talla: `37`, `37.5`, `37 1/2`, `37½`; sin número → 1000+rank."""
    t = (
        tallas.astype("string")
        .str.replace("½", ".5", regex=False)
        .str.replace(r"\s*1/2", ".5", regex=True)
        .str.replace(",", ".", regex=False)
    )
    num = pd.to_numeric(t.str.extract(r"(\d+(?:\.\d+)?)")[0], errors="coerce")
    orden = {t: i for i, t in enumerate(sorted(set(tallas.astype(str))))}
    resto = tallas.astype(str).map(orden)
    return num.where(num.notna(), 1000 + resto).astype(float)


# ------------------------------------------------------------------ SQL


def _c(mapa: Mapping[str, str], campo: str) -> str:
    return f"`{validar_columna(mapa[campo])}`"


def _t(tabla: str) -> str:
    return f"`{validar_tabla(tabla)}`"


def filtro_marca_arti(col_producto: str, arti: str, mapa_arti: Mapping[str, str]) -> str:
    """Semijoin: sólo productos de las marcas pedidas (evita bajar todo el tablón)."""
    if "marca" not in mapa_arti:
        return ""
    return (
        f"AND CAST({col_producto} AS STRING) IN (SELECT CAST({_c(mapa_arti, 'id_producto')} "
        f"AS STRING) FROM {_t(arti)} WHERE UPPER(TRIM(CAST({_c(mapa_arti, 'marca')} AS STRING))) "
        "IN UNNEST(@marcas))"
    )


def sql_arti(tabla: str, mapa: Mapping[str, str], con_marcas: bool) -> str:
    campos = [
        c
        for c in (
            "modcol",
            "cod_modelo",
            "cod_color",
            "talla",
            "marca",
            "genero",
            "categoria",
            "descripcion",
            "color",
        )
        if c in mapa
    ]
    sel = [f"CAST({_c(mapa, 'id_producto')} AS STRING) AS id_producto"]
    sel += [f"ANY_VALUE(CAST({_c(mapa, c)} AS STRING)) AS {c}" for c in campos]
    if "precio" in mapa:
        sel.append(f"MAX(SAFE_CAST({_c(mapa, 'precio')} AS FLOAT64)) AS precio")
    where = f"{_c(mapa, 'id_producto')} IS NOT NULL"
    if con_marcas and "marca" in mapa:
        where += f" AND UPPER(TRIM(CAST({_c(mapa, 'marca')} AS STRING))) IN UNNEST(@marcas)"
    return f"SELECT {', '.join(sel)}\nFROM {_t(tabla)}\nWHERE {where}\nGROUP BY 1"


def sql_ventas(
    tabla: str, mapa: Mapping[str, str], arti: str, mapa_arti: Mapping[str, str], con_marcas: bool
) -> str:
    f = _c(mapa, "fecha")
    prod = ["id_producto"] if "id_producto" in mapa else ["cod_modelo", "cod_color", "talla"]
    sel = [
        f"DATE_TRUNC(DATE({f}), WEEK(MONDAY)) AS semana_inicio",
        f"CAST({_c(mapa, 'tienda_cod')} AS STRING) AS tienda_cod",
    ]
    sel += [f"CAST({_c(mapa, p)} AS STRING) AS {p}" for p in prod]
    grupos = ", ".join(str(i + 1) for i in range(len(sel)))
    sel.append(f"SUM(SAFE_CAST({_c(mapa, 'unidades')} AS FLOAT64)) AS unidades")
    where = f"DATE({f}) >= @desde AND DATE({f}) < @hasta"
    if con_marcas:
        if "marca" in mapa:
            where += f" AND UPPER(TRIM(CAST({_c(mapa, 'marca')} AS STRING))) IN UNNEST(@marcas)"
        elif "id_producto" in mapa:
            where += " " + filtro_marca_arti(_c(mapa, "id_producto"), arti, mapa_arti)
    return f"SELECT {', '.join(sel)}\nFROM {_t(tabla)}\nWHERE {where}\nGROUP BY {grupos}"


def _stock_expr(mapa: Mapping[str, str]) -> str:
    partes = [
        f"COALESCE(SAFE_CAST({_c(mapa, c)} AS FLOAT64), 0)"
        for c in ("stock_tienda", "stock_bodega")
        if c in mapa
    ]
    return " + ".join(partes)


def sql_cortes(tabla: str, mapa: Mapping[str, str]) -> str:
    """Fechas de corte disponibles en la ventana (sólo lee la columna de fecha)."""
    f = _c(mapa, "fecha")
    return (
        f"SELECT DATE({f}) AS fecha_corte, COUNT(1) AS filas\nFROM {_t(tabla)}\n"
        f"WHERE DATE({f}) >= @desde AND DATE({f}) < @hasta_foto\nGROUP BY 1 ORDER BY 1"
    )


def sql_dias_con_stock(
    tabla: str, mapa: Mapping[str, str], arti: str, mapa_arti: Mapping[str, str], con_marcas: bool
) -> str:
    f = _c(mapa, "fecha")
    idp = _c(mapa, "id_producto")
    where = f"DATE({f}) >= @desde AND DATE({f}) < @hasta AND ({_stock_expr(mapa)}) > 0"
    if con_marcas:
        where += " " + filtro_marca_arti(idp, arti, mapa_arti)
    return (
        f"SELECT DATE_TRUNC(DATE({f}), WEEK(MONDAY)) AS semana_inicio,\n"
        f"       CAST({_c(mapa, 'tienda_cod')} AS STRING) AS tienda_cod,\n"
        f"       CAST({idp} AS STRING) AS id_producto,\n"
        f"       COUNT(DISTINCT DATE({f})) AS cortes_con_stock\n"
        f"FROM {_t(tabla)}\nWHERE {where}\nGROUP BY 1, 2, 3"
    )


def sql_stock_foto(
    tabla: str, mapa: Mapping[str, str], arti: str, mapa_arti: Mapping[str, str], con_marcas: bool
) -> str:
    f = _c(mapa, "fecha")
    idp = _c(mapa, "id_producto")
    sel = [
        f"CAST({_c(mapa, 'tienda_cod')} AS STRING) AS tienda_cod",
        f"CAST({idp} AS STRING) AS id_producto",
    ]
    if "tienda_nombre" in mapa:
        sel.append(f"ANY_VALUE(CAST({_c(mapa, 'tienda_nombre')} AS STRING)) AS tienda_nombre")
    sel.append(f"SUM({_stock_expr(mapa)}) AS stock")
    if "transito" in mapa:
        sel.append(f"SUM(COALESCE(SAFE_CAST({_c(mapa, 'transito')} AS FLOAT64), 0)) AS transito")
    where = f"DATE({f}) = @fecha_foto"
    if con_marcas:
        where += " " + filtro_marca_arti(idp, arti, mapa_arti)
    return f"SELECT {', '.join(sel)}\nFROM {_t(tabla)}\nWHERE {where}\nGROUP BY 1, 2"


# ------------------------------------------------------------------ transformaciones


def a_dim_producto(arti: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    notas: list[str] = []
    d = pd.DataFrame({"sku": texto(arti["id_producto"])})
    if "modcol" in arti and arti["modcol"].notna().any():
        mc = texto(arti["modcol"]).str.upper()
        d["modelo_id"] = mc.str.split("-").str[0]
    else:
        mod, col = texto(arti["cod_modelo"]).str.upper(), texto(arti["cod_color"]).str.upper()
        mc, d["modelo_id"] = mod + "-" + col, mod
    d["modelo_color_id"] = mc
    color = arti["color"] if "color" in arti else arti.get("cod_color")
    d["color"] = texto(color) if color is not None else pd.NA
    d["talla"] = texto(arti["talla"]).str.upper()
    d["categoria"] = (
        texto(arti.get("categoria", pd.Series(pd.NA, index=arti.index)))
        .str.upper()
        .fillna("SIN_CATEGORIA")
    )
    d["genero"] = (
        texto(arti.get("genero", pd.Series(pd.NA, index=arti.index)))
        .str.upper()
        .fillna("SIN_GENERO")
    )
    d = d.dropna(subset=["sku", "modelo_color_id", "talla"])
    if "precio" in arti and arti["precio"].notna().any():
        precio = pd.to_numeric(arti.loc[d.index, "precio"], errors="coerce")
        pmc = precio.groupby(d["modelo_color_id"]).transform("max")
        pct = pmc.groupby(d["categoria"]).rank(pct=True)
        d["rango_precio"] = np.select(
            [pct.isna(), pct <= 1 / 3, pct <= 2 / 3], ["SIN_RANGO", "BAJO", "MEDIO"], "ALTO"
        )
    else:
        d["rango_precio"] = "SIN_RANGO"
        notas.append(
            "ARTI sin precio mapeado: rango_precio = SIN_RANGO (afinidad A2/A3 menos fina)."
        )
    d["talla_orden"] = talla_orden(d["talla"])
    d = d.sort_values(["modelo_color_id", "talla_orden", "sku"], kind="mergesort")
    dup = d.duplicated(["modelo_color_id", "talla"])
    if dup.any():
        notas.append(
            f"{int(dup.sum())} SKU con modelo-color×talla repetido en ARTI: se conservó "
            "el ID Producto menor."
        )
    d = d.loc[~dup & ~d.duplicated("sku")]
    return d.reset_index(drop=True), notas


def _sku_desde_llave(df: pd.DataFrame, dim: pd.DataFrame) -> pd.Series:
    llave = (
        texto(df["cod_modelo"]).str.upper()
        + "-"
        + texto(df["cod_color"]).str.upper()
        + "-"
        + texto(df["talla"]).str.upper()
    )
    mapa = dict(zip(dim["modelo_color_id"] + "-" + dim["talla"], dim["sku"], strict=True))
    return llave.map(mapa)


def dias_por_semana(dias_cs: pd.DataFrame, cortes: pd.DataFrame) -> pd.DataFrame:
    """cortes con stock → días con stock (0..7), escalando por la frecuencia de fotos.

    Con foto diaria, 5 cortes con stock = 5 días. Con foto semanal, 1 corte = 7 días.
    """
    c = cortes.copy()
    c["semana_inicio"] = pd.to_datetime(c["fecha_corte"]) - pd.to_timedelta(
        pd.to_datetime(c["fecha_corte"]).dt.weekday, unit="D"
    )
    por_semana = c.groupby("semana_inicio")["fecha_corte"].nunique().rename("cortes_semana")
    d = dias_cs.copy()
    d["semana_inicio"] = pd.to_datetime(d["semana_inicio"])
    d = d.join(por_semana, on="semana_inicio")
    esc = 7.0 / d["cortes_semana"].where(d["cortes_semana"] > 0, np.nan)
    d["dias_con_stock"] = np.clip(np.round(d["cortes_con_stock"] * esc), 0, 7).fillna(7)
    return d


def leer_stock_cd_archivo(contenido: bytes, nombre: str = "stock_cd.xlsx") -> pd.DataFrame:
    """STOCK CD.xlsx (formato Repo Control Center) → sku, fisico, reservado, comprometido."""
    if nombre.lower().endswith((".csv", ".txt")):
        df = pd.read_csv(io.BytesIO(contenido), dtype=str, sep=None, engine="python")
    else:
        df = pd.read_excel(io.BytesIO(contenido), dtype=str)
    df.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in df.columns]
    if "ID Producto" not in df.columns or "Disponible" not in df.columns:
        raise ValueError(
            "El archivo de stock CD debe traer las columnas 'ID Producto' y "
            "'Disponible' (formato STOCK CD de Forus)."
        )
    num = lambda c: pd.to_numeric(df[c], errors="coerce").fillna(0) if c in df else 0.0  # noqa: E731
    reservas = sum(
        num(c) for c in ("Reserva eCommerce", "Res. Retail", "Res. Wholesale", "Res. Multicanal")
    )
    out = pd.DataFrame(
        {
            "sku": texto(df["ID Producto"]),
            "disponible": num("Disponible").clip(lower=0),
            "reservado": pd.Series(reservas, index=df.index).clip(lower=0),
        }
    )
    out = out.dropna(subset=["sku"]).groupby("sku", as_index=False).sum()
    out["fisico"] = out["disponible"] + out["reservado"]
    out["comprometido"] = 0.0
    return out[["sku", "fisico", "reservado", "comprometido"]]


@dataclass
class Diagnostico:
    notas: list[str] = field(default_factory=list)
    filas: dict[str, int] = field(default_factory=dict)
    gb_leidos: float = 0.0
    fecha_foto: str | None = None
    cortes_en_ventana: int = 0
    mapeos: dict[str, dict[str, str]] = field(default_factory=dict)


def construir_entradas(
    arti: pd.DataFrame,
    ventas: pd.DataFrame,
    dias_cs: pd.DataFrame,
    cortes: pd.DataFrame,
    foto: pd.DataFrame,
    cd_id: str,
    excluidas: set[str],
    stock_cd_archivo: pd.DataFrame | None,
    fecha_corte: pd.Timestamp,
    diag: Diagnostico,
):
    """DataFrames crudos de las consultas → EngineInputs (contratos canónicos)."""
    from forusight.engine.pipeline import EngineInputs

    cd = codigo_tienda(cd_id)
    dim, notas = a_dim_producto(arti)
    diag.notas += notas
    skus = set(dim["sku"])

    # --- ventas semanales
    v = ventas.copy()
    v["sku"] = texto(v["id_producto"]) if "id_producto" in v else _sku_desde_llave(v, dim)
    v["tienda_id"] = v["tienda_cod"].map(codigo_tienda)
    fuera = ~v["sku"].isin(skus)
    if fuera.any():
        diag.notas.append(
            f"{int(fuera.sum())} filas de venta con productos fuera de ARTI/marca: se descartaron."
        )
    v = v.loc[~fuera & ~v["tienda_id"].isin(excluidas | {cd})]
    v = v.groupby(["semana_inicio", "tienda_id", "sku"], as_index=False)["unidades"].sum()
    v["semana_inicio"] = pd.to_datetime(v["semana_inicio"])

    # --- días con stock desde el historial de fotos
    if len(cortes):
        d = dias_por_semana(dias_cs, cortes)
        d["sku"] = texto(d["id_producto"])
        d["tienda_id"] = d["tienda_cod"].map(codigo_tienda)
        d = d.groupby(["semana_inicio", "tienda_id", "sku"], as_index=False)["dias_con_stock"].max()
        semanas_con_foto = set(
            pd.to_datetime(cortes["fecha_corte"]).dt.to_period("W-SUN").dt.start_time
        )
    else:
        d = pd.DataFrame(columns=["semana_inicio", "tienda_id", "sku", "dias_con_stock"])
        semanas_con_foto = set()
        diag.notas.append(
            "La tabla de stock no tiene fotos en la ventana: dias_con_stock se "
            "asume 7 cuando hubo venta (sin historial no se distingue quiebre)."
        )
    sem = v.merge(d, on=["semana_inicio", "tienda_id", "sku"], how="outer")
    sem["unidades"] = sem["unidades"].fillna(0.0)
    sin_foto = ~sem["semana_inicio"].isin(semanas_con_foto)
    # Semana sin foto: no hay información de exposición → se asume expuesta si vendió.
    sem["dias_con_stock"] = sem["dias_con_stock"].fillna(
        pd.Series(np.where(sin_foto & (sem["unidades"] > 0), 7, 0), index=sem.index)
    )
    sem["dias_con_stock"] = sem["dias_con_stock"].astype(int)
    sem = sem.loc[sem["sku"].isin(skus) & ~sem["tienda_id"].isin(excluidas | {cd})]

    # --- última foto: tiendas y CD
    f = foto.copy()
    f["sku"] = texto(f["id_producto"])
    f["tienda_id"] = f["tienda_cod"].map(codigo_tienda)
    f = f.loc[f["sku"].isin(skus)]
    if "transito" not in f:
        f["transito"] = 0.0
        diag.notas.append("La tabla de stock no trae tránsito mapeado: se asume 0 (pendiente e).")
    en_cd = f["tienda_id"].eq(cd)
    tiendas_f = f.loc[~en_cd & ~f["tienda_id"].isin(excluidas)]
    st = tiendas_f.groupby(["tienda_id", "sku"], as_index=False).agg(
        stock_disponible=("stock", "sum"), stock_transito=("transito", "sum")
    )
    st[["stock_disponible", "stock_transito"]] = st[["stock_disponible", "stock_transito"]].clip(
        lower=0
    )

    if stock_cd_archivo is not None:
        stock_cd = stock_cd_archivo.loc[stock_cd_archivo["sku"].isin(skus)].copy()
        diag.notas.append("Stock CD desde el archivo subido (disponible y reservas).")
    else:
        stock_cd = (
            f.loc[en_cd]
            .groupby("sku", as_index=False)["stock"]
            .sum()
            .rename(columns={"stock": "fisico"})
        )
        stock_cd["fisico"] = stock_cd["fisico"].clip(lower=0)
        stock_cd["reservado"] = 0.0
        stock_cd["comprometido"] = 0.0
        diag.notas.append(
            f"Stock CD {cd} desde la foto de la tabla de stock, sin reservas: sube "
            "el archivo STOCK CD para descontar reservas (pendiente d)."
        )
        if stock_cd.empty:
            diag.notas.append(f"La foto de stock no trae filas para la bodega {cd}.")

    # --- dimensión tienda (derivada; clusters/importancia reales: pendiente f)
    nombres = (
        tiendas_f.dropna(subset=["tienda_nombre"]).groupby("tienda_id")["tienda_nombre"].first()
        if "tienda_nombre" in tiendas_f
        else pd.Series(dtype=str)
    )
    ids = sorted(set(st["tienda_id"]) | set(sem["tienda_id"]))
    corte = pd.Timestamp(fecha_corte)
    v12 = sem.loc[sem["semana_inicio"] >= corte - pd.Timedelta(weeks=12)]
    venta_t = v12.groupby("tienda_id")["unidades"].sum().reindex(ids, fill_value=0)
    reciente = set(
        sem.loc[
            (sem["semana_inicio"] >= corte - pd.Timedelta(weeks=4)) & (sem["unidades"] > 0),
            "tienda_id",
        ]
    )
    con_stock = set(st.loc[st["stock_disponible"] > 0, "tienda_id"])
    dim_t = pd.DataFrame(
        {
            "tienda_id": ids,
            "nombre": [nombres.get(t, t) for t in ids],
            "cluster": None,
            "formato": None,
            "importancia_comercial": np.clip(venta_t.rank(pct=True).to_numpy(), 0.05, 1.0)
            if len(ids)
            else [],
            "activa": [t in reciente or t in con_stock for t in ids],
            "max_unidades_corrida": np.nan,
        }
    )
    diag.notas.append(
        "Tiendas sin cluster (pendiente f): la similitud se calcula por coseno del "
        "mix; importancia comercial = percentil de venta 12S."
    )
    diag.filas.update(
        {
            "dim_producto": len(dim),
            "ventas_semanales": len(sem),
            "stock_tienda": len(st),
            "stock_cd": len(stock_cd),
            "tiendas": len(dim_t),
        }
    )
    return EngineInputs(
        ventas=sem[["semana_inicio", "tienda_id", "sku", "unidades", "dias_con_stock"]],
        stock_tienda=st,
        stock_cd=stock_cd[["sku", "fisico", "reservado", "comprometido"]],
        dim_producto=dim,
        dim_tienda=dim_t,
    )


def ventana(fecha_corte: pd.Timestamp, semanas: int) -> dict[str, dt.date]:
    corte = pd.Timestamp(fecha_corte).date()
    return {
        "desde": corte - dt.timedelta(weeks=semanas),
        "hasta": corte,
        "hasta_foto": corte + dt.timedelta(days=7),
    }
