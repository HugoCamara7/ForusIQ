"""Reporte de distribución del día como base de la corrida.

El reporte diario (hoja con "Código SKU" en la fila de encabezado) trae, por tienda × SKU:
venta de las últimas 12 semanas, stock físico y en tránsito, stock en CD, punto de reorden,
nivel máximo, unidad de empaque y el grupo de requerimiento. Con él Forusight:

* no depende de que la tabla de ventas de BigQuery esté al día (la venta viene del reporte);
* recalcula la necesidad con la misma regla del reporte (validada en 99,6 % de las filas):
      posición = físico + tránsito int. + tránsito prov. − comprometido − backorder
      si posición ≤ punto de reorden:  pedir hasta el nivel máximo,
      redondeado a la unidad de empaque (mínimo un empaque);
* respeta las cargas manuales ("Carga Pedidos", "Reposicion Jerarquia"): se informan como
  pendientes de distribución, igual que en el reporte;
* reparte el stock del CD con la prioridad de Forusight cuando no alcanza.
"""

from __future__ import annotations

import io
import re

import numpy as np
import pandas as pd

from forusight.data.cadenas import prefijo

SKU, CENTRO = "Código SKU", "Código Centro"
Q, P, MOT = "Cantidad Pedida Final [un]", "Pendiente Reposición", "Motivo Pendiente Reposición"
GRUPO, CD = "Grupo Requerimiento", "Stock en CD"
MAX, ROP, UE = "Nivel Máximo [un]", "Punto Reorden [un]", "Unidad Empaque Distribución"
FISICO, TR_INT, TR_PROV = "Stock Físico [un]", "Stock Trán. Int. [un]", "Stock Trán. Prov. [un]"
COMPROMETIDO, BACKORDER = "Stock Comprometido [un]", "Backorder [un]"
POSICION, PRONOSTICO = "Posición Stock [un]", "Pronóstico Demanda [un/semana]"
TEXTO = [SKU, CENTRO, "Código Modelo", "Código Color", "Talla", "Código Centro Origen"]
SEMANA = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: Motivos del reporte.
SIN_PENDIENTE, STOCK_CD, DISTRIBUCION = "Sin Reposición Pendiente", "Stock CD", "Distribución"


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0.0)


def leer_reporte(contenido: bytes) -> tuple[pd.DataFrame, pd.Timestamp | None]:
    """(filas del reporte, fecha del reporte). Busca la hoja y la fila de encabezado."""
    try:
        hojas = pd.read_excel(
            io.BytesIO(contenido), sheet_name=None, header=None, engine="calamine"
        )
    except (ImportError, ValueError):
        hojas = pd.read_excel(io.BytesIO(contenido), sheet_name=None, header=None)
    candidatas = []  # la hoja con más filas bajo un encabezado "Código SKU"
    for hoja in hojas.values():
        if hoja.empty or hoja.shape[1] < 2:
            continue
        filas = hoja.index[hoja.iloc[:, 0].astype(str).str.strip().eq(SKU)]
        if len(filas):
            candidatas.append((len(hoja) - int(filas[0]), int(filas[0]), hoja))
    if not candidatas:
        raise ValueError("El archivo no tiene una hoja con la columna 'Código SKU'.")
    _, h, crudo = max(candidatas, key=lambda c: c[0])
    fecha = None
    for i in range(h):
        if str(crudo.iat[i, 0]).strip().lower().startswith("fecha"):
            fecha = pd.to_datetime(str(crudo.iat[i, 1]), dayfirst=True, errors="coerce")
    cols = [
        c.strftime("%Y-%m-%d") if isinstance(c, pd.Timestamp | np.datetime64) else str(c).strip()
        for c in crudo.iloc[h]
    ]
    cols = [str(pd.Timestamp(c).date()) if c[:4].isdigit() and "-" in c else c for c in cols]
    df = crudo.iloc[h + 1 :].copy()
    df.columns = cols
    df = df.loc[df[SKU].notna()].reset_index(drop=True)
    for c in TEXTO:
        if c in df:
            df[c] = df[c].map(_texto_codigo).astype("string")
    if "Talla" in df:  # la talla viene con ceros a la izquierda ("075", "390")
        df["Talla"] = (
            df["Talla"].str.zfill(3).where(df["Talla"].str.fullmatch(r"\d{1,3}"), df["Talla"])
        )
    return df, (None if fecha is None or pd.isna(fecha) else fecha)


