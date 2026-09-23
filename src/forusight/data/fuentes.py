"""Lectura directa de las tablas de Forus → contratos canónicos del motor.

Funciona con los mismos secrets que Catálogo Control Center, sin configurar tablas:

  - ARTI  (`product_master_table` o `table`; por defecto stg_pe_central_arti)
      → dimensión producto. SKU = CODINT_MA, modelo-color = CODMOD_MA-CODCOL_MA.
  - STOCK (`stock_table`; por defecto stg_pe_central_stock_bi, una foto por `fecha_corte`)
      · última foto → stock de cada tienda y del CD 320
      · historial   → días con stock por semana y, si no hay tabla de venta, venta estimada
        por consumo (caída del stock entre fotos consecutivas)
  - VENTA (`ventas_table`, opcional): si está y responde, reemplaza a la venta estimada.
  - STOCK CD.xlsx (opcional, se sube en la app): disponible y reservas del CD.

Reglas de `stg_pe_central_stock_bi` tomadas de Reassign Control Center:
  - en una tienda sólo cuenta `stock_tiendas` (su `stock_bodega` es de otro almacén);
  - en la bodega central (320) el disponible es `stock_tiendas + stock_bodega`;
  - el `id_producto` se canoniza en SQL (sin `.0`, sin ceros a la izquierda) para que los
    cruces con ARTI no fallen por tipo;
  - nunca se suman cortes distintos.

Costo: filtro de fecha parametrizado, agregación en el servidor, semijoin con ARTI por
marca, columnas explícitas y dry run con tope de GB antes de cada consulta.
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

TABLA_ARTI = "forus-analitica-prod-datalake.bronze.stg_pe_central_arti"
TABLA_STOCK = "forus-analitica-prod-datalake.bronze.stg_pe_central_stock_bi"
TABLAS_POR_DEFECTO = {"arti": TABLA_ARTI, "stock": TABLA_STOCK}

#: Esquemas conocidos (Catálogo / Reassign Control Center). Sólo se usan si
#: INFORMATION_SCHEMA no responde (p. ej. la cuenta no tiene permiso de metadatos).
COLUMNAS_CONOCIDAS = {
    TABLA_STOCK: [
        "fecha_corte",
        "id_producto",
        "conca",
        "talla",
        "codigo_tienda",
        "CONCAT_TIENDA",
        "stock_tiendas",
        "stock_bodega",
    ],
    TABLA_ARTI: [
        "CODINT_MA",
        "CODMOD_MA",
        "CODCOL_MA",
        "TALNUM_MA",
        "CODBAR_MA",
        "MARCA_MA",
        "GENERO_MA",
        "TIPO_MA",
        "DESCRIPCION_MA",
        "COLOR_MA",
    ],
}

VENTA_TABLA = "tabla de venta"
VENTA_CONSUMO = "consumo de stock (estimada)"


# ------------------------------------------------------------------ normalización


def codigo_tienda(valor: Any) -> str:
    """`018`, `18`, `18.0` y ` 18 ` colapsan al mismo código."""
    t = str(valor if valor is not None and not pd.isna(valor) else "").strip()
    if t.endswith(".0"):
        t = t[:-2]
    return str(int(t)) if t.isdigit() else t.upper()


def texto(s: pd.Series) -> pd.Series:
    """Quita espacios, el apóstrofo que Forus antepone para forzar texto y el `.0`."""
    out = s.astype("string").str.strip().str.replace(r"^'+", "", regex=True)
    out = out.str.replace(r"^(\d+)\.0+$", r"\1", regex=True)
    return out.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "<NA>": pd.NA})


def sku_canonico(s: pd.Series) -> pd.Series:
    """Igual que `sku_sql`: mayúsculas, sin `.0`, sin ceros a la izquierda si es numérico."""
    t = texto(s).str.upper()
    return t.str.replace(r"^0+(\d)", r"\1", regex=True)


def talla_orden(tallas: pd.Series) -> pd.Series:
    """Orden numérico de la talla: `37`, `37.5`, `37 1/2`, `37½`; sin número → 1000+rank."""
    t = (
        tallas.astype("string")
        .str.replace("½", ".5", regex=False)
        .str.replace(r"\s*1/2", ".5", regex=True)
        .str.replace(",", ".", regex=False)
    )
    num = pd.to_numeric(t.str.extract(r"(\d+(?:\.\d+)?)")[0], errors="coerce")
    orden = {v: i for i, v in enumerate(sorted(set(tallas.astype(str))))}
    return num.where(num.notna(), 1000 + tallas.astype(str).map(orden)).astype(float)


# ------------------------------------------------------------------ SQL


def _c(mapa: Mapping[str, str], campo: str) -> str:
    return f"`{validar_columna(mapa[campo])}`"


def _t(tabla: str) -> str:
    return f"`{validar_tabla(tabla)}`"


def sku_sql(columna: str) -> str:
    """SKU canónico en BigQuery (mismo criterio que Reassign Control Center)."""
    limpio = f"REGEXP_REPLACE(UPPER(TRIM(CAST({columna} AS STRING))), r'[.]0+$', '')"
    return (
        f"IF(REGEXP_CONTAINS({limpio}, r'^[0-9]+$'), "
        f"IFNULL(REGEXP_EXTRACT({limpio}, r'^0*([0-9]+?)$'), {limpio}), {limpio})"
    )


def tienda_sql(mapa: Mapping[str, str]) -> str:
    """Código de tienda; si viene vacío se toma del sufijo de CONCAT_TIENDA (`3-151`)."""
    cod = f"NULLIF(TRIM(CAST({_c(mapa, 'tienda_cod')} AS STRING)), '')"
    if "tienda_nombre" in mapa:
        return (
            f"COALESCE({cod}, REGEXP_EXTRACT(CAST({_c(mapa, 'tienda_nombre')} AS STRING), "
            r"r'-\s*([0-9]+)\s*$'))"
        )
    return cod


def _marca_sql(mapa: Mapping[str, str]) -> str:
    return f"UPPER(TRIM(CAST({_c(mapa, 'marca')} AS STRING)))"


def filtro_marca_arti(col_producto: str, arti: str, mapa_arti: Mapping[str, str]) -> str:
    """Semijoin: sólo productos de las marcas pedidas (evita bajar todo el tablón)."""
    if "marca" not in mapa_arti:
        return ""
    return (
        f"AND {sku_sql(col_producto)} IN (SELECT {sku_sql(_c(mapa_arti, 'id_producto'))} "
        f"FROM {_t(arti)} WHERE {_marca_sql(mapa_arti)} IN UNNEST(@marcas))"
    )


def sql_marcas(tabla: str, mapa: Mapping[str, str]) -> str:
    """Marcas del maestro con su cantidad de SKU (para elegir en pantalla)."""
    return (
        f"SELECT {_marca_sql(mapa)} AS marca, COUNT(DISTINCT {_c(mapa, 'id_producto')}) AS skus"
        f"\nFROM {_t(tabla)}\nWHERE {_c(mapa, 'marca')} IS NOT NULL\n"
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 300"
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
    sel = [f"{sku_sql(_c(mapa, 'id_producto'))} AS id_producto"]
    sel += [f"ANY_VALUE(CAST({_c(mapa, c)} AS STRING)) AS {c}" for c in campos]
    if "precio" in mapa:
        sel.append(f"MAX(SAFE_CAST({_c(mapa, 'precio')} AS FLOAT64)) AS precio")
    where = f"{_c(mapa, 'id_producto')} IS NOT NULL"
    if con_marcas and "marca" in mapa:
        where += f" AND {_marca_sql(mapa)} IN UNNEST(@marcas)"
    return f"SELECT {', '.join(sel)}\nFROM {_t(tabla)}\nWHERE {where}\nGROUP BY 1"


def sql_ventas(
    tabla: str, mapa: Mapping[str, str], arti: str, mapa_arti: Mapping[str, str], con_marcas: bool
) -> str:
    f = _c(mapa, "fecha")
    sel = [
        f"DATE_TRUNC(DATE({f}), WEEK(MONDAY)) AS semana_inicio",
        f"CAST({_c(mapa, 'tienda_cod')} AS STRING) AS tienda_cod",
    ]
    if "id_producto" in mapa:
        sel.append(f"{sku_sql(_c(mapa, 'id_producto'))} AS id_producto")
    else:
        sel += [
            f"CAST({_c(mapa, p)} AS STRING) AS {p}" for p in ("cod_modelo", "cod_color", "talla")
        ]
    grupos = ", ".join(str(i + 1) for i in range(len(sel)))
    sel.append(f"SUM(SAFE_CAST({_c(mapa, 'unidades')} AS FLOAT64)) AS unidades")
    where = f"DATE({f}) >= @desde AND DATE({f}) < @hasta"
    if con_marcas:
        if "marca" in mapa:
            where += f" AND {_marca_sql(mapa)} IN UNNEST(@marcas)"
        elif "id_producto" in mapa:
            where += " " + filtro_marca_arti(_c(mapa, "id_producto"), arti, mapa_arti)
    return f"SELECT {', '.join(sel)}\nFROM {_t(tabla)}\nWHERE {where}\nGROUP BY {grupos}"


def sql_cortes(tabla: str, mapa: Mapping[str, str]) -> str:
    """Fechas de corte disponibles en la ventana (sólo lee la columna de fecha)."""
    f = _c(mapa, "fecha")
    return (
        f"SELECT DATE({f}) AS fecha_corte, COUNT(1) AS filas\nFROM {_t(tabla)}\n"
        f"WHERE DATE({f}) >= @desde AND DATE({f}) < @hasta_foto\nGROUP BY 1 ORDER BY 1"
    )


def sql_historial(
    tabla: str, mapa: Mapping[str, str], arti: str, mapa_arti: Mapping[str, str], con_marcas: bool
) -> str:
    """Una sola lectura del historial → por semana×tienda×SKU:

    - ``cortes_con_stock``: fotos con stock en sala (> 0), para los días con stock;
    - ``consumo``: caída del stock entre una foto y la siguiente (si el SKU no aparece en
      la foto siguiente, llegó a 0). Es la venta estimada cuando no hay tabla de venta.
    ``@fechas`` son las fechas de corte de la ventana, en orden.
    """
    f = _c(mapa, "fecha")
    idp = sku_sql(_c(mapa, "id_producto"))
    where = f"DATE({f}) >= @desde AND DATE({f}) < @hasta"
    if con_marcas:
        where += " " + filtro_marca_arti(_c(mapa, "id_producto"), arti, mapa_arti)
    q = f"COALESCE(SAFE_CAST({_c(mapa, 'stock_tienda')} AS FLOAT64), 0)"
    return f"""WITH fechas AS (
  SELECT fecha, LEAD(fecha) OVER (ORDER BY fecha) AS siguiente
  FROM UNNEST(@fechas) AS fecha
),
base AS (
  SELECT DATE({f}) AS fecha, {tienda_sql(mapa)} AS tienda_cod, {idp} AS id_producto,
         SUM({q}) AS q
  FROM {_t(tabla)}
  WHERE {where}
  GROUP BY 1, 2, 3
),
serie AS (
  SELECT b.*, fe.siguiente,
         LEAD(b.fecha) OVER w AS fecha_sig, LEAD(b.q) OVER w AS q_sig
  FROM base AS b JOIN fechas AS fe USING (fecha)
  WINDOW w AS (PARTITION BY b.tienda_cod, b.id_producto ORDER BY b.fecha)
)
SELECT DATE_TRUNC(fecha, WEEK(MONDAY)) AS semana_inicio, tienda_cod, id_producto,
       COUNTIF(q > 0) AS cortes_con_stock,
       SUM(IF(siguiente IS NULL, 0,
              GREATEST(q - IF(fecha_sig = siguiente, q_sig, 0), 0))) AS consumo
