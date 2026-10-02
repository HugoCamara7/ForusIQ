"""Envíos pendientes de recepción (evita repartir dos veces el mismo stock del CD).

Con reposición lunes, miércoles y viernes, el corte de stock de hoy (cierre de ayer) todavía
cuenta en el CD lo que se aprobó en la corrida anterior y aún no llega a la tienda. Si no se
descuenta, Forusight vuelve a repartirlo y la suma de envíos sobrepasa el stock real.

Fuentes (se suman):
  * aprobaciones guardadas por Forusight en GitHub en los últimos ``dias`` días;
  * archivos que sube el usuario: la aprobación (CSV) o el archivo Forusight (xlsx) anterior.

Efecto: el CD pierde esas unidades (comprometido) y la tienda destino las suma como tránsito.
"""

from __future__ import annotations

import io
import re

import pandas as pd

from forusight.data.fuentes import codigo_tienda, sku_canonico

COLUMNAS = ["tienda_id", "sku", "cantidad"]
_FECHA_ARCHIVO = re.compile(r"_(\d{8})_(\d{6})\.csv$")


def vacio() -> pd.DataFrame:
    return pd.DataFrame(
        {c: pd.Series(dtype="string" if c != "cantidad" else "int64") for c in COLUMNAS}
    )


def _normalizar(tienda, sku, cantidad) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "tienda_id": pd.Series(tienda).map(codigo_tienda).astype("string").to_numpy(),
            "sku": sku_canonico(pd.Series(sku)).astype("string").to_numpy(),
            "cantidad": pd.to_numeric(pd.Series(cantidad), errors="coerce").fillna(0).to_numpy(),
        }
    )
    df = df.loc[(df["cantidad"] > 0) & df["tienda_id"].notna() & df["sku"].notna()]
    return (
        df.groupby(["tienda_id", "sku"], as_index=False)["cantidad"]
        .sum()
        .astype({"cantidad": "int64"})
    )


def leer_archivo(contenido: bytes, nombre: str) -> pd.DataFrame:
    """Aprobación CSV de Forusight o archivo Forusight / reporte (xlsx) → tienda, sku, cantidad."""
    if nombre.lower().endswith(".csv"):
        d = pd.read_csv(io.BytesIO(contenido), dtype=str, encoding="utf-8-sig")
        col = "cantidad_aprobada" if "cantidad_aprobada" in d else "cantidad"
        if not {"tienda_id", "sku", col} <= set(d.columns):
            raise ValueError(f"{nombre}: el CSV debe traer tienda_id, sku y cantidad_aprobada.")
        return _normalizar(d["tienda_id"], d["sku"], d[col])
    from forusight.data import reporte as R

    d, _ = R.leer_reporte(contenido)
    if R.Q not in d:
        raise ValueError(f"{nombre}: no trae la columna «{R.Q}».")
    return _normalizar(d[R.CENTRO], d[R.SKU], d[R.Q])


def desde_github(
    secrets, dias: int, hoy: pd.Timestamp | None = None
) -> tuple[pd.DataFrame, list[str]]:
    """Aprobaciones de los últimos ``dias`` días guardadas en GitHub (la última por corrida)."""
    from forusight.data.github_store import GitHubStore, config_github

    cfg = config_github(secrets)
    if cfg is None or dias <= 0:
        return vacio(), []
    store = GitHubStore(cfg["repository"], cfg["token"], cfg["branch"], cfg["prefix"])
    hoy = (hoy or pd.Timestamp.now()).normalize()
    ultimos: dict[str, tuple[str, str]] = {}
    for f in store.listar("aprobaciones"):
        m = _FECHA_ARCHIVO.search(f.get("name", ""))
        if not m:
            continue
        fecha = pd.Timestamp(m.group(1))
        if (hoy - fecha).days > dias:
            continue
        run_id = f["name"][: m.start()]
        marca = m.group(1) + m.group(2)
        if run_id not in ultimos or marca > ultimos[run_id][0]:
            ultimos[run_id] = (marca, f["path"])
    partes, usados = [], []
    for _, ruta in sorted(ultimos.values()):
        contenido = store.leer(ruta)
        if contenido:
            partes.append(leer_archivo(contenido, ruta))
            usados.append(ruta.rsplit("/", 1)[-1])
    return sumar(partes), usados


def sumar(partes: list[pd.DataFrame]) -> pd.DataFrame:
    partes = [p for p in partes if p is not None and len(p)]
    if not partes:
        return vacio()
    return pd.concat(partes).groupby(["tienda_id", "sku"], as_index=False)["cantidad"].sum()


