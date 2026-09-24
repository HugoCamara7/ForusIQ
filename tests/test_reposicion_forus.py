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


def _escenario_intro():
    e = Escenario()
    viejo = e.modelo("A-NEG", tallas=("37", "38", "39"))
    nuevo = e.modelo("B-NEG", tallas=("37", "38", "39"))
    for t, nombre in (("T1", "HP JOCKEY"), ("T2", "DH LURIN"), ("T3", "HP CHICLAYO")):
        e.tienda(t, nombre=nombre, importancia=0.5)
        e.venta_constante(t, viejo, unidades=1)
        for s in viejo:
            e.stock_tienda(t, s, 0)
    e.venta_constante("T3", nuevo, unidades=2)  # el modelo nuevo sólo se vende en T3
    for s in viejo + nuevo:
        e.stock_cd(s, 2)  # no alcanza para todas
    return e, nuevo


def test_por_defecto_no_se_introducen_modelos_nuevos():
    e, nuevo = _escenario_intro()
    p = params(afinidad={"introducir_modelos_nuevos": False})
    d = ejecutar(e.inputs(), p, CORTE).detalle
    intro = d[d["sku"].isin(nuevo) & d["tienda_id"].isin(["T1", "T2"])]
    assert intro["cantidad"].sum() == 0
    assert set(intro["motivo_codigo"]) == {"NO_INTRODUCCION"}


def test_liquidadora_ultima_en_el_reparto_y_sin_introducciones():
    e, nuevo = _escenario_intro()
    d = ejecutar(e.inputs(), params(), CORTE).detalle  # introducciones activadas
    por_t = d.groupby("tienda_id")["cantidad"].sum()
    assert por_t["T2"] < por_t["T1"]
    assert d[(d["tienda_id"] == "T2") & d["sku"].isin(nuevo)]["cantidad"].sum() == 0


def test_no_se_llena_una_talla_que_la_tienda_nunca_tuvo_ni_vendio():
    e = Escenario()
    skus = e.modelo("A-NEG", tallas=("37", "38", "39", "40"))
    e.tienda("T1", nombre="HP JOCKEY")
    e.venta_constante("T1", skus[:3], unidades=2)  # la talla 40 nunca se vendió aquí
    for s in skus[:3]:
        e.stock_tienda("T1", s, 0)
    for s in skus:
        e.stock_cd(s, 20)
    d = ejecutar(e.inputs(), params(), CORTE).detalle.set_index("sku")
    assert d.loc[skus[3], "cantidad"] == 0
    assert d.loc[skus[3], "motivo_codigo"] == "NO_TALLA_NUNCA_TUVO"
    assert (d.loc[skus[:3], "cantidad"] > 0).all()  # lo vendido sí se repone