FROM serie
GROUP BY 1, 2, 3"""


def sql_stock_foto(
    tabla: str, mapa: Mapping[str, str], arti: str, mapa_arti: Mapping[str, str], con_marcas: bool
) -> str:
    f = _c(mapa, "fecha")
    sel = [
        f"{tienda_sql(mapa)} AS tienda_cod",
        f"{sku_sql(_c(mapa, 'id_producto'))} AS id_producto",
    ]
    if "tienda_nombre" in mapa:
        sel.append(f"ANY_VALUE(CAST({_c(mapa, 'tienda_nombre')} AS STRING)) AS tienda_nombre")
    for campo in ("stock_tienda", "stock_bodega", "transito"):
        if campo in mapa:
            sel.append(f"SUM(COALESCE(SAFE_CAST({_c(mapa, campo)} AS FLOAT64), 0)) AS {campo}")
    where = f"DATE({f}) = @fecha_foto"
    if con_marcas:
        where += " " + filtro_marca_arti(_c(mapa, "id_producto"), arti, mapa_arti)
    return f"SELECT {', '.join(sel)}\nFROM {_t(tabla)}\nWHERE {where}\nGROUP BY 1, 2"


# ------------------------------------------------------------------ transformaciones


def a_dim_producto(arti: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    notas: list[str] = []
    vacio = pd.Series(pd.NA, index=arti.index)
    d = pd.DataFrame({"sku": sku_canonico(arti["id_producto"])})
    if "modcol" in arti and arti["modcol"].notna().any():
        mc = texto(arti["modcol"]).str.upper()
        d["modelo_id"] = mc.str.split("-").str[0]
    else:
        mod, col = texto(arti["cod_modelo"]).str.upper(), texto(arti["cod_color"]).str.upper()
        mc, d["modelo_id"] = mod + "-" + col, mod
    d["modelo_color_id"] = mc
    d["color"] = texto(arti.get("color", arti.get("cod_color", vacio)))
    d["talla"] = texto(arti["talla"]).str.upper()
    d["categoria"] = texto(arti.get("categoria", vacio)).str.upper().fillna("SIN_CATEGORIA")
    d["genero"] = texto(arti.get("genero", vacio)).str.upper().fillna("SIN_GENERO")
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
        notas.append("ARTI no trae precio: la afinidad no usa rango de precio.")
    d["talla_orden"] = talla_orden(d["talla"])
    d = d.sort_values(["modelo_color_id", "talla_orden", "sku"], kind="mergesort")
    dup = d.duplicated(["modelo_color_id", "talla"])
    if dup.any():
        notas.append(
            f"{int(dup.sum())} SKU con modelo-color×talla repetido en ARTI: se conservó el primero."
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


def _lunes(fechas: pd.Series) -> pd.Series:
    f = pd.to_datetime(fechas)
    return f - pd.to_timedelta(f.dt.weekday, unit="D")


def dias_por_semana(hist: pd.DataFrame, cortes: pd.DataFrame) -> pd.DataFrame:
    """Fotos con stock → días con stock (0..7), escalando por la frecuencia de fotos.

    Con foto diaria, 5 fotos con stock = 5 días. Con foto semanal, 1 foto = 7 días.
    """
    por_semana = (
        cortes.assign(semana_inicio=_lunes(cortes["fecha_corte"]))
        .groupby("semana_inicio")["fecha_corte"]
        .nunique()
        .rename("cortes_semana")
    )
    d = hist.copy()
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

    def num(c: str):
        return pd.to_numeric(df[c], errors="coerce").fillna(0) if c in df else 0.0

    reservas = sum(
        num(c) for c in ("Reserva eCommerce", "Res. Retail", "Res. Wholesale", "Res. Multicanal")
    )
    out = pd.DataFrame(
        {
            "sku": sku_canonico(df["ID Producto"]),
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
    fuente_venta: str = VENTA_CONSUMO
    marcas: list[str] = field(default_factory=list)
    tablas: dict[str, str] = field(default_factory=dict)
    mapeos: dict[str, dict[str, str]] = field(default_factory=dict)


def construir_entradas(
    arti: pd.DataFrame,
    ventas: pd.DataFrame | None,
    hist: pd.DataFrame,
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
    no_reciben = excluidas | {cd}
    dim, notas = a_dim_producto(arti)
    diag.notas += notas
    skus = set(dim["sku"])

    h = hist.copy()
    h["sku"] = sku_canonico(h["id_producto"])
    h["tienda_id"] = h["tienda_cod"].map(codigo_tienda)
    h = h.loc[h["sku"].isin(skus) & ~h["tienda_id"].isin(no_reciben) & h["tienda_id"].ne("")]
    llave = ["semana_inicio", "tienda_id", "sku"]

    # --- venta semanal: tabla de venta o consumo de stock
    if ventas is not None:
        v = ventas.copy()
        v["sku"] = (
            sku_canonico(v["id_producto"]) if "id_producto" in v else _sku_desde_llave(v, dim)
        )
        v["tienda_id"] = v["tienda_cod"].map(codigo_tienda)
        v = v.loc[v["sku"].isin(skus) & ~v["tienda_id"].isin(no_reciben)]
        diag.fuente_venta = VENTA_TABLA
    else:
        v = h.rename(columns={"consumo": "unidades"})
        diag.fuente_venta = VENTA_CONSUMO
        diag.notas.append(
            "Venta estimada por consumo de stock (caída entre fotos de "
            "stg_pe_central_stock_bi): incluye traspasos de salida y no ve la venta "
            "del mismo día de una reposición. Configura `ventas_table` para usar "
            "la venta real."
        )
    v["semana_inicio"] = pd.to_datetime(v["semana_inicio"])
    v = v.groupby(llave, as_index=False)["unidades"].sum()

    # --- días con stock desde el historial de fotos
    d = dias_por_semana(h, cortes).groupby(llave, as_index=False)["dias_con_stock"].max()
    sem = v.merge(d, on=llave, how="outer")
    sem["unidades"] = sem["unidades"].fillna(0.0).clip(lower=0)
    semanas_con_foto = set(_lunes(cortes["fecha_corte"]))
    sin_foto = ~sem["semana_inicio"].isin(semanas_con_foto)
    # Semana sin foto: no hay información de exposición → se asume expuesta si vendió.
    sem["dias_con_stock"] = sem["dias_con_stock"].fillna(
        pd.Series(np.where(sin_foto & (sem["unidades"] > 0), 7, 0), index=sem.index)
    )
    sem["dias_con_stock"] = sem["dias_con_stock"].astype(int)
    sem = sem.loc[(sem["unidades"] > 0) | (sem["dias_con_stock"] > 0)]

    # --- última foto: tiendas (sólo stock en sala) y CD (sala + bodega)
    f = foto.copy()
    for c in ("stock_tienda", "stock_bodega", "transito"):
        if c not in f:
            f[c] = 0.0
    f["sku"] = sku_canonico(f["id_producto"])
    f["tienda_id"] = f["tienda_cod"].map(codigo_tienda)
    f = f.loc[f["sku"].isin(skus) & f["tienda_id"].ne("")]
    en_cd = f["tienda_id"].eq(cd)
    tiendas_f = f.loc[~en_cd & ~f["tienda_id"].isin(excluidas)]
    st = tiendas_f.groupby(["tienda_id", "sku"], as_index=False).agg(
        stock_disponible=("stock_tienda", "sum"), stock_transito=("transito", "sum")
    )
    st[["stock_disponible", "stock_transito"]] = st[["stock_disponible", "stock_transito"]].clip(
        lower=0
    )

    if stock_cd_archivo is not None:
        stock_cd = stock_cd_archivo.loc[stock_cd_archivo["sku"].isin(skus)].copy()
        diag.notas.append("Stock CD desde el archivo subido (disponible y reservas).")
    else:
        g = f.loc[en_cd].groupby("sku", as_index=False)[["stock_tienda", "stock_bodega"]].sum()
        stock_cd = pd.DataFrame(
            {
                "sku": g["sku"],
                "fisico": (g["stock_tienda"] + g["stock_bodega"]).clip(lower=0),
                "reservado": 0.0,
                "comprometido": 0.0,
            }
        )
        diag.notas.append(
            f"Stock CD {cd} = stock_tiendas + stock_bodega de la última foto, sin "
            "reservas: sube el archivo STOCK CD para descontarlas."
        )
        if stock_cd.empty:
            diag.notas.append(
                f"La foto de stock no trae filas de la bodega {cd} para estas marcas."
            )

    # --- dimensión tienda (derivada; clusters reales: pendiente)
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
            "nombre": [str(nombres.get(t, t)) for t in ids],
            "cluster": None,
            "formato": None,
            "importancia_comercial": (
                np.clip(venta_t.rank(pct=True).to_numpy(), 0.05, 1.0) if len(ids) else []
            ),
            "activa": [t in reciente or t in con_stock for t in ids],
            "max_unidades_corrida": np.nan,
        }
    )
    diag.filas.update(
        {
            "productos": len(dim),
            "semanas_tienda_sku": len(sem),
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