def _texto_codigo(x) -> str | None:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x).strip()


def semanas(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if SEMANA.match(str(c))]


def posicion(df: pd.DataFrame) -> pd.Series:
    return (
        _num(df[FISICO])
        + _num(df.get(TR_INT, 0))
        + _num(df.get(TR_PROV, 0))
        - _num(df.get(COMPROMETIDO, 0))
        - _num(df.get(BACKORDER, 0))
    )


def es_carga(df: pd.DataFrame) -> pd.Series:
    """Cargas manuales (pedidos o jerarquía) sin revisión de stock."""
    g = df[GRUPO].astype("string").fillna("")
    return g.str.contains("Carga Pedidos|Jerarquia", regex=True) & ~g.str.contains(
        "Revision de stock"
    )


def es_mixta(df: pd.DataFrame) -> pd.Series:
    g = df[GRUPO].astype("string").fillna("")
    return g.str.contains("Carga Pedidos|Jerarquia", regex=True) & g.str.contains(
        "Revision de stock"
    )


def es_revision(df: pd.DataFrame) -> pd.Series:
    return df[GRUPO].astype("string").fillna("").str.contains("Revision de stock")


def necesidad(df: pd.DataFrame) -> pd.Series:
    """Necesidad por la regla del reporte (revisión de stock) o la carga manual indicada."""
    pos = posicion(df)
    maximo = pd.to_numeric(df[MAX], errors="coerce")
    rop = pd.to_numeric(df[ROP], errors="coerce")
    ue = _num(df.get(UE, 1)).clip(lower=1)
    bruta = np.where(maximo.notna() & (pos <= rop), (maximo - pos).clip(lower=0), 0)
    empaques = np.where(bruta > 0, np.maximum(np.floor(bruta / ue + 0.5), 1), 0)
    revision = empaques * ue
    bloqueada = df.get("Reposición Bloqueada", pd.Series("NO", index=df.index)).astype(str).eq("SI")
    carga_indicada = _num(df.get(Q, 0)) + _num(df.get(P, 0))
    rev = es_revision(df).to_numpy() & ~bloqueada.to_numpy()
    nec = np.where(rev, revision, 0)
    # Carga manual: la cantidad indicada; mixta (carga + revisión): revisión + carga, que el
    # reporte informa sumadas en cantidad + pendiente.
    mixta = rev & es_mixta(df).to_numpy()
    nec = np.where(mixta, np.maximum(nec, carga_indicada), nec)
    nec = np.where(es_carga(df), carga_indicada, nec)
    return pd.Series(nec, index=df.index).astype("int64")


def filtrar_marcas(df: pd.DataFrame, marcas: list[str] | None) -> pd.DataFrame:
    if not marcas or "Marca" not in df:
        return df
    pedidas = {m.strip().upper() for m in marcas}
    return df.loc[df["Marca"].astype("string").str.strip().str.upper().isin(pedidas)].reset_index(
        drop=True
    )


def corte(df: pd.DataFrame) -> pd.Timestamp:
    """Lunes siguiente a la última semana del reporte (12 semanas cerradas)."""
    return pd.Timestamp(max(semanas(df))) + pd.Timedelta(weeks=1)


#: Criterios de reparto cuando el stock del CD no alcanza.
IGUAL_REPORTE, PRIORIDAD_FORUSIGHT = "reporte", "forusight"


