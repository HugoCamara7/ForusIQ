import pandas as pd
import pytest

from forusight.engine.common import bloque_semana
from forusight.engine.demand import FUENTE_SIMILARES, demanda_historica
from forusight.engine.pipeline import ejecutar
from tests.builders import CORTE, Escenario, params


def _semanal(serie: dict[int, tuple[float, int]], p=None) -> pd.DataFrame:
    """serie: rel -> (unidades, dias)."""
    p = p or params()
    df = pd.DataFrame(
        [
            {
                "tienda_id": "T1",
                "modelo_color_id": "M1",
                "sku": "M1-38",
                "rel": r,
                "unidades": u,
                "dias_con_stock": d,
            }
            for r, (u, d) in serie.items()
        ]
    )
    df["bloque"] = bloque_semana(df["rel"], p)
    return df


def _d(serie, p=None):
    p = p or params()
    return demanda_historica(_semanal(serie, p), p).iloc[0]


def test_semanas_sin_exposicion_se_excluyen():
    serie = {r: (2.0, 7) for r in range(1, 7)} | {r: (0.0, 0) for r in range(7, 13)}
    assert _d(serie)["tasa_12s"] == pytest.approx(2.0)  # no 1.0


def test_correccion_exposicion_parcial_y_tope():
    # 3.5 días → factor 2 (1/0.5); 1 día → 1/max(0.14, 0.3)=3.33, tope 2
    assert _d({r: (1.0, 7) for r in range(1, 13)})["tasa_12s"] == pytest.approx(1.0)
    r = _d({5: (1.0, 3)})  # 3 días: expo 0.4286 → factor 2.333 → tope 2
    assert r["tasa_12s"] == pytest.approx(2.0)
    r = _d({5: (1.0, 5)})  # 5 días: factor 1.4
    assert r["tasa_12s"] == pytest.approx(1.4)


def test_pesos_planos_sin_volumen():
    # 6 pares en total (< 8): tasa plana aunque haya tendencia fuerte
    serie = {1: (2.0, 7), 2: (2.0, 7), 3: (1.0, 7)} | {r: (0.1, 7) for r in range(4, 13)}
    r = _d(serie)
    assert not r["tendencia_significativa"]
    assert r["factor_tendencia"] == 1.0
    assert r["demanda_historica"] == pytest.approx(r["tasa_12s"])


def test_pesos_y_tendencia_con_volumen():
    # 4S: 4/sem, 5-8S: 2/sem, 9-12S: 1/sem → tasa 12S = 7/3; ratio = 1.714 → factor 1.2
    serie = (
        {r: (4.0, 7) for r in range(1, 5)}
        | {r: (2.0, 7) for r in range(5, 9)}
        | {r: (1.0, 7) for r in range(9, 13)}
    )
    r = _d(serie)
    assert r["tendencia_significativa"]
    assert r["tendencia_ratio"] == pytest.approx(4 / (7 / 3))
    assert r["factor_tendencia"] == pytest.approx(1.2)
    ponderada = 0.5 * 4 + 0.3 * 2 + 0.2 * 1
    assert r["demanda_historica"] == pytest.approx(ponderada * 1.2)


def test_tendencia_negativa_acotada():
    serie = {r: (0.5, 7) for r in range(1, 5)} | {r: (3.0, 7) for r in range(5, 13)}
    r = _d(serie)
    assert r["factor_tendencia"] == pytest.approx(0.85)


def test_tendencia_no_significativa_usa_plano():
    serie = {r: (2.0, 7) for r in range(1, 13)}
    r = _d(serie)
    assert not r["tendencia_significativa"]
    assert r["demanda_historica"] == pytest.approx(2.0)


def test_quiebre_reciente_no_deprime_demanda():
    serie = {r: (3.0, 7) for r in range(4, 13)} | {1: (0.0, 0), 2: (0.0, 0), 3: (0.0, 0)}
    r = _d(serie)
    assert r["demanda_historica"] == pytest.approx(3.0)


def test_sin_historia_usa_similares_por_indice_por_07():
    e = Escenario()
    for t in ("T1", "T2", "T3"):
        e.tienda(t, cluster="A")
    nuevo = e.modelo("M1-NEG")
    otro = e.modelo("M2-NEG")  # misma categoría: define el índice de la tienda
    for s in nuevo:
        e.stock_cd(s, 50)
    e.venta_constante("T2", nuevo, unidades=1.0)
    e.venta_constante("T3", nuevo, unidades=3.0)
    for t in ("T1", "T2", "T3"):
        e.venta_constante(t, otro, unidades=1.0)
    # índice T1 = venta_cat(T1) / media(similares); T1 sólo vende M2 → 60 / media(120, 180)
    res = ejecutar(e.inputs(), params(), CORTE, run_id="r")
    m = res.mc.set_index(["tienda_id", "modelo_color_id"]).loc[("T1", "M1-NEG")]
    assert m["fuente_demanda"] == FUENTE_SIMILARES
    vel = (5 * 1.0 + 5 * 3.0) / 2  # pares/sem del MC (5 tallas) promedio de T2 y T3
    indice = 60 / ((120 + 240) / 2)  # T2 = 60+60, T3 = 180+60
    assert m["demanda_semanal"] == pytest.approx(vel * max(indice, 0.5) * 0.7)