def preparar_transito(inputs, params):
    """stock_bi.transito es la salida de la tienda ORIGEN (traspasos entre tiendas), no lo que
    viene del CD (30/09: 2 u en común con el tránsito del reporte en 796): sin él por defecto."""
    from dataclasses import replace

    if params.recepcion.usar_transito_bigquery:
        return inputs
    st = inputs.stock_tienda.copy()
    st["stock_transito"] = 0.0
    return replace(inputs, stock_tienda=st)


def usa_pedidos(inputs, params) -> bool:
    """El tránsito sale de las tablas de pedidos (si se leyeron y está activado)."""
    return bool(params.recepcion.transito_pedidos) and getattr(inputs, "pedidos", None) is not None


def aplicar_pedidos(inputs, params):
    """Tránsito de cada tienda × SKU = pedidos hacia la tienda en los estados y clasificaciones
    de ``params.recepcion`` (reemplaza el tránsito de stock_bi y el de las aprobaciones)."""
    from dataclasses import replace

    from forusight.data.fuentes import comprometido_cd, transito_de_pedidos

    rec = params.recepcion
    tr = transito_de_pedidos(
        inputs.pedidos,
        rec.estados_transito,
        rec.clasificaciones_transito,
        rec.recepcionados_post_corte,
    )
    st = inputs.stock_tienda.drop(columns="stock_transito", errors="ignore")
    st = st.merge(tr, on=["tienda_id", "sku"], how="outer")
    st[["stock_disponible", "stock_transito"]] = st[
        ["stock_disponible", "stock_transito"]
    ].fillna(0.0)
    cd = inputs.stock_cd
    if rec.descontar_pedidos_del_cd:
        # Lo comprometido con tiendas que el corte del CD aún cuenta: no se reparte dos veces.
        comp = comprometido_cd(inputs.pedidos, rec.clasificaciones_desde_cd)
        cd = cd.copy()
        cd["comprometido"] = cd["comprometido"] + cd["sku"].map(comp).fillna(0)
    return replace(inputs, stock_tienda=st, stock_cd=cd)


def aplicar_a_entradas(inputs, pend: pd.DataFrame, params=None):
    """Suma como tránsito en la tienda destino (y, si se pide, descuenta del CD).

    Con ``params``: ``recepcion.descontar_del_cd`` (el corte del CD de BigQuery ya descuenta lo
    despachado) y ``recepcion.transito_solo_provincia`` (en Lima llega al día siguiente)."""
    from dataclasses import replace

    if pend is None or pend.empty:
        return inputs
    rec = params.recepcion if params is not None else None
    if rec is not None and rec.transito_solo_provincia and "zona" in inputs.dim_tienda:
        zona = inputs.dim_tienda.set_index("tienda_id")["zona"].astype("string").str.upper()
        prov = set(zona.index[zona.str.contains("PROVINCIA", na=False)])
        pend_tr = pend.loc[pend["tienda_id"].isin(prov)]
    else:
        pend_tr = pend
    cd = inputs.stock_cd.copy()
    if rec is None or rec.descontar_del_cd:
        por_sku = pend.groupby("sku")["cantidad"].sum()
        cd["comprometido"] = cd["comprometido"] + cd["sku"].map(por_sku).fillna(0)
    pend = pend_tr
    if pend.empty:
        return replace(inputs, stock_cd=cd)
    st = inputs.stock_tienda.copy()
    st = st.merge(pend.rename(columns={"cantidad": "_pend"}), on=["tienda_id", "sku"], how="outer")
    st["stock_disponible"] = st["stock_disponible"].fillna(0.0)
    st["stock_transito"] = st["stock_transito"].fillna(0.0) + st["_pend"].fillna(0)
    st = st.drop(columns="_pend")
    return replace(inputs, stock_cd=cd, stock_tienda=st)


def aplicar_a_reporte(df: pd.DataFrame, pend: pd.DataFrame) -> pd.DataFrame:
    """Lo mismo sobre las filas del reporte: Stock en CD − pendiente del SKU, y el pendiente
    de cada tienda × SKU como tránsito interno. Deja la columna ``_pendiente_recepcion``."""
    from forusight.data import reporte as R

    out = df.copy()
    out["_pendiente_recepcion"] = 0
    if pend is None or pend.empty:
        return out
    sku = sku_canonico(out[R.SKU].astype("string"))
    tienda = out[R.CENTRO].map(codigo_tienda)
    por_sku = pend.groupby("sku")["cantidad"].sum()
    fila = pend.set_index(["tienda_id", "sku"])["cantidad"]
    llave = pd.MultiIndex.from_arrays([tienda, sku])
    p_fila = pd.Series(fila.reindex(llave).fillna(0).to_numpy(), index=out.index).astype("int64")
    out[R.CD] = (R._num(out[R.CD]) - sku.map(por_sku).fillna(0).to_numpy()).clip(lower=0)
    out[R.TR_INT] = R._num(out.get(R.TR_INT, 0)) + p_fila
    out["_pendiente_recepcion"] = p_fila
    return out
