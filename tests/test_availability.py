import numpy as np

from forusight.engine import common as C
from forusight.engine.availability import clasificar
from forusight.engine.pipeline import ejecutar
from tests.builders import CORTE, Escenario, params


def _estado_mc(res, tienda, mc):
    m = res.mc
    return m.loc[(m.tienda_id == tienda) & (m.modelo_color_id == mc), "estado_mc"].iat[0]


def _escenario_base():
    e = Escenario()
    for t in ("T1", "T2", "T3", "T4", "T5"):
        e.tienda(t)
    skus = e.modelo("M1-NEG")
    for s in skus:
        e.stock_cd(s, 20)
    return e, skus


def test_clasificar_reglas_basicas():
    venta = np.array([5, 5, 0, 0, 0, 0])
    dias = np.array([60, 60, 0, 0, 10, 30])
    stock = np.array([3, 0, 0, 2, 0, 1])
    bajo = np.zeros(6, dtype=bool)
    est = clasificar(venta, dias, stock, bajo, 14)
    assert list(est) == [
        C.TUVO_Y_VENDE,
        C.QUIEBRE,
        C.NUNCA_TUVO,
        C.EXPOSICION_INSUFICIENTE,
        C.EXPOSICION_INSUFICIENTE,
        C.TUVO_SIN_VENTA,
    ]


def test_quiebre_vs_nunca_tuvo():
    """Ambas tiendas tienen stock 0 hoy; sólo la que vendía está en QUIEBRE."""
    e, skus = _escenario_base()
    e.venta_constante("T1", skus, semanas=range(3, 13))  # vendía, 2 últimas semanas sin stock
    res = ejecutar(e.inputs(), params(), CORTE, run_id="r")
    assert _estado_mc(res, "T1", "M1-NEG") == C.QUIEBRE
    assert _estado_mc(res, "T2", "M1-NEG") == C.NUNCA_TUVO
    det = res.detalle.set_index(["tienda_id", "sku"])
    assert (det.loc["T1", "estado_sku"] == C.QUIEBRE).all()
    # el quiebre recibe envío por quiebre, no se trata como introducción
    assert (det.loc["T1", "motivo_codigo"] == "ENVIO_QUIEBRE").any()
    assert not det.loc["T1", "es_introduccion"].any()


def test_quiebre_por_bajo_minimo_de_exhibicion():
    e, skus = _escenario_base()
    e.venta_constante("T1", skus)
    # Stock sólo en la talla 36 (no core): todas las tallas core bajo mínimo → QUIEBRE
    e.stock_tienda("T1", skus[0], 5)
    res = ejecutar(e.inputs(), params(), CORTE, run_id="r")
    assert _estado_mc(res, "T1", "M1-NEG") == C.QUIEBRE


def test_curva_rota_no_es_quiebre():
    e, skus = _escenario_base()
    e.venta_constante("T1", skus)
    for s in skus:
        e.stock_tienda("T1", s, 3)
    e.stock_tienda("T1", "M1-NEG-38", 0)  # falta 1 de 3 tallas core
    res = ejecutar(e.inputs(), params(), CORTE, run_id="r")
    assert _estado_mc(res, "T1", "M1-NEG") == C.TUVO_Y_VENDE
    fila = res.detalle.set_index(["tienda_id", "sku"]).loc[("T1", "M1-NEG-38")]
    assert fila["curva_rota"] and fila["talla_core_faltante"]
    assert fila["cantidad"] > 0 and fila["motivo_codigo"] in ("ENVIO_CURVA_ROTA", "ENVIO_QUIEBRE")


def test_exposicion_insuficiente_vs_tuvo_sin_venta():
    e, skus = _escenario_base()
    e.exposicion("T1", skus[2], semanas=[1, 2], dias=5)  # 10 días, sin venta
    e.stock_tienda("T1", skus[2], 1)
    e.exposicion("T2", skus[2], semanas=[1, 2, 3], dias=7)  # 21 días, sin venta
    e.stock_tienda("T2", skus[2], 1)
    res = ejecutar(e.inputs(), params(), CORTE, run_id="r")
    assert _estado_mc(res, "T1", "M1-NEG") == C.EXPOSICION_INSUFICIENTE
    assert _estado_mc(res, "T2", "M1-NEG") == C.TUVO_SIN_VENTA


def test_cero_ventas_solo_es_sin_demanda_en_tuvo_sin_venta():
    e, skus = _escenario_base()
    for t in ("T3", "T4"):  # referencias que venden
        e.venta_constante(t, skus)
        for s in skus:
            e.stock_tienda(t, s, 4)
    e.exposicion("T1", skus[2], semanas=[1], dias=7)  # 7 días: insuficiente
    e.stock_tienda("T1", skus[2], 1)
    e.exposicion("T2", skus[2], semanas=range(1, 13), dias=7)  # 84 días: evidencia de no-demanda
    e.stock_tienda("T2", skus[2], 1)
    res = ejecutar(e.inputs(), params(), CORTE, run_id="r")
    m = res.mc.set_index(["tienda_id", "modelo_color_id"])
    assert m.loc[("T1", "M1-NEG"), "demanda_semanal"] > 0  # no se lee el 0 como sin demanda
    assert m.loc[("T2", "M1-NEG"), "demanda_semanal"] == 0
    assert (res.detalle.query("tienda_id == 'T2'")["motivo_codigo"] == "NO_SIN_DEMANDA").all()


def test_stock_sin_historia_es_exposicion_insuficiente():
    e, skus = _escenario_base()
    e.stock_tienda("T1", skus[1], 2)  # recién llegado, sin días registrados
    res = ejecutar(e.inputs(), params(), CORTE, run_id="r")
    assert _estado_mc(res, "T1", "M1-NEG") == C.EXPOSICION_INSUFICIENTE
