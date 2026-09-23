from forusight.engine.pipeline import ejecutar
from tests.builders import CORTE, Escenario, params


def test_afinidad_en_rango(resultado_sintetico):
    a = resultado_sintetico.mc["afinidad"]
    assert a.between(0, 1).all() and a.notna().all()
    for c in ("a1", "a2", "a3", "a4", "a5"):
        v = resultado_sintetico.mc[c].dropna()
        assert v.between(0, 1).all(), c


def _escenario():
    e = Escenario()
    for t in ("T1", "T2", "T3", "T4"):
        e.tienda(t, cluster="A")
    nuevo = e.modelo("M1-NEG")
    e.modelo("M1-ROJ", modelo_id="M1")
    for s in nuevo:
        e.stock_cd(s, 100)
    for t in ("T2", "T3", "T4"):
        e.venta_constante(t, nuevo, unidades=1)
    return e, nuevo


def test_penalizacion_tuvo_sin_venta():
    e, nuevo = _escenario()
    e.exposicion("T1", nuevo[2], semanas=range(1, 13))  # 84 días sin venta
    e.stock_tienda("T1", nuevo[2], 1)
    res = ejecutar(e.inputs(), params(), CORTE, run_id="r")
    m = res.mc.set_index(["tienda_id", "modelo_color_id"])
    assert m.loc[("T1", "M1-NEG"), "afinidad_penalizada"]
    assert m.loc[("T1", "M1-NEG"), "afinidad"] <= params().afinidad.penalizacion_sin_venta
    assert m.loc[("T2", "M1-NEG"), "afinidad"] > m.loc[("T1", "M1-NEG"), "afinidad"]


def test_afinidad_bajo_umbral_no_introduce_aunque_sobre_stock():
    e, _ = _escenario()
    res_alto = ejecutar(
        e.inputs(), params(afinidad={"umbral_introduccion": 0.99}), CORTE, run_id="r"
    )
    t1 = res_alto.detalle.query("tienda_id == 'T1'")
    assert t1["cantidad"].sum() == 0
    assert (t1["motivo_codigo"] == "NO_AFINIDAD_BAJA").all()

    res_bajo = ejecutar(
        e.inputs(), params(afinidad={"umbral_introduccion": 0.0}), CORTE, run_id="r"
    )
    t1 = res_bajo.detalle.query("tienda_id == 'T1'")
    assert t1["cantidad"].sum() > 0
    assert (t1.loc[t1["cantidad"] > 0, "motivo_codigo"] == "ENVIO_INTRODUCCION").all()


def test_presencia_historica_del_modelo_en_otro_color_sube_afinidad():
    e, _ = _escenario()
    e.venta_constante("T1", ["M1-ROJ-38"], unidades=1)  # T1 vende el mismo modelo en rojo
    e.tienda("T5", cluster="A")
    res = ejecutar(e.inputs(), params(), CORTE, run_id="r")
    m = res.mc.set_index(["tienda_id", "modelo_color_id"])
    assert m.loc[("T1", "M1-NEG"), "a5"] > m.loc[("T5", "M1-NEG"), "a5"]
