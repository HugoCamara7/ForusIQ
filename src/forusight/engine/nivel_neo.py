"""Nivel máximo y punto de reorden con la misma estructura que el reporte de distribución.

Reconstruido y validado con los 7 reportes de septiembre 2026 (todas las marcas el 30/09):

    pronóstico_mc   = k(categoría) × venta de las 4 últimas semanas cerradas (modelo-color × tienda)
    pronóstico_sku  = pronóstico_mc × w(talla) / Σ w de las tallas que la tienda vendió en 12 semanas
                      (w = venta nacional de 12 semanas del SKU: la curva es la misma en todas las
                      tiendas; la talla sin venta en 12 semanas no recibe pronóstico)
    d               = pronóstico_sku × (lead time + revisión) / 7
    nivel máximo    = max(SMT, ceil(d + 0,84·√d))        (exacto en el 87,9 % de las filas)
    punto reorden   = nivel − 1                           (87,2 %)
    se pide sólo si ceil(posición) ≤ punto de reorden, hasta el nivel, en múltiplos de la UE.

SMT, k y UE son parámetros del planificador (no salen de la venta): se toman del maestro de
planificación (``data/planificacion.py``). Sin maestro: SMT = 1, k base por clase, UE = 1.

Universo (qué tienda × talla se evalúa, como el reporte; precisión/recobro 97 %/97 %): la talla
tiene stock, tránsito o venta en 12 semanas, y el modelo-color vendió en la tienda en las 4
últimas semanas o desde la ruta anterior; o la clave estaba en un reporte reciente.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from forusight.config.settings import EngineParams
from forusight.data import planificacion as PLAN

KEY = ["tienda_id", "sku"]


def _attr(sku: pd.DataFrame, dim_producto: pd.DataFrame, col: str) -> pd.Series:
    if col not in dim_producto:
        return pd.Series(pd.NA, index=sku.index, dtype="string")
    m = dim_producto.drop_duplicates("sku").set_index("sku")[col]
    return sku["sku"].map(m).astype("string")


def _smt(sku: pd.DataFrame, prod: pd.DataFrame, m: PLAN.Maestro, frescas: pd.Series) -> np.ndarray:
    """SMT de la clave (reporte reciente) → moda del SKU en otras tiendas → marca×prenda×talla
    → marca×prenda → 1."""
    out = pd.Series(np.nan, index=sku.index)
    if len(m.claves):
        c = m.claves.dropna(subset=["smt"])
        out = out.fillna(frescas)
        info = c.merge(prod, on="sku", how="left")
        por_sku = info.groupby("sku")["smt"].agg(PLAN._moda)
        out = out.fillna(sku["sku"].map(por_sku))
        for cols in (["marca", "prenda", "talla"], ["marca", "prenda"]):
            if all(x in info for x in cols):
                t = info.dropna(subset=cols).groupby(cols)["smt"].agg(PLAN._moda)
                idx = pd.MultiIndex.from_frame(sku[cols].astype(object))
                out = out.fillna(pd.Series(t.reindex(idx).to_numpy(), index=sku.index))
    return out.fillna(1.0).to_numpy(dtype=float)


def p_dia(m: PLAN.Maestro) -> pd.Timestamp:
    """Día de la corrida (lo fija ``planificacion.vigente``; si no, la fecha más reciente)."""
    dia = getattr(m, "dia", None)
    if dia is not None:
        return dia
    return pd.to_datetime(m.claves["fecha"]).max() if len(m.claves) else pd.Timestamp.today()


def niveles(
    sku: pd.DataFrame,
    semanal: pd.DataFrame,
    tiendas: pd.DataFrame,
    dim_producto: pd.DataFrame,
    maestro: PLAN.Maestro | None,
    params: EngineParams,
) -> pd.DataFrame:
    """tienda_id, sku, nivel_ref, rop_ref, ue_ref, pronostico_ref para cada fila de ``sku``."""
    p = params.nivel_neo
    m = maestro if maestro is not None else PLAN.vacio()
    s = sku[KEY + ["modelo_color_id", "talla", "venta_4s", "venta_12s"]].copy()
    for c in ("stock_disponible", "stock_transito", "venta_desde_ruta", "venta_post_corte"):
        s[c] = sku[c].fillna(0).to_numpy() if c in sku else 0.0
    prod = pd.DataFrame({"sku": dim_producto["sku"]})
    for c in ("marca", "prenda", "categoria", "genero", "talla"):
        prod[c] = (
            dim_producto[c].astype("string").str.strip().str.upper() if c in dim_producto else pd.NA
        )
    prod = prod.drop_duplicates("sku")
    for c in ("marca", "prenda", "categoria", "genero"):
        s[c] = _attr(s, prod, c)
    s["talla_u"] = s["talla"].astype("string").str.strip().str.upper()

    # --- pronóstico de la talla
    mc = [s["tienda_id"], s["modelo_color_id"]]
    s4_mc = s["venta_4s"].clip(lower=0).groupby(mc).transform("sum")
    cat = PLAN.clave_categoria(s["marca"], s["categoria"], s["prenda"], s["genero"])
    if len(m.claves) and "categoria_k" in m.claves:  # categoría con los nombres del reporte
        cat_rep = (
            m.claves.dropna(subset=["categoria_k"]).groupby("sku")["categoria_k"].agg(PLAN._moda)
        )
        cat = s["sku"].map(cat_rep).fillna(cat)
    k = cat.map(m.k.set_index("categoria_k")["k"]) if len(m.k) else pd.Series(np.nan, index=s.index)
    k = k.astype(float).fillna(s["categoria"].map(PLAN.K_BASE)).fillna(PLAN.K_DEFECTO)
    nacional = semanal.groupby("sku")["unidades"].sum() if len(semanal) else pd.Series(dtype=float)
    activa = s["venta_12s"] > 0
    w = np.where(activa, s["sku"].map(nacional).fillna(0).clip(lower=0), 0.0)
    w = pd.Series(w, index=s.index)
    tot = w.groupby(mc).transform("sum")
    share = np.where(tot > 0, w / tot.where(tot > 0, 1), 0.0)
    fc = (k * s4_mc).to_numpy() * share

    # --- nivel y punto de reorden
    t = tiendas.set_index("tienda_id")
    lt = s["tienda_id"].map(t["leadtime_dias"]) if "leadtime_dias" in t else np.nan
    rv = s["tienda_id"].map(t["revision_dias"]) if "revision_dias" in t else np.nan
    lt = pd.Series(lt, index=s.index).fillna(params.calendario.leadtime_dias_defecto)
    rv = pd.Series(rv, index=s.index).fillna(params.calendario.revision_dias_defecto)
    d = np.clip(fc * (lt + rv).to_numpy() / 7.0, 0, None)

    ref = (
        s[KEY].merge(m.claves, on=KEY, how="left")
        if len(m.claves)
        else s[KEY].assign(smt=np.nan, nivel=np.nan, rop=np.nan, ue=np.nan, fecha=pd.NaT)
    )
    ref.index = s.index
    frescas = ref["smt"].where(ref["nivel"].notna())
    smt = _smt(s, prod, m, frescas)
    nivel = np.maximum(smt, np.ceil(d + p.z * np.sqrt(d) - 1e-9))
    if p.nivel_reporte_como_piso:  # la clave de un reporte reciente no baja de su nivel
        nivel = np.maximum(nivel, ref["nivel"].fillna(0).to_numpy())
    # Clave de un reporte de hace pocos días: su nivel y reorden tal cual (el nivel cambia poco
    # en 2 días: 93,6 % igual 28→30/09; la fórmula se pasa en las tallas de alta rotación).
    usa_rep = np.zeros(len(s), dtype=bool)
    if p.dias_nivel_reporte > 0 and len(m.claves):
        edad = (pd.Timestamp(p_dia(m)) - pd.to_datetime(ref["fecha"])).dt.days
        usa_rep = (edad <= p.dias_nivel_reporte).fillna(False).to_numpy() & ref[
            "nivel"
        ].notna().to_numpy()
        nivel = np.where(usa_rep, ref["nivel"].fillna(0).to_numpy(), nivel)

    # --- universo evaluado (como el reporte)
    talla_viva = (
        (s["stock_disponible"] > 0)
        | (s["stock_transito"] > 0)
        | activa
        | (s["venta_desde_ruta"] > 0)
    )
    reciente = (
        s["venta_4s"].clip(lower=0) + s["venta_desde_ruta"] + s["venta_post_corte"]
    ).groupby(mc).transform("sum") > 0
    listada = ref["nivel"].fillna(0).to_numpy() > 0
    evalua = (talla_viva & reciente).to_numpy()
    if p.universo == "listada_reciente":  # talla de un reporte reciente, si el modelo se mueve
        evalua = evalua | (listada & reciente.to_numpy())
    elif p.universo == "listada":
        evalua = evalua | listada
    # Clave de un reporte de hace pocos días: el reporte ya decidió que se evalúa.
    evalua = evalua | (usa_rep & listada)
    nivel = np.where(evalua, nivel, 0)

    # Banda nivel − reorden: 1 en el 87 % de las filas; la clave conserva la de su último reporte.
    banda = np.ones(len(s))
    if p.banda_reporte:
        b = (ref["nivel"] - ref["rop"]).where(ref["nivel"] > 0)
        banda = np.clip(b.fillna(1).to_numpy(), 1, None)

    # Reporte reciente sin espacio en la tienda (motivo Almacenamiento): no se pide.
    alm = ref["almacenamiento"] if "almacenamiento" in ref else pd.Series(False, index=s.index)
    sin_espacio = usa_rep & alm.fillna(False).astype(bool).to_numpy()

    ue = ref["ue"]
    if len(m.claves):
        ue = ue.fillna(s["sku"].map(m.claves.groupby("sku")["ue"].agg(PLAN._moda)))
    ue = ue.fillna(1).clip(lower=1).to_numpy()
    return pd.DataFrame(
        {
            "tienda_id": s["tienda_id"].to_numpy(),
            "sku": s["sku"].to_numpy(),
            "nivel_ref": nivel.astype(float),
            "rop_ref": np.where(
                evalua & ~sin_espacio,
                np.where(usa_rep, ref["rop"].fillna(-1).to_numpy(), nivel - banda),
                -1,
            ).astype(float),
            "ue_ref": ue.astype(float),
            "smt_ref": np.where(evalua, smt, 0).astype(float),
            "pronostico_ref": fc,
        }
    )