def distribuir(
    df: pd.DataFrame,
    patrones_prioridad: list[str] | None = None,
    criterio: str = IGUAL_REPORTE,
) -> pd.DataFrame:
    """Recalcula cantidad, pendiente y motivo de cada fila del reporte.

    Por SKU: si el stock del CD alcanza, cada tienda recibe su necesidad; si no, se reparte
    en múltiplos de empaque por prioridad: tiendas prioritarias (p. ej. JOCKEY), luego mayor
    pronóstico y menor alcance. Las cargas manuales quedan pendientes de distribución.

    ``criterio``:
      * ``reporte``: lo que el reporte decide con información que no está en sus columnas
        (capacidad de almacenamiento de la tienda, cargas manuales despachadas, SKUs que el
        reporte racionó y el orden de reparto de un SKU escaso) se respeta tal cual → mismo
        resultado que el reporte.
      * ``forusight``: el CD escaso se reparte primero a las tiendas prioritarias (JOCKEY),
        luego por nivel de servicio planificado y necesidad más chica.
    """
    out = df.copy()
    nec = necesidad(out)
    ue = _num(out.get(UE, 1)).clip(lower=1).astype("int64")
    carga = es_carga(out).to_numpy()
    stock_cd = _num(out[CD]).astype("int64")
    nombre = out.get("Nombre Centro", pd.Series("", index=out.index)).astype("string").fillna("")
    prio = pd.Series(False, index=out.index)
    for pat in patrones_prioridad or []:
        if str(pat).strip():
            prio |= nombre.str.upper().str.contains(str(pat).strip().upper(), regex=False)
    pos = posicion(out)
    pron = _num(out.get(PRONOSTICO, 0))
    alcance = np.where(pron > 0, pos / pron.where(pron > 0, 1), np.inf)

    cant = np.zeros(len(out), dtype=np.int64)
    pide = (nec > 0).to_numpy() & ~carga
    q_rep = _num(df.get(Q, 0)).astype("int64").to_numpy()
    mot_rep = df.get(MOT, pd.Series("", index=df.index)).astype("string").fillna("")
    fijo = np.zeros(len(out), dtype=bool)
    if criterio == IGUAL_REPORTE:
        # Capacidad de la tienda y cargas manuales: decisión del reporte.
        mixta = es_mixta(out).to_numpy() & pide
        p_rep = _num(df.get(P, 0)).to_numpy()
        # SKU racionado por el reporte: envió menos que la necesidad sin dejar pendiente.
        racionado = (
            pide & (q_rep < nec.to_numpy()) & (p_rep == 0) & mot_rep.eq(SIN_PENDIENTE).to_numpy()
        )
        fijo = (
            (mot_rep.eq("Almacenamiento").to_numpy() & pide)
            | (carga & (q_rep > 0))
            | mixta
            | racionado
        )
        nec = pd.Series(np.where(racionado, q_rep, nec), index=out.index).astype("int64")
        cant[fijo] = np.minimum(q_rep[fijo], np.maximum(nec.to_numpy()[fijo], q_rep[fijo]))
    ndp = _num(out.get("Nivel de Disponibilidad Planificado [%]", 0))
    orden = pd.DataFrame(
        {
            "sku": out[SKU],
            "rep": -q_rep if criterio == IGUAL_REPORTE else 0,
            "prio": ~prio if criterio == PRIORIDAD_FORUSIGHT else False,
            "ndp": -ndp,
            "nec": nec,
            "pron": -pron,
            "alc": alcance,
            "centro": out[CENTRO],
        }
    )
    claves = ["sku", "rep", "prio", "ndp", "nec", "pron", "alc", "centro"]
    idx = orden.loc[pide & ~fijo].sort_values(claves, kind="mergesort")
    restante: dict[str, int] = {}
    nec_v, ue_v, cd_v = nec.to_numpy(), ue.to_numpy(), stock_cd.to_numpy()
    for i in np.flatnonzero(fijo):  # lo ya fijado consume stock del CD
        sku = out[SKU].iat[i]
        restante[sku] = restante.get(sku, int(cd_v[i])) - int(cant[i])
    for i, sku in zip(idx.index, idx["sku"], strict=True):
        r = restante.setdefault(sku, int(cd_v[i]))
        q = min(int(nec_v[i]), r)
        q = (q // ue_v[i]) * ue_v[i] if ue_v[i] > 1 else q
        cant[i] = q
        restante[sku] = r - q

    out[Q] = cant
    out[P] = (nec.to_numpy() - cant).clip(min=0)
    sin_cd = stock_cd.to_numpy() <= 0
    almacen = mot_rep.eq("Almacenamiento").to_numpy() & fijo
    # Carga pendiente: "Distribución" si el CD que queda tras reponer la cubre; si no, "Stock CD".
    queda = out[SKU].map(lambda k: restante.get(k)).astype("Float64")
    queda = queda.fillna(pd.Series(cd_v, index=out.index).astype("Float64")).to_numpy(dtype=float)
    cubre = ~sin_cd & (queda >= out[P].to_numpy())
    out[MOT] = np.select(
        [out[P].to_numpy() == 0, almacen, (carga | es_mixta(out).to_numpy()) & cubre],
        [SIN_PENDIENTE, "Almacenamiento", DISTRIBUCION],
        STOCK_CD,
    )
    out[POSICION] = pos
    if "Alcance Posición Stock Final [semanas]" in out:
        out["Alcance Posición Stock Final [semanas]"] = np.where(
            pron > 0, (pos + cant) / pron.where(pron > 0, 1), 0
        )
    if "Monto Pedido Final [$]" in out and "Costo [$/un]" in out:
        out["Monto Pedido Final [$]"] = cant * _num(out["Costo [$/un]"])
    out["_necesidad"] = nec
    return out


# ------------------------------------------------------------------ integración con la app


def entradas_desde_reporte(df: pd.DataFrame):
    """EngineInputs para el dashboard y las páginas, construidas sólo con el reporte."""
    from forusight.data.fuentes import inferir_exposicion, talla_orden
    from forusight.engine.pipeline import EngineInputs

    t = df.assign(tienda_id=df[CENTRO].astype("string"), sku=df[SKU].astype("string"))
    wk = semanas(t)
    v = t.melt(
        id_vars=["tienda_id", "sku"], value_vars=wk, var_name="semana_inicio", value_name="unidades"
    )
    v["semana_inicio"] = pd.to_datetime(v["semana_inicio"])
    v["unidades"] = _num(v["unidades"])
    v = v.loc[v["unidades"] > 0]
    pos = posicion(t)
    stock = pd.DataFrame(
        {
            "tienda_id": t["tienda_id"],
            "sku": t["sku"],
            "stock_disponible": _num(t[FISICO]),
            "stock_transito": pos - _num(t[FISICO]),
        }
    )
    sem = inferir_exposicion(v, stock, [pd.Timestamp(w) for w in wk])
    prod = t.drop_duplicates("sku")
    modelo = prod.get("Código Modelo", prod["sku"]).astype("string")
    color = prod.get("Código Color", pd.Series("", index=prod.index)).astype("string").fillna("")
    dim_p = pd.DataFrame(
        {
            "sku": prod["sku"],
            "modelo_id": modelo,
            "modelo_color_id": modelo + "-" + color,
            "color": prod.get("Color", color).astype("string"),
            "talla": prod["Talla"].astype("string"),
            "talla_orden": talla_orden(prod["Talla"].astype("string")).astype(float),
            "categoria": prod.get("Clase", pd.Series("NA", index=prod.index))
            .astype("string")
            .fillna("NA"),
            "genero": prod.get("Género", pd.Series("NA", index=prod.index))
            .astype("string")
            .fillna("NA"),
            "rango_precio": "NA",
            "marca": prod.get("Marca", pd.Series(pd.NA, index=prod.index)).astype("string"),
            "descripcion": prod.get("Modelo", pd.Series(pd.NA, index=prod.index)).astype("string"),
        }
    )
    cd = pd.DataFrame(
        {"sku": prod["sku"], "fisico": _num(prod[CD]), "reservado": 0.0, "comprometido": 0.0}
    )
    ti = t.drop_duplicates("tienda_id")
    nombre = ti.get("Nombre Centro", ti["tienda_id"]).astype("string")
    venta_t = v.groupby("tienda_id")["unidades"].sum()
    dim_t = pd.DataFrame(
        {
            "tienda_id": ti["tienda_id"],
            "nombre": nombre,
            "cluster": None,
            "formato": None,
            "importancia_comercial": ti["tienda_id"]
            .map(venta_t.rank(pct=True))
            .fillna(0.05)
            .clip(0.05, 1),
            "activa": True,
            "max_unidades_corrida": np.nan,
            "centro_comercial": ti.get("Centro Comercial", pd.Series(pd.NA, index=ti.index)).astype(
                "string"
            ),
            "zona": ti.get("Zona CC", pd.Series(pd.NA, index=ti.index)).astype("string"),
            "cadena": prefijo(nombre),
            "origen_tienda": "reporte",
        }
    )
    return EngineInputs(
        ventas=sem,
        stock_tienda=stock,
        stock_cd=cd,
        dim_producto=dim_p,
        dim_tienda=dim_t,
        reporte=df,
    )


def resultado_desde_reporte(dist: pd.DataFrame, run_id: str, fecha_corte, cd_id: str, params):
    """EngineResult con el detalle tienda × SKU del reporte recalculado por ``distribuir``."""
    from forusight.engine.pipeline import COLUMNAS_EXTRA, COLUMNAS_SALIDA, EngineResult

    d = dist
    wk = semanas(d)
    v12 = d[wk].apply(_num).sum(axis=1) if wk else pd.Series(0.0, index=d.index)
    v4 = d[wk[-4:]].apply(_num).sum(axis=1) if wk else v12 * 0
    pos = _num(d[POSICION])
    fisico = _num(d[FISICO])
    mc = (
        d.get("Código Modelo", d[SKU]).astype("string")
        + "-"
        + d.get("Código Color", pd.Series("", index=d.index)).astype("string").fillna("")
    )
    tienda = d[CENTRO].astype("string")
    g = pd.DataFrame({"t": tienda, "mc": mc, "v": v12, "s": fisico})
    agg = g.groupby(["t", "mc"]).agg(v=("v", "sum"), s=("s", "sum"))
    est_mc = np.select(
        [(agg.v > 0) & (agg.s > 0), agg.v > 0, agg.s > 0],
        ["TUVO_Y_VENDE", "QUIEBRE", "TUVO_SIN_VENTA"],
        "NUNCA_TUVO",
    )
    estado_mc = (
        pd.Series(est_mc, index=agg.index)
        .reindex(pd.MultiIndex.from_arrays([tienda, mc]))
        .to_numpy()
    )
    estado_sku = np.select(
        [(v12 > 0) & (fisico > 0), v12 > 0, fisico > 0],
        ["TUVO_Y_VENDE", "QUIEBRE", "TUVO_SIN_VENTA"],
        "NUNCA_TUVO",
    )
    q = d[Q].astype("int64")
    nec = d["_necesidad"].astype("int64")
    mot = d[MOT].astype("string")
    cd = _num(d[CD])
    codigo = np.select(
        [
            (q > 0) & (fisico <= 0) & (v12 > 0),
            q > 0,
            nec <= 0,
            mot.eq("Almacenamiento"),
            mot.eq(DISTRIBUCION),
            cd <= 0,
        ],
        [
            "ENVIO_QUIEBRE",
            "ENVIO_REPOSICION",
            "NO_SIN_NECESIDAD",
            "NO_ALMACENAMIENTO",
            "PEND_DISTRIBUCION",
            "NO_SIN_STOCK_CD",
        ],
        "NO_CD_INSUFICIENTE",
    )
    from forusight.engine.reasons import DESCRIPCION_CODIGOS

    maximo = pd.to_numeric(d[MAX], errors="coerce").fillna(0)
    texto = [
        f"{DESCRIPCION_CODIGOS.get(c, c)}. Posición {int(p)}, máximo {int(m)}, CD {int(k)}."
        for c, p, m, k in zip(codigo, pos, maximo, cd, strict=True)
    ]
    det = pd.DataFrame(
        {
            "run_id": run_id,
            "tienda_id": tienda,
            "modelo_color_id": mc,
            "sku": d[SKU].astype("string"),
            "talla": d["Talla"].astype("string"),
            "estado_mc": estado_mc,
            "estado_sku": estado_sku,
            "stock_tienda": fisico,
            "stock_transito": pos - fisico,
            "venta_4s": v4,
            "venta_12s": v12,
            "demanda_semanal": _num(d.get(PRONOSTICO, 0)),
            "stock_objetivo": maximo.astype("int64"),
            "necesidad": nec,
            "stock_cd_disponible": cd,
            "cantidad": q,
            "afinidad": 1.0,
            "motivo_codigo": codigo,
            "motivo_texto": texto,
            "modelo_id": d.get("Código Modelo", d[SKU]).astype("string"),
            "minimo_exhibicion": 0,
            "color": d.get("Color", pd.Series("", index=d.index)).astype("string"),
            "categoria": d.get("Clase", pd.Series("NA", index=d.index))
            .astype("string")
            .fillna("NA"),
            "genero": d.get("Género", pd.Series("NA", index=d.index)).astype("string").fillna("NA"),
            "rango_precio": "NA",
            "talla_orden": 0.0,
        }
    )
    for c in COLUMNAS_SALIDA + COLUMNAS_EXTRA:
        if c not in det:
            det[c] = pd.NA
    det["motivo_reporte"] = mot
    resumen = {
        "run_id": run_id,
        "fecha_corte": pd.Timestamp(fecha_corte).date().isoformat(),
        "cd_id": cd_id,
        "filas_evaluadas": int(len(det)),
        "filas_con_envio": int((q > 0).sum()),
        "unidades_a_distribuir": int(q.sum()),
        "necesidad_total": int(nec.sum()),
        "tiendas_con_envio": int(det.loc[q > 0, "tienda_id"].nunique()),
        "stock_cd_disponible": int(d.drop_duplicates(SKU)[CD].pipe(_num).sum()),
        "iteraciones_asignacion": 1,
        "fill_rate": float(np.round(q.sum() / max(nec.sum(), 1), 4)),
    }
    return EngineResult(
        run_id=run_id,
        fecha_corte=pd.Timestamp(fecha_corte),
        params=params,
        detalle=det,
        mc=pd.DataFrame(),
        resumen=resumen,
    )


def tabla_para_archivo(
    reporte: pd.DataFrame, detalle: pd.DataFrame, cantidad: pd.Series | None
) -> pd.DataFrame:
    """Filas del reporte con cantidad, pendiente y motivo recalculados (o aprobados)."""
    out = reporte.copy()
    q = (cantidad if cantidad is not None else detalle["cantidad"]).to_numpy(dtype="int64")
    nec = detalle["necesidad"].to_numpy(dtype="int64")
    out[Q] = q
    out[P] = np.maximum(nec - q, 0)
    mot = detalle["motivo_reporte"].astype("string").to_numpy()
    out[MOT] = np.where(out[P].to_numpy() == 0, SIN_PENDIENTE, mot)
    pos = _num(out[FISICO]) + detalle["stock_transito"].to_numpy(dtype=float)
    out[POSICION] = pos
    pron = _num(out.get(PRONOSTICO, 0))
    if "Alcance Posición Stock Final [semanas]" in out:
        out["Alcance Posición Stock Final [semanas]"] = np.where(
            pron > 0, (pos + q) / pron.where(pron > 0, 1), 0
        )
    if "Monto Pedido Final [$]" in out and "Costo [$/un]" in out:
        out["Monto Pedido Final [$]"] = q * _num(out["Costo [$/un]"])
    wk = semanas(out)
    # VTA 2 SEM = última semana cerrada + semana en curso, como fórmula viva (igual al reporte)
    actual = "Demanda Periodo Actual"
    if "VTA 2 SEM" in out and wk and actual in out:
        from xlsxwriter.utility import xl_col_to_name

        a, b = (xl_col_to_name(list(out.columns).index(c)) for c in (wk[-1], actual))
        out["VTA 2 SEM"] = [f"={a}{i}+{b}{i}" for i in range(8, 8 + len(out))]
    return out


def coincidencia(reporte: pd.DataFrame, detalle: pd.DataFrame) -> dict:
    """Qué tanto coincide la cantidad de Forusight con la del reporte original, fila a fila."""
    q_rep = _num(reporte[Q]).to_numpy()
    q_fs = detalle["cantidad"].to_numpy(dtype=float)
    iguales = q_rep == q_fs
    return {
        "filas": int(len(iguales)),
        "filas_iguales": int(iguales.sum()),
        "pct_filas": float(iguales.mean()) if len(iguales) else 1.0,
        "unidades_reporte": int(q_rep.sum()),
        "unidades_forusight": int(q_fs.sum()),
    }
