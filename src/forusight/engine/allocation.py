"""Asignación del stock disponible del CD 320 por SKU.

Etapa 0  SKU con stock suficiente para todas las necesidades (y tienda sin tope activo):
         cada tienda recibe su necesidad.
Etapa 1  SKU escaso: filas en QUIEBRE con demanda reciben hasta su mínimo de exhibición
         (en orden de prioridad).
Etapa 2  Asignación marginal con heap, de a un múltiplo de envío por vez:
           prioridad = w1·riesgo_quiebre + w2·velocidad + w3·afinidad + w4·tendencia
                     + w5·importancia_comercial + w6·bono_curva_rota
         recalculada tras cada asignación (riesgo y bono dependen de la posición).
Restricciones duras: Σ asignado ≤ disponible CD por SKU, asignado ≤ necesidad,
asignado ≤ tope por tienda, múltiplo de envío. Desempate determinista por
(prioridad desc, tienda_id, sku).
Introducciones: si el CD no cubre la curva mínima, el MC no se introduce y sus
unidades se liberan para el resto (re-ejecución determinista).
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass

import numpy as np
import pandas as pd

from forusight.config.settings import EngineParams
from forusight.engine.common import KEY_MC, QUIEBRE

NO_CURVA_MINIMA_CD = "NO_CURVA_MINIMA_CD"
ETAPA_NINGUNA, ETAPA_SUFICIENTE, ETAPA_QUIEBRE, ETAPA_MARGINAL = 0, 1, 2, 3

COLUMNAS_REQUERIDAS = [
    "tienda_id",
    "sku",
    "modelo_color_id",
    "necesidad",
    "stock_disponible",
    "stock_transito",
    "stock_objetivo",
    "minimo_exhibicion",
    "es_core",
    "estado_mc",
    "estado_sku",
    "demanda_semanal",
    "demanda_sku",
    "afinidad",
    "factor_tendencia",
    "importancia_comercial",
    "talla_core_faltante",
    "es_introduccion",
    "requerido_curva",
    "req_curva",
]


class AsignacionInvalida(RuntimeError):
    """Se violó una restricción dura (nunca debería ocurrir)."""


@dataclass
class ResultadoAsignacion:
    filas: pd.DataFrame  # filas de entrada + cantidad, etapa, prioridad_inicial, flags
    pool_final: dict[str, int]
    cap_final: dict[str, int]
    iteraciones: int


def _componentes(f: pd.DataFrame, params: EngineParams) -> tuple[np.ndarray, np.ndarray]:
    """(parte estática de la prioridad, velocidad normalizada)."""
    w = params.asignacion.pesos_prioridad
    d = params.demanda
    vmax = f.groupby("sku", sort=False)["demanda_sku"].transform("max").to_numpy()
    vel = np.where(vmax > 0, f["demanda_sku"].to_numpy() / np.where(vmax > 0, vmax, 1), 0.0)
    rango = d.tendencia_max - d.tendencia_min
    tend = (
        np.clip((f["factor_tendencia"].to_numpy() - d.tendencia_min) / rango, 0, 1) if rango else 0
    )
    estatica = (
        w.velocidad * vel
        + w.afinidad * f["afinidad"].to_numpy()
        + w.tendencia * tend
        + w.importancia_comercial * f["importancia_comercial"].fillna(0).to_numpy()
    )
    return estatica, vel


def _prioridad_vec(estatica, pos, obj, faltante, params: EngineParams) -> np.ndarray:
    w = params.asignacion.pesos_prioridad
    riesgo = np.where(obj > 0, np.clip(1.0 - pos / np.where(obj > 0, obj, 1), 0, 1), 0.0)
    bono = (faltante & (pos <= 0)).astype(float)
    return np.round(estatica + w.riesgo_quiebre * riesgo + w.bono_curva_rota * bono, 12)


def _prioridad(
    estatica: float, pos: float, obj: float, faltante: bool, params: EngineParams
) -> float:
    w = params.asignacion.pesos_prioridad
    riesgo = min(max(1.0 - pos / obj, 0.0), 1.0) if obj > 0 else 0.0
    bono = 1.0 if (faltante and pos <= 0) else 0.0
    return round(estatica + w.riesgo_quiebre * riesgo + w.bono_curva_rota * bono, 12)


def _asignar_una_vez(
    f: pd.DataFrame, pool0: dict[str, int], cap0: dict[str, int], params: EngineParams
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, int], dict[str, int]]:
    m = params.asignacion.multiplo_envio
    n = len(f)
    pool = dict(pool0)
    cap = dict(cap0)
    tiendas = f["tienda_id"].to_numpy()
    skus = f["sku"].to_numpy()
    need = (f["necesidad"].to_numpy(dtype=np.int64) // m) * m  # asignado ≤ necesidad
    pos = np.array(f["stock_disponible"] + f["stock_transito"], dtype=float)  # copia mutable
    obj = f["stock_objetivo"].to_numpy(dtype=float)
    falt = f["talla_core_faltante"].to_numpy(dtype=bool)
    estatica, _ = _componentes(f, params)
    asig = np.zeros(n, dtype=np.int64)
    etapa = np.zeros(n, dtype=np.int64)
    prio0 = _prioridad_vec(estatica, pos, obj, falt, params)

    def dar(i: int, q: int, e: int) -> None:
        asig[i] += q
        pos[i] += q
        pool[skus[i]] -= q
        cap[tiendas[i]] -= q
        if etapa[i] == ETAPA_NINGUNA:
            etapa[i] = e

    # --- Etapa 0: suficiente
    s = pd.DataFrame({"t": tiendas, "s": skus, "need": need})
    dem_sku = s.groupby("s", sort=False)["need"].sum()
    dem_tienda = s.groupby("t", sort=False)["need"].sum()
    sku_ok = (s["s"].map(dem_sku) <= s["s"].map(pool).fillna(0)).to_numpy()
    tienda_ok = (s["t"].map(dem_tienda) <= s["t"].map(cap).fillna(0)).to_numpy()
    for i in np.flatnonzero(sku_ok & tienda_ok & (need > 0)):
        dar(i, int(need[i]), ETAPA_SUFICIENTE)

    # Orden determinista para etapas 1-2
    orden = sorted(
        (i for i in range(n) if need[i] - asig[i] >= m),
        key=lambda i: (-prio0[i], tiendas[i], skus[i]),
    )

    # --- Etapa 1: quiebres con demanda hasta el mínimo de exhibición (SKU escaso)
    quiebre = (f["estado_mc"].eq(QUIEBRE) | f["estado_sku"].eq(QUIEBRE)).to_numpy() & (
        f["demanda_semanal"].to_numpy() > 0
    )
    minimo = f["minimo_exhibicion"].to_numpy(dtype=float)
    for i in orden:
        if sku_ok[i] or not quiebre[i]:
            continue
        gap = int(np.ceil(max(minimo[i] - pos[i], 0) / m) * m)
        q = min(gap, need[i] - asig[i], pool[skus[i]], cap[tiendas[i]])
        q = (q // m) * m
        if q > 0:
            dar(i, int(q), ETAPA_QUIEBRE)

    # --- Etapa 2: marginal con heap
    prio1 = _prioridad_vec(estatica, pos, obj, falt, params)
    heap = [(-prio1[i], tiendas[i], skus[i], i) for i in orden if need[i] - asig[i] >= m]
    heapq.heapify(heap)
    while heap:
        _, t, sk, i = heapq.heappop(heap)
        if need[i] - asig[i] < m or pool[sk] < m or cap[t] < m:
            continue
        dar(i, m, ETAPA_MARGINAL)
        if need[i] - asig[i] >= m:
            heapq.heappush(
                heap, (-_prioridad(estatica[i], pos[i], obj[i], falt[i], params), t, sk, i)
            )
    return asig, etapa, prio0, pool, cap


def verificar_restricciones(
    f: pd.DataFrame, cantidad: np.ndarray, pool0: dict[str, int], cap0: dict[str, int], m: int
) -> None:
    """Aserción explícita (no se desactiva con -O) de las restricciones duras."""
    cant = pd.Series(cantidad, index=f.index)
    por_sku = cant.groupby(f["sku"]).sum()
    exceso = {s: int(v) for s, v in por_sku.items() if v > pool0.get(s, 0)}
    if exceso:
        raise AsignacionInvalida(f"Σ asignado supera el disponible del CD: {exceso}")
    por_tienda = cant.groupby(f["tienda_id"]).sum()
    if any(v > cap0.get(t, 0) for t, v in por_tienda.items()):
        raise AsignacionInvalida("Σ asignado supera el tope de tienda")
    if (cant > f["necesidad"]).any():
        raise AsignacionInvalida("asignado > necesidad")
    if (cant % m != 0).any() or (cant < 0).any():
        raise AsignacionInvalida("asignado no es múltiplo de envío")


def asignar(
    filas: pd.DataFrame,
    disponible_cd: dict[str, int],
    tope_tienda: dict[str, int],
    params: EngineParams,
) -> ResultadoAsignacion:
    """Asigna el CD a las filas tienda×SKU. Determinista e independiente del orden de entrada."""
    faltan = [c for c in COLUMNAS_REQUERIDAS if c not in filas.columns]
    if faltan:
        raise ValueError(f"Faltan columnas para asignar: {faltan}")
    m = params.asignacion.multiplo_envio
    f = filas.sort_values(["tienda_id", "sku"], kind="mergesort").reset_index(drop=True)
    f["motivo_asignacion"] = ""
    pool0 = {str(k): int(v) for k, v in disponible_cd.items()}
    for s in f["sku"].unique():
        pool0.setdefault(s, 0)
    cap0 = {str(k): int(v) for k, v in tope_tienda.items()}
    for t in f["tienda_id"].unique():
        cap0.setdefault(t, 0)

    # Pre-chequeo de curva mínima: el CD, por sí solo, debe cubrir cada talla requerida.
    req = f["requerido_curva"].to_numpy(dtype=bool)
    pos = (f["stock_disponible"] + f["stock_transito"]).to_numpy(dtype=float)
    falta = np.maximum(f["req_curva"].to_numpy() - pos, 0)
    no_cubre = req & (
        (falta > f["sku"].map(pool0).to_numpy()) | (falta > f["necesidad"].to_numpy())
    )
    excluidos = set(map(tuple, f.loc[no_cubre, KEY_MC].drop_duplicates().to_numpy()))

    iteraciones = 0
    while True:
        iteraciones += 1
        clave = list(zip(f["tienda_id"], f["modelo_color_id"], strict=True))
        excl = np.array([k in excluidos for k in clave], dtype=bool)
        fi = f.copy()
        fi.loc[excl, "necesidad"] = 0
        asig, etapa, prio0, pool, cap = _asignar_una_vez(fi, pool0, cap0, params)
        # Introducciones que no alcanzaron la curva mínima
        nuevos = set()
        if req.any():
            ok = (pos + asig) >= f["req_curva"].to_numpy()
            fallo = req & ~ok & ~excl
            nuevos = set(map(tuple, f.loc[fallo, KEY_MC].drop_duplicates().to_numpy()))
        if not nuevos:
            break
        excluidos |= nuevos

    verificar_restricciones(fi, asig, pool0, cap0, m)
    f["cantidad"] = asig
    f["etapa"] = etapa
    f["prioridad_inicial"] = prio0
    f["excluido_curva_minima"] = excl
    f.loc[excl, "motivo_asignacion"] = NO_CURVA_MINIMA_CD
    sku_arr = f["sku"].to_numpy()
    t_arr = f["tienda_id"].to_numpy()
    f["cd_agotado"] = [pool[s] < m for s in sku_arr]
    f["tope_tienda_agotado"] = [cap[t] < m for t in t_arr]
    return ResultadoAsignacion(filas=f, pool_final=pool, cap_final=cap, iteraciones=iteraciones)
