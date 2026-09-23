import numpy as np
import pandas as pd
import pytest

from forusight.engine.allocation import (
    ETAPA_QUIEBRE,
    ETAPA_SUFICIENTE,
    NO_CURVA_MINIMA_CD,
    AsignacionInvalida,
    asignar,
    verificar_restricciones,
)
from forusight.engine.pipeline import ejecutar
from tests.builders import CORTE, Escenario, params


def fila(tienda, sku, necesidad, **kw):
    base = {
        "tienda_id": tienda,
        "sku": sku,
        "modelo_color_id": sku.rsplit("-", 1)[0],
        "necesidad": necesidad,
        "stock_disponible": 0.0,
        "stock_transito": 0.0,
        "stock_objetivo": necesidad,
        "minimo_exhibicion": 1,
        "es_core": True,
        "estado_mc": "TUVO_Y_VENDE",
        "estado_sku": "TUVO_Y_VENDE",
        "demanda_semanal": 1.0,
        "demanda_sku": 1.0,
        "afinidad": 0.5,
        "factor_tendencia": 1.0,
        "importancia_comercial": 0.5,
        "talla_core_faltante": False,
        "es_introduccion": False,
        "requerido_curva": False,
        "req_curva": 0,
    }
    base.update(kw)
    return base


def _cant(res):
    return res.filas.set_index(["tienda_id", "sku"])["cantidad"]


GRANDE = 10**9


def test_si_alcanza_cada_tienda_recibe_su_necesidad():
    f = pd.DataFrame([fila("T1", "A-38", 3), fila("T2", "A-38", 4), fila("T3", "A-38", 2)])
    res = asignar(f, {"A-38": 9}, {"T1": GRANDE, "T2": GRANDE, "T3": GRANDE}, params())
    assert _cant(res).to_dict() == {("T1", "A-38"): 3, ("T2", "A-38"): 4, ("T3", "A-38"): 2}
    assert (res.filas["etapa"] == ETAPA_SUFICIENTE).all()


@pytest.mark.parametrize("seed", range(150))
def test_nunca_asignar_mas_que_el_stock_del_cd(seed):
    rng = np.random.default_rng(seed)
    n_t, n_s = int(rng.integers(1, 12)), int(rng.integers(1, 8))
    m = int(rng.choice([1, 1, 2, 3]))
    filas = []
    for t in range(n_t):
        for s in range(n_s):
            if rng.random() < 0.7:
                filas.append(
                    fila(
                        f"T{t:02d}",
                        f"M{s % 3}-{36 + s}",
                        int(rng.integers(0, 15)),
                        stock_disponible=float(rng.integers(0, 3)),
                        estado_mc=rng.choice(["QUIEBRE", "TUVO_Y_VENDE"]),
                        afinidad=float(rng.random()),
                        importancia_comercial=float(rng.random()),
                        demanda_sku=float(rng.random() * 3),
                        talla_core_faltante=bool(rng.random() < 0.2),
                    )
                )
    if not filas:
        return
    f = pd.DataFrame(filas)
    pool = {s: int(rng.integers(0, 30)) for s in f["sku"].unique()}
    caps = {t: int(rng.integers(0, 40)) for t in f["tienda_id"].unique()}
    res = asignar(f, pool, caps, params(asignacion={"multiplo_envio": m}))
    out = res.filas
    por_sku = out.groupby("sku")["cantidad"].sum()
    for s, q in por_sku.items():
        assert q <= pool[s], f"SKU {s}: {q} > {pool[s]}"
    assert (out.groupby("tienda_id")["cantidad"].sum() <= pd.Series(caps)).all()
    assert (out["cantidad"] <= out["necesidad"]).all()
    assert (out["cantidad"] % m == 0).all()
    # eficiencia: si queda stock y cupo, la necesidad restante no podía cubrirse
    for r in out.itertuples():
        restante = r.necesidad - r.cantidad
        if restante >= m:
            assert res.pool_final[r.sku] < m or res.cap_final[r.tienda_id] < m


def test_determinismo_independiente_del_orden_de_entrada():
    rng = np.random.default_rng(3)
    f = pd.DataFrame(
        [
            fila(f"T{t}", f"A-{s}", int(rng.integers(1, 6)), afinidad=0.5)
            for t in range(8)
            for s in (36, 37, 38)
        ]
    )
    pool = {"A-36": 7, "A-37": 5, "A-38": 30}
    caps = {f"T{t}": GRANDE for t in range(8)}
    a = asignar(f, pool, caps, params()).filas
    for seed in range(5):
        b = asignar(f.sample(frac=1, random_state=seed), pool, caps, params()).filas
        pd.testing.assert_frame_equal(a, b)


def test_desempate_determinista_por_tienda():
    f = pd.DataFrame([fila("T2", "A-38", 1), fila("T1", "A-38", 1)])
    res = asignar(f, {"A-38": 1}, {"T1": GRANDE, "T2": GRANDE}, params())
    assert _cant(res).to_dict() == {("T1", "A-38"): 1, ("T2", "A-38"): 0}


def test_etapa1_quiebre_recibe_minimo_antes_que_reposicion_prioritaria():
    f = pd.DataFrame(
        [
            # reposición con prioridad estática muy alta
            fila("T1", "A-38", 5, afinidad=1.0, importancia_comercial=1.0, demanda_sku=3.0),
            # quiebre con prioridad baja
            fila(
                "T2",
                "A-38",
                5,
                estado_mc="QUIEBRE",
                afinidad=0.0,
                importancia_comercial=0.0,
                demanda_sku=0.1,
            ),
        ]
    )
    res = asignar(f, {"A-38": 2}, {"T1": GRANDE, "T2": GRANDE}, params())
    q = _cant(res)
    assert q[("T2", "A-38")] == 1  # su mínimo de exhibición
    assert q[("T1", "A-38")] == 1
    assert res.filas.set_index("tienda_id").loc["T2", "etapa"] == ETAPA_QUIEBRE


