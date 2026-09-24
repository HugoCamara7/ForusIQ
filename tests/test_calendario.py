"""Calendario de reposición, prioridad A/B/C, cobertura por tienda y modelo agotado."""

import pandas as pd

from forusight.data import calendario as CAL
from forusight.data.fuentes import inferir_exposicion
from forusight.engine.pipeline import ejecutar
from tests.builders import CORTE, Escenario, params


def _escenario():
    e = Escenario()
    skus = e.modelo("A-NEG", tallas=("38", "39"))
    for tid, nombre in (("8", "HP JOCKEY"), ("23", "HP CHORRILLOS"), ("999", "HP NUEVA")):
        e.tienda(tid, nombre=nombre)
        e.venta_constante(tid, skus, unidades=1)
        for s in skus:
            e.stock_tienda(tid, s, 0)
    for s in skus:
        e.stock_cd(s, 50)
    return e


def test_calendario_de_los_reportes():
    cal = CAL.calendario().set_index("codigo_tienda")
    assert cal.loc["8", "dias"] == "LU,MI,VI" and cal.loc["23", "dias"] == "JU"
    assert cal.loc["43", "revision_dias"] == 3.5 and cal.loc["129", "revision_dias"] == 7.0
    assert CAL.dia_semana("2026-09-24") == "JU"


def test_solo_reciben_las_tiendas_que_reponen_ese_dia():
    e = _escenario()
    p = params()
    inp = e.inputs()
    jueves = CAL.aplicar(inp.dim_tienda, "2026-09-24", p, ["HUSH PUPPIES"])
    assert jueves.set_index("tienda_id")["recibe_hoy"].to_dict() == {
        "8": False,  # Jockey: lunes, miércoles y viernes
        "23": True,  # Chorrillos: jueves
        "999": True,  # sin calendario: recibe cualquier día
    }
    inp.dim_tienda = jueves
    d = ejecutar(inp, p, CORTE).detalle
    assert set(d["tienda_id"]) == {"23", "999"}  # el resultado sólo lista las de hoy


def test_prioridad_a_b_c_y_cobertura_propia_de_la_tienda():
    e = _escenario()
    p = params(cobertura={"seguridad_semanas": {"alta": 0.0, "media": 0.0, "baja": 0.0}})
    inp = e.inputs()
    inp.dim_tienda = CAL.aplicar(inp.dim_tienda, "2026-09-23", p, ["HUSH PUPPIES"])  # miércoles
    t = inp.dim_tienda.set_index("tienda_id")
    assert t.loc["8", "prioridad"] == "A" and t.loc["23", "prioridad"] == "C"
    assert t.loc["8", "leadtime_dias"] == 3.0 and t.loc["8", "revision_dias"] == 2.33
    res = ejecutar(inp, p, CORTE)
    mc = res.mc.set_index("tienda_id")
    # Jockey (A): (3 + 2,33) / 7 semanas × 1,25
    cob = mc.loc[["8"], "cobertura_semanas"].iloc[0]
    assert abs(cob - (3 + 2.33) / 7 * 1.25) < 1e-6


def test_modelo_agotado_sin_venta_reciente_no_se_repone():
    e = Escenario()
    skus = e.modelo("A-NEG", tallas=("38", "39"))
    e.tienda("T1", nombre="HP T1")
    for rel in (9, 10, 11):  # vendió hace 2-3 meses y se agotó
        e.venta("T1", skus[0], rel, 1)
    for s in skus:
        e.stock_tienda("T1", s, 0)
        e.stock_cd(s, 20)
    d = ejecutar(e.inputs(), params(), CORTE).detalle.set_index("sku")
    assert d["cantidad"].sum() == 0
    assert d.loc[skus[0], "motivo_codigo"] == "NO_MODELO_AGOTADO"


def test_modelo_nuevo_se_mide_desde_su_primera_venta():
    semanas = [pd.Timestamp("2026-06-29") + pd.Timedelta(weeks=i) for i in range(12)]
    ventas = pd.DataFrame(
        {"semana_inicio": [semanas[-2]], "tienda_id": ["1"], "sku": ["A38"], "unidades": [1.0]}
    )
    stock = pd.DataFrame(
        {"tienda_id": ["1", "1"], "sku": ["A38", "A39"], "stock_disponible": [1.0, 2.0]}
    )
    mc = pd.Series(["A", "A"], index=["A38", "A39"])
    sin = inferir_exposicion(ventas, stock, semanas)
    con = inferir_exposicion(ventas, stock, semanas, mc)
    expo = lambda df: int((df.loc[df["sku"] == "A39", "dias_con_stock"] > 0).sum())  # noqa: E731
    assert expo(sin) == 12 and expo(con) == 2  # llegó con el modelo, hace 2 semanas
