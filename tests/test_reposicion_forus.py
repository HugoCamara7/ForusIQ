"""Reglas de reposición validadas contra el reporte de distribución de Forus (23/09/2026):
reponer lo vendido talla a talla, tallas en formato Forus (390 = 39) y prioridad Jockey."""

import pandas as pd

from forusight.engine.common import clave_talla
from forusight.engine.pipeline import ejecutar
from tests.builders import CORTE, Escenario, params


def _fila(res, tienda, sku):
    d = res.detalle
    return d[(d["tienda_id"] == tienda) & (d["sku"] == sku)].iloc[0]


def test_tallas_formato_forus_son_core():
    assert list(clave_talla(pd.Series(["390", "39", "075", "105", "M"]))) == [
        "39",
        "39",
        "7.5",
        "10.5",
        "M",
    ]
    e = Escenario()
    skus = e.modelo("A-NEG", tallas=("360", "370", "380", "390", "400"))
    e.tienda("T1")
    e.venta_constante("T1", skus, unidades=1)
    for s in skus:
        e.stock_tienda("T1", s, 2)
        e.stock_cd(s, 50)
    res = ejecutar(e.inputs(), params(), CORTE)
    core = res.detalle.set_index("talla")["es_core"]
    assert core["370"] and core["380"] and core["390"] and not core["360"]


def test_talla_vendida_que_quedo_en_cero_se_repone_aunque_venda_poco():
    e = Escenario()
    skus = e.modelo("A-NEG", tallas=("35", "36", "40"))  # ninguna es core
    e.tienda("T1")
    for rel in (2, 7, 11):  # 3 pares en 12 semanas: demanda baja
        e.venta("T1", skus[0], rel, 1)
    e.exposicion("T1", skus[1], range(1, 13))
    e.stock_tienda("T1", skus[0], 0)
    e.stock_tienda("T1", skus[1], 1)
    e.stock_tienda("T1", skus[2], 1)
    for s in skus:
        e.stock_cd(s, 20)
    res = ejecutar(e.inputs(), params(), CORTE)
    assert _fila(res, "T1", skus[0])["cantidad"] == 1  # se vendió y quedó en 0 → vuelve 1
    assert _fila(res, "T1", skus[1])["cantidad"] == 0  # ya tiene su mínimo


def test_jockey_recibe_primero_y_mas_cobertura_con_igual_demanda():
    e = Escenario()
    skus = e.modelo("A-NEG", tallas=("37", "38", "39"))
    e.tienda("T1", nombre="HP JOCKEY", importancia=0.3)
    e.tienda("T2", nombre="HP CHICLAYO", importancia=0.9)
    for t in ("T1", "T2"):
        e.venta_constante(t, skus, unidades=2)
        for s in skus:
            e.stock_tienda(t, s, 0)
    for s in skus:
        e.stock_cd(s, 3)  # no alcanza para las dos
    res = ejecutar(e.inputs(), params(), CORTE)
    d = res.detalle.groupby("tienda_id")[["cantidad", "stock_objetivo"]].sum()
    assert d.loc["T1", "cantidad"] > d.loc["T2", "cantidad"]
    assert d.loc["T1", "stock_objetivo"] > d.loc["T2", "stock_objetivo"]