def test_prioridad_se_recalcula_tras_cada_unidad():
    """Dos tiendas iguales salvo stock: la de menor cobertura recibe primero, pero no todo."""
    f = pd.DataFrame(
        [
            fila("T1", "A-38", 4, stock_objetivo=4, stock_disponible=0.0),
            fila("T2", "A-38", 2, stock_objetivo=4, stock_disponible=2.0),
        ]
    )
    res = asignar(f, {"A-38": 4}, {"T1": GRANDE, "T2": GRANDE}, params())
    q = _cant(res)
    assert q[("T1", "A-38")] == 3 and q[("T2", "A-38")] == 1


def test_multiplo_de_envio():
    f = pd.DataFrame([fila("T1", "A-38", 5), fila("T2", "A-38", 1), fila("T3", "A-38", 4)])
    res = asignar(
        f,
        {"A-38": 7},
        {t: GRANDE for t in ("T1", "T2", "T3")},
        params(asignacion={"multiplo_envio": 2}),
    )
    q = res.filas["cantidad"]
    assert (q % 2 == 0).all() and q.sum() <= 7
    assert _cant(res)[("T2", "A-38")] == 0  # necesidad 1 < múltiplo 2


def test_tope_por_tienda():
    f = pd.DataFrame([fila("T1", "A-36", 5), fila("T1", "A-37", 5), fila("T2", "A-36", 5)])
    res = asignar(f, {"A-36": 50, "A-37": 50}, {"T1": 6, "T2": GRANDE}, params())
    q = _cant(res)
    assert q[("T1", "A-36")] + q[("T1", "A-37")] == 6
    assert q[("T2", "A-36")] == 5


def test_introduccion_exige_curva_minima_en_cd_y_libera_unidades():
    intro = {
        "es_introduccion": True,
        "estado_mc": "NUNCA_TUVO",
        "estado_sku": "NUNCA_TUVO",
        "requerido_curva": True,
        "req_curva": 1,
        "afinidad": 0.9,
    }
    f = pd.DataFrame(
        [
            fila("T1", "N-37", 1, **intro),
            fila("T1", "N-38", 1, **intro),
            fila("T1", "N-39", 1, **intro),
            fila("T2", "N-37", 2),
        ]
    )
    # sin stock de la 39 → no se introduce y la 37 queda para T2
    res = asignar(f, {"N-37": 2, "N-38": 5, "N-39": 0}, {"T1": GRANDE, "T2": GRANDE}, params())
    q = _cant(res)
    assert q.loc["T1"].sum() == 0
    assert q[("T2", "N-37")] == 2
    assert (res.filas.query("tienda_id == 'T1'")["motivo_asignacion"] == NO_CURVA_MINIMA_CD).all()

    # con stock de todas las tallas core, se introduce la curva completa
    res = asignar(f, {"N-37": 3, "N-38": 5, "N-39": 1}, {"T1": GRANDE, "T2": GRANDE}, params())
    assert _cant(res).loc["T1"].tolist() == [1, 1, 1]


def test_introduccion_que_pierde_la_competencia_se_revierte_completa():
    intro = {
        "es_introduccion": True,
        "estado_mc": "NUNCA_TUVO",
        "estado_sku": "NUNCA_TUVO",
        "requerido_curva": True,
        "req_curva": 1,
        "afinidad": 0.0,
        "importancia_comercial": 0.0,
        "demanda_sku": 0.01,
    }
    f = pd.DataFrame(
        [
            fila("T1", "N-37", 1, **intro),
            fila("T1", "N-38", 1, **intro),
            fila("T2", "N-38", 1, afinidad=1.0, importancia_comercial=1.0, estado_mc="QUIEBRE"),
        ]
    )
    res = asignar(f, {"N-37": 1, "N-38": 1}, {"T1": GRANDE, "T2": GRANDE}, params())
    q = _cant(res)
    assert q[("T2", "N-38")] == 1
    assert q[("T1", "N-37")] == 0  # no se deja una curva incompleta
    assert res.iteraciones >= 2


def test_verificar_restricciones_detecta_violaciones():
    f = pd.DataFrame([fila("T1", "A-38", 5)])
    with pytest.raises(AsignacionInvalida):
        verificar_restricciones(f, np.array([3]), {"A-38": 2}, {"T1": GRANDE}, 1)
    with pytest.raises(AsignacionInvalida):
        verificar_restricciones(f, np.array([6]), {"A-38": 10}, {"T1": GRANDE}, 1)


def test_pipeline_nunca_supera_cd_con_disponible_neto():
    """Disponible = físico − reservado − comprometido."""
    e = Escenario()
    for t in ("T1", "T2", "T3"):
        e.tienda(t)
    skus = e.modelo("M1-NEG")
    for t in ("T1", "T2", "T3"):
        e.venta_constante(t, skus, unidades=2)
    for s in skus:
        e.stock_cd(s, fisico=10, reservado=3, comprometido=4)  # disponible 3
    res = ejecutar(e.inputs(), params(), CORTE, run_id="r")
    por_sku = res.detalle.groupby("sku")["cantidad"].sum()
    assert (por_sku <= 3).all() and por_sku.sum() > 0
    assert (res.detalle["stock_cd_disponible"] == 3).all()
