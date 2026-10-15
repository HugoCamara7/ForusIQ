import numpy as np
import pytest

from forusight.engine.pipeline import ejecutar
from tests.builders import CORTE, Escenario, params


def _det(res, tienda):
    return res.detalle.query("tienda_id == @tienda").set_index("talla")


def _base():
    e = Escenario()
    for t in ("T1", "T2", "T3"):
        e.tienda(t)
    skus = e.modelo("M1-NEG")
    for s in skus:
        e.stock_cd(s, 100)
    return e, skus


def test_necesidad_es_objetivo_menos_stock_y_transito(resultado_sintetico):
    d = resultado_sintetico.detalle
    libres = d.loc[
        d["motivo_codigo"].isin(
            ["ENVIO_REPOSICION", "ENVIO_QUIEBRE", "NO_SIN_NECESIDAD", "NO_SIN_STOCK_CD"]
        )
    ]
    esperado = np.maximum(
        0, libres["stock_objetivo"] - np.ceil(libres["stock_tienda"] + libres["stock_transito"])
    )
    esperado = np.minimum(esperado, params().tope_tienda.max_unidades_por_sku)
    assert (libres["necesidad"] == esperado).all()


def test_minimo_de_exhibicion_en_tallas_core():
    e, skus = _base()
    e.venta_constante("T1", skus, unidades=0.2, semanas=range(1, 13))  # demanda baja
    for s in skus:
        e.stock_tienda("T1", s, 0)
    res = ejecutar(e.inputs(), params(), CORTE, run_id="r")
    d = _det(res, "T1")
    core = d.loc[d["es_core"]]
    assert (core["stock_objetivo"] >= 1).all()
    assert set(core.index) == {"37", "38", "39"}


def test_transito_reduce_necesidad():
    e, skus = _base()
    e.venta_constante("T1", skus, unidades=2)
    for s in skus:
        e.stock_tienda("T1", s, 1, transito=5)
    res = ejecutar(e.inputs(), params(), CORTE, run_id="r")
    d = _det(res, "T1")
    assert (d["necesidad"] == np.maximum(0, d["stock_objetivo"] - 6)).all()


def test_sobrestock_necesidad_cero_y_marcado():
    e, skus = _base()
    e.venta_constante("T1", skus, unidades=0.5)
    for s in skus:
        e.stock_tienda("T1", s, 40)
    res = ejecutar(e.inputs(), params(), CORTE, run_id="r")
    d = _det(res, "T1")
    assert d["sobrestock"].all()
    assert (d["necesidad"] == 0).all()
    assert (d["motivo_codigo"] == "NO_SOBRESTOCK").all()


def test_tope_por_sku():
    e, skus = _base()
    e.venta_constante("T1", skus, unidades=30)
    res = ejecutar(e.inputs(), params(tope_tienda={"max_unidades_por_sku": 4}), CORTE, run_id="r")
    d = _det(res, "T1")
    assert (d["necesidad"] <= 4).all() and (d["necesidad_bruta"] > 4).any()
    assert d["motivo_texto"].str.contains("máximo por SKU").any()


def test_tope_por_tienda_desde_dimension():
    e, skus = _base()
    e.tiendas[0]["max_unidades_corrida"] = 5
    e.venta_constante("T1", skus, unidades=5)
    res = ejecutar(e.inputs(), params(), CORTE, run_id="r")
    assert _det(res, "T1")["cantidad"].sum() == 5


def test_cobertura_por_categoria_y_rotacion():
    p = params(cobertura={"por_categoria": {"ZAPATO": {"lead_time_semanas": 3}}})
    assert p.cobertura.para("ZAPATO", "alta")[0] == pytest.approx(3 + 1 + 1.0)
    assert p.cobertura.para("BOTA", "baja")[0] == pytest.approx(1 + 1 + 0.5)


def test_rotacion_por_percentil_dentro_de_categoria(resultado_sintetico):
    mc = resultado_sintetico.mc
    for _, g in mc.drop_duplicates("modelo_color_id").groupby("categoria"):
        if g["sell_through"].nunique() < 3:
            continue
        alta = g.loc[g["rotacion"] == "alta", "sell_through"]
        baja = g.loc[g["rotacion"] == "baja", "sell_through"]
        if len(alta) and len(baja):
            assert alta.min() > baja.max()


def test_curva_rota_da_bonificacion_de_prioridad():
    e, skus = _base()
    for t in ("T1", "T2"):
        e.venta_constante(t, skus, unidades=1)
        for s in skus:
            e.stock_tienda(t, s, 2)
    e.stock_tienda("T1", "M1-NEG-38", 0)  # T1: talla core faltante
    e.stock_tienda("T2", "M1-NEG-38", 1)  # T2: tiene 1
    e.stock_cd("M1-NEG-38", 1)
    res = ejecutar(e.inputs(), params(), CORTE, run_id="r")
    d = res.detalle.set_index(["tienda_id", "talla"])
    assert d.loc[("T1", "38"), "talla_core_faltante"]
    assert d.loc[("T1", "38"), "cantidad"] == 1
    assert d.loc[("T2", "38"), "cantidad"] == 0


def _talla_con_stock_sin_venta(venta_semana_actual: float = 0.0):
    """HP1-NEG: la 39 vende; la 38 tiene 1 par y no vendió nada en 12 semanas. El nivel del
    reporte (stock mínimo 2) le pide subir a 2."""
    import pandas as pd

    from forusight.engine.pipeline import ejecutar
    from tests.builders import CORTE, Escenario, params

    e = Escenario()
    s38, s39 = e.modelo("HP1-NEG", tallas=("38", "39"))
    e.tienda("8", nombre="HP JOCKEY")
    e.venta_constante("8", [s39], unidades=1)
    e.exposicion("8", s38, range(1, 13))
    if venta_semana_actual:
        e.venta("8", s38, 0, venta_semana_actual)
    e.stock_tienda("8", s38, 1)
    e.stock_tienda("8", s39, 0)
    e.stock_cd(s38, 20)
    e.stock_cd(s39, 20)
    inp = e.inputs()
    inp.niveles_ref = pd.DataFrame(
        {"tienda_id": ["8", "8"], "sku": [s38, s39], "nivel_ref": [2.0, 2.0], "rop_ref": [1.0, 1.0]}
    )
    p = params(calendario={"aplicar": False}, nivel_neo={"activo": False})
    return ejecutar(inp, p, CORTE).detalle.set_index("sku"), s38, s39


def test_talla_con_stock_y_sin_venta_no_se_repone():
    """Crítico: una talla con stock que no vendió nada no se repone aunque el stock mínimo
    (nivel 2, punto de reorden 1) lo pida. La talla que sí vende se repone igual."""
    d, s38, s39 = _talla_con_stock_sin_venta()
    assert d.loc[s38, "cantidad"] == 0
    assert d.loc[s38, "motivo_codigo"] == "NO_CON_STOCK_SIN_VENTA"
    assert "no vendió nada" in d.loc[s38, "motivo_texto"]
    assert d.loc[s39, "cantidad"] > 0


def test_talla_con_stock_que_vendio_esta_semana_si_se_repone():
    """Vendió en la semana en curso (fuera de las 12 semanas cerradas): sí se repone."""
    d, s38, _ = _talla_con_stock_sin_venta(venta_semana_actual=1)
    assert d.loc[s38, "cantidad"] == 1


def test_regla_sin_venta_se_puede_apagar():
    from forusight.config.settings import EngineParams

    assert EngineParams().reposicion_venta.no_reponer_con_stock_sin_venta
