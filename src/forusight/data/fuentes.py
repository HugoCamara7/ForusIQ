"""Lectura directa de las tablas de Forus → contratos canónicos del motor.

Con los secrets de Catálogo Control Center:
  - ARTI    (`table`; por defecto stg_pe_central_arti) → dimensión producto.
  - STOCK   (`stock_table`; por defecto stg_pe_central_stock_bi). Sólo trae el ÚLTIMO corte
            del día (no hay historial): se usa la foto más reciente, tiendas y CD 320.
  - VENTA   (`ventas_table`, obligatoria): 12 semanas cerradas + la semana en curso.
  - MAESTROS (opcionales): `maestro_tiendas_table` (tienda → nombre) y
            `maestro_cadena_table` (modelo → cadena, limita las introducciones).
  - STOCK CD.xlsx (opcional, se sube en la app): disponible y reservas del CD.

Sin historial de stock, la exposición semanal se INFIERE de la venta real y del stock actual:
  - vendió y hoy tiene stock      → expuesto desde la primera venta hasta hoy;
  - vendió y hoy está en 0        → expuesto entre la primera y la última venta (después,
                                    quiebre: esas semanas no cuentan como "sin demanda");
  - no vendió y hoy tiene stock   → expuesto toda la ventana (falta de venta, no de stock);
  - no vendió y no tiene stock    → sin exposición (nunca tuvo).

Reglas de `stg_pe_central_stock_bi` (Reassign Control Center): en tienda cuenta sólo
`stock_tiendas`; en el CD 320 `stock_tiendas + stock_bodega`; `id_producto` canonizado.
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
#: Venta retail (tabla de hechos). Columnas informadas por Forus.
TABLA_VENTA_RETAIL = "forus-analitica-prod-datalake.silver.ft_pe_venta_retail"

COLUMNAS_CONOCIDAS = {
    TABLA_VENTA_RETAIL: [
        "origen_data",
        "id_canal_bi",
        "tipo_venta",
        "nlocal_lv",
        "nombre_tienda",
        "cadena_sitio",
        "tipdoc_lv",
        "nrodoc_lv",
        "terminal_lv",
        "transac_lv",
        "fecmov_lv",
        "nroped_lv",
        "rutcli_lv",
        "nombre_cliente",
        "region",
        "comuna",
        "id_sitio",
        "oc_cliente",
        "numero_sg",
        "codpro_df",
        "codean_df",
        "marca",
        "clase",
        "genero",
        "modelo",
        "color",
        "cod_modelo",
        "cod_color",
        "talla",
        "hora_pago",
        "unidades_venta",
        "venta_total_sin_iva",
        "venta_total_con_iva",
        "shipping",
        "monto_cupon_descuento",
        "costo",
    ],
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
    """Igual que `sku_sql`: mayúsculas, sin `.0` final, sin ceros a la izquierda si es numérico."""
    t = texto(s).str.upper().str.replace(r"[.]0+$", "", regex=True)
    numerico = t.str.fullmatch(r"\d+").fillna(False).astype(bool)
    return t.where(~numerico, t.str.replace(r"^0+(\d)", r"\1", regex=True))


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
            "prenda",
            "temporada",
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
    """Venta semanal por tienda×SKU: semanas cerradas y la semana en curso (hasta hoy)."""
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
    sel.append(f"MAX(DATE({f})) AS ultima_venta")
    where = f"DATE({f}) >= @desde AND DATE({f}) < @hasta_foto"
    if con_marcas:
        # La marca se toma de ARTI (igual que el stock): el texto de marca de la venta puede
        # cambiar y dejar semanas recientes fuera sin avisar.
        if "id_producto" in mapa:
            where += " " + filtro_marca_arti(_c(mapa, "id_producto"), arti, mapa_arti)
        elif "marca" in mapa:
            where += f" AND {_marca_sql(mapa)} IN UNNEST(@marcas)"
    return f"SELECT {', '.join(sel)}\nFROM {_t(tabla)}\nWHERE {where}\nGROUP BY {grupos}"


def sql_revisar_venta(
    tabla: str, mapa: Mapping[str, str], arti: str, mapa_arti: Mapping[str, str]
) -> tuple[str, str]:
    """(resumen, muestra) para saber si la venta está atrasada o si cambió el código.

    resumen: última fecha de TODA la tabla y de la marca (vía ARTI) en los últimos 180 días.
    muestra: filas posteriores a la última venta de la marca (si existen, la tabla sí está al
    día y lo que cambió es el código de producto o de tienda).
    """
    f = _c(mapa, "fecha")
    marca = filtro_marca_arti(_c(mapa, "id_producto"), arti, mapa_arti).removeprefix("AND ")
    marca = marca or "TRUE"
    resumen = (
        f"SELECT MAX(DATE({f})) AS ultima_tabla,\n"
        f"  MAX(IF({marca}, DATE({f}), NULL)) AS ultima_marca,\n"
        f"  COUNTIF(DATE({f}) >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)) AS filas_14_dias\n"
        f"FROM {_t(tabla)}\nWHERE DATE({f}) >= DATE_SUB(CURRENT_DATE(), INTERVAL 180 DAY)"
    )
    cols = [f"DATE({f}) AS fecha", f"CAST({_c(mapa, 'tienda_cod')} AS STRING) AS tienda"]
    cols.append(f"CAST({_c(mapa, 'id_producto')} AS STRING) AS producto")
    if "marca" in mapa:
        cols.append(f"CAST({_c(mapa, 'marca')} AS STRING) AS marca_venta")
    cols.append(f"SAFE_CAST({_c(mapa, 'unidades')} AS FLOAT64) AS unidades")
    muestra = (
        f"SELECT {', '.join(cols)}\nFROM {_t(tabla)}\n"
        f"WHERE DATE({f}) > @ultima_marca ORDER BY 1 DESC LIMIT 20"
    )
    return resumen, muestra


def sql_cortes(tabla: str, mapa: Mapping[str, str]) -> str:
    """Fechas de corte del último año (sólo lee la columna de fecha).

    La tabla trae el cierre del día anterior de este año y el del mismo día del año pasado:
    se usa la fecha más reciente (este año) y la del año pasado se descarta.
    """
    f = _c(mapa, "fecha")
    return (
        f"SELECT DATE({f}) AS fecha_corte, COUNT(1) AS filas\nFROM {_t(tabla)}\n"
        f"WHERE DATE({f}) >= @desde_foto AND DATE({f}) < @hasta_foto\nGROUP BY 1 ORDER BY 1"
    )


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


def sql_maestro(tabla: str, mapa: Mapping[str, str]) -> str:
    """Maestro chico (tiendas / modelo→cadena): sólo las columnas mapeadas, sin duplicados."""
    sel = [f"CAST({_c(mapa, c)} AS STRING) AS {c}" for c in mapa]
    return f"SELECT DISTINCT {', '.join(sel)}\nFROM {_t(tabla)}"


# ------------------------------------------------------------------ transformaciones


def a_dim_producto(arti: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    notas: list[str] = []
    vacio = pd.Series(pd.NA, index=arti.index)
    d = pd.DataFrame({"sku": sku_canonico(arti["id_producto"])})
    if "modcol" in arti and arti["modcol"].notna().any():
        mc = texto(arti["modcol"]).str.upper()
        d["modelo_id"] = mc.str.split("-").str[0]
        d["cod_color"] = mc.str.split("-").str[1]
    else:
        mod, col = texto(arti["cod_modelo"]).str.upper(), texto(arti["cod_color"]).str.upper()
        mc, d["modelo_id"], d["cod_color"] = mod + "-" + col, mod, col
    d["modelo_color_id"] = mc
    d["color"] = texto(arti.get("color", arti.get("cod_color", vacio)))
    d["talla"] = texto(arti["talla"]).str.upper()
    d["categoria"] = texto(arti.get("categoria", vacio)).str.upper().fillna("SIN_CATEGORIA")
    d["genero"] = texto(arti.get("genero", vacio)).str.upper().fillna("SIN_GENERO")
    # Atributos sólo para el archivo de salida (se omiten si ARTI no los trae).
    for c in ("marca", "descripcion", "prenda", "temporada"):
        d[c] = texto(arti.get(c, vacio)).str.upper()
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


def inferir_exposicion(
    ventas: pd.DataFrame,
    stock: pd.DataFrame,
    semanas: list,
    mc_de_sku: pd.Series | None = None,
) -> pd.DataFrame:
    """Días con stock por semana SIN historial de stock (ver docstring del módulo).

    ``ventas``: semana_inicio, tienda_id, sku, unidades (semanas cerradas).
    ``stock``: tienda_id, sku, stock_disponible (foto actual). ``semanas``: lunes cerrados.
    ``mc_de_sku``: sku → modelo-color. Si viene, una talla con stock que todavía no vendió se
    considera expuesta desde la primera venta del modelo en la tienda (un modelo nuevo se
    mide desde que llegó, no las 12 semanas).
    Devuelve semana_inicio, tienda_id, sku, unidades, dias_con_stock (0 o 7).
    """
    llave = ["tienda_id", "sku"]
    vv = ventas.loc[ventas["unidades"] > 0]
    rango = vv.groupby(llave).agg(primera=("semana_inicio", "min"), ultima=("semana_inicio", "max"))
    st = stock.set_index(llave)["stock_disponible"]
    pares = pd.DataFrame(index=rango.index.union(st.index[st > 0])).reset_index()
    pares = pares.join(rango, on=llave).join(st.rename("stock"), on=llave)
    pares["stock"] = pares["stock"].fillna(0)
    pares["primera_mc"] = pd.NaT
    if mc_de_sku is not None and len(vv):
        mc = mc_de_sku[~mc_de_sku.index.duplicated()]
        v_mc = vv.assign(_mc=vv["sku"].map(mc)).dropna(subset=["_mc"])
        primera_mc = v_mc.groupby(["tienda_id", "_mc"])["semana_inicio"].min()
        claves = pd.MultiIndex.from_arrays([pares["tienda_id"], pares["sku"].map(mc)])
        pares["primera_mc"] = primera_mc.reindex(claves).to_numpy()
    grid = pares.merge(pd.DataFrame({"semana_inicio": pd.to_datetime(semanas)}), how="cross")
    tiene = grid["stock"] > 0
    vendio = grid["primera"].notna()
    desde_mc = grid["primera_mc"].isna() | (grid["semana_inicio"] >= grid["primera_mc"])
    expuesta = np.select(
        [vendio & tiene, vendio & ~tiene, ~vendio & tiene],
        [
            grid["semana_inicio"] >= grid["primera"],
            (grid["semana_inicio"] >= grid["primera"]) & (grid["semana_inicio"] <= grid["ultima"]),
            desde_mc,
        ],
        False,
    )
    grid["dias_con_stock"] = np.where(expuesta, 7, 0)
    out = grid[llave + ["semana_inicio", "dias_con_stock"]].merge(
        ventas[llave + ["semana_inicio", "unidades"]], on=llave + ["semana_inicio"], how="outer"
    )
    out["unidades"] = out["unidades"].fillna(0.0)
    out["dias_con_stock"] = out["dias_con_stock"].fillna(7).astype(int)  # vendió → expuesta
    out.loc[out["unidades"] > 0, "dias_con_stock"] = 7
    return out.loc[(out["unidades"] > 0) | (out["dias_con_stock"] > 0)]


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
    fuente_venta: str = VENTA_TABLA
    marcas: list[str] = field(default_factory=list)
    tablas: dict[str, str] = field(default_factory=dict)
    mapeos: dict[str, dict[str, str]] = field(default_factory=dict)
    tiendas: list = field(default_factory=list)
    regla_introduccion: str = ""
    venta_hasta: str | None = None
    corte_venta: str | None = None


def construir_entradas(
    arti: pd.DataFrame,
    ventas: pd.DataFrame,
    foto: pd.DataFrame,
    cd_id: str,
    excluidas: set[str],
    stock_cd_archivo: pd.DataFrame | None,
    fecha_corte: pd.Timestamp,
    diag: Diagnostico,
    semanas: int = 13,
    tiendas_m: pd.DataFrame | None = None,
    cadena_m: pd.DataFrame | None = None,
    marcas_por_cadena: dict | None = None,
):
    """DataFrames crudos de las consultas → EngineInputs (contratos canónicos)."""
    from forusight.engine.pipeline import EngineInputs

    cd = codigo_tienda(cd_id)
    no_reciben = excluidas | {cd}
    dim, notas = a_dim_producto(arti)
    diag.notas += notas
    skus = set(dim["sku"])
    corte = pd.Timestamp(fecha_corte).normalize()
    lunes = [corte - pd.Timedelta(weeks=k) for k in range(semanas, 0, -1)]

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

    # --- venta real semanal (cerradas + semana en curso)
    v = ventas.copy()
    v["sku"] = sku_canonico(v["id_producto"]) if "id_producto" in v else _sku_desde_llave(v, dim)
    v["tienda_id"] = v["tienda_cod"].map(codigo_tienda)
    v = v.loc[v["sku"].isin(skus) & ~v["tienda_id"].isin(no_reciben)]
    v["semana_inicio"] = pd.to_datetime(v["semana_inicio"])
    v = v.groupby(["semana_inicio", "tienda_id", "sku"], as_index=False)["unidades"].sum()
    v["unidades"] = v["unidades"].clip(lower=0)
    en_curso = v.loc[v["semana_inicio"] >= corte]
    cerradas = v.loc[v["semana_inicio"] < corte]
    sem = inferir_exposicion(cerradas, st, lunes, dim.set_index("sku")["modelo_color_id"])
    # la semana en curso viaja aparte del análisis (Demanda Periodo Actual en el archivo)
    en_curso = en_curso.assign(dias_con_stock=0)
    diag.notas.append(
        "Stock sin historial (sólo el último corte): la exposición semanal se "
        "infiere de la venta real y del stock actual."
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
                # componentes, para elegir qué parte del CD se reparte (Parámetros → stock_cd)
                "cd_stock_tiendas": g["stock_tienda"].clip(lower=0),
                "cd_stock_bodega": g["stock_bodega"].clip(lower=0),
            }
        )
        if stock_cd.empty:
            diag.notas.append(
                f"La foto de stock no trae filas de la bodega {cd} para estas marcas."
            )

    # --- dimensión tienda: maestro de tiendas si está; si no, la foto de stock
    ids = sorted(set(st["tienda_id"]) | set(sem["tienda_id"]))
    nombres = (
        tiendas_f.dropna(subset=["tienda_nombre"]).groupby("tienda_id")["tienda_nombre"].first()
        if "tienda_nombre" in tiendas_f
        else pd.Series(dtype=str)
    )
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
    # Tienda → nombre / cadena: maestro de BigQuery y, si falta, catálogo Forus.
    from forusight.data import cadenas as CAD

    cat = CAD.catalogo_tiendas().rename(
        columns={"codigo_tienda": "tienda_cod", "nombre_tienda": "tienda_nombre"}
    )
    fuentes_t = []
    if tiendas_m is not None and len(tiendas_m):
        fuentes_t.append(("maestro", tiendas_m))
    fuentes_t.append(("catálogo Forus", cat))
    dim_t["origen_tienda"] = pd.NA
    for c in ("centro_comercial", "zona", "cadena"):
        dim_t[c] = pd.NA
    for origen, tabla in fuentes_t:
        m = tabla.copy()
        m["tienda_id"] = m["tienda_cod"].map(codigo_tienda)
        m = m.drop_duplicates("tienda_id").set_index("tienda_id")
        falta = dim_t["origen_tienda"].isna() & dim_t["tienda_id"].isin(m.index)
        dim_t.loc[falta, "nombre"] = dim_t.loc[falta, "tienda_id"].map(m["tienda_nombre"])
        for c in ("centro_comercial", "zona", "cadena"):
            if c in m:
                dim_t.loc[falta, c] = dim_t.loc[falta, "tienda_id"].map(m[c])
        dim_t.loc[falta, "origen_tienda"] = origen
    identificada = dim_t["origen_tienda"].notna()
    dim_t["cadena"] = dim_t["cadena"].astype("string").str.strip().str.upper()
    dim_t["cadena"] = dim_t["cadena"].where(
        dim_t["cadena"].notna() & dim_t["cadena"].ne(""), CAD.prefijo(dim_t["nombre"])
    )
    dim_t.loc[~identificada, "cadena"] = pd.NA
    # Sólo reciben tiendas identificadas: bodegas eComm u otros códigos quedan fuera.
    sin_id = dim_t.loc[~identificada, "tienda_id"].tolist()
    if sin_id:
        dim_t.loc[~identificada, "activa"] = False
        diag.notas.append(
            f"{len(sin_id)} códigos de tienda sin maestro ni catálogo no reciben "
            f"(bodegas u otros): {', '.join(sin_id[:15])}" + ("…" if len(sin_id) > 15 else "")
        )

    # --- qué se puede INTRODUCIR en cada tienda (la reposición no se restringe)
    matriz = CAD.marcas_por_cadena(marcas_por_cadena)
    cm = None
    if cadena_m is not None and len(cadena_m):
        cm = (
            pd.DataFrame(
                {
                    "modelo_id": texto(cadena_m["cod_modelo"]).str.upper(),
                    "cadena": texto(cadena_m["cadena"]).str.upper(),
                }
            )
            .dropna()
            .drop_duplicates()
        )
    permitidos, regla = CAD.pares_permitidos(dim_t, dim, cm, matriz)
    diag.regla_introduccion = regla
    sin_matriz = sorted(set(dim_t.loc[identificada, "cadena"].dropna()) - set(matriz))
    diag.notas.append(
        f"Introducciones por {regla}: {len(permitidos):,} pares tienda×modelo "
        "habilitados."
        + (
            f" Cadenas sin marcas definidas: {', '.join(sin_matriz)}."
            if sin_matriz and cm is None
            else ""
        )
    )
    diag.tiendas = dim_t[
        ["tienda_id", "nombre", "cadena", "centro_comercial", "zona", "origen_tienda", "activa"]
    ].to_dict("records")
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
        ventas=pd.concat([sem, en_curso[sem.columns]], ignore_index=True)[
            ["semana_inicio", "tienda_id", "sku", "unidades", "dias_con_stock"]
        ],
        stock_tienda=st,
        stock_cd=stock_cd[
            ["sku", "fisico", "reservado", "comprometido"]
            + [c for c in ("cd_stock_tiendas", "cd_stock_bodega") if c in stock_cd]
        ],
        dim_producto=dim,
        dim_tienda=dim_t,
        permitidos=permitidos,
    )


def ventana(fecha_corte: pd.Timestamp, semanas: int) -> dict[str, dt.date]:
    corte = pd.Timestamp(fecha_corte).date()
    return {
        "desde": corte - dt.timedelta(weeks=semanas),
        "hasta": corte,
        "desde_foto": corte - dt.timedelta(days=400),
        "hasta_foto": max(corte + dt.timedelta(days=7), dt.date.today() + dt.timedelta(days=1)),
    }
