"""Nivel máximo y punto de reorden con la estructura del reporte de distribución (nivel_neo)."""

import numpy as np
import pandas as pd

from forusight.data import planificacion as PLAN
from forusight.engine.pipeline import ejecutar
from tests.builders import CORTE, Escenario, params

DIA = CORTE + pd.Timedelta(days=2)


def _escenario(stock38=3, stock39=4):
    """A-NEG en T1: tallas 38 y 39 venden 2/semana; la 40 nunca vendió ni tuvo stock.
    k 0,325 × venta 4 semanas (16) = 5,2 por semana, mitad por talla (2,6); cobertura 1 semana:
    nivel = ceil(2,6 + 0,84·√2,6) = 4, reorden 3."""
    e = Escenario()
    s38, s39, s40 = e.modelo("A-NEG", tallas=("38", "39", "40"))
    e.tienda("T1", nombre="HP T1")
    e.venta_constante("T1", [s38, s39], unidades=2)
    e.stock_tienda("T1", s38, stock38)
    e.stock_tienda("T1", s39, stock39)
    for s in (s38, s39, s40):
        e.stock_cd(s, 50)
    inp = e.inputs()
    inp.dim_tienda["leadtime_dias"] = 3.0
    inp.dim_tienda["revision_dias"] = 4.0
    return inp, (s38, s39, s40)


def _params(**nivel):
    return params(
        nivel_neo={"activo": True, "universo": "venta", **nivel},
        referencia={"reponer_venta_con_nivel_del_reporte": True},
        reposicion_venta={"solo_bajo_punto_reorden": True},
    )


def _maestro(filas, k=None):
    c = pd.DataFrame(filas, columns=["tienda_id", "sku", "smt", "nivel", "rop", "ue", "fecha"])
    kk = pd.DataFrame(k or [], columns=["categoria_k", "k", "fecha"])
    return PLAN.Maestro(c, kk, DIA)


def _det(inp, p, maestro=None):
    from dataclasses import replace

    inp = replace(inp, planificacion=maestro)
    return ejecutar(inp, p, CORTE).detalle.set_index("sku")


def test_nivel_y_gatillo_como_el_reporte():
    inp, (s38, s39, s40) = _escenario()
    d = _det(inp, _params())
    assert d.loc[s38, "stock_objetivo"] == 4 and d.loc[s39, "stock_objetivo"] == 4
    assert d.loc[s38, "cantidad"] == 1  # posición 3 <= reorden 3: pide hasta 4
    assert d.loc[s39, "cantidad"] == 0  # posición 4 > reorden 3: no pide
    assert np.isclose(d.loc[s38, "demanda_semanal"], 2.6)
    # la talla sin stock ni venta no se evalúa (el reporte no la lista)
    assert d.loc[s40, "stock_objetivo"] == 0 and d.loc[s40, "cantidad"] == 0


def test_clave_de_un_reporte_reciente_usa_su_nivel_y_reorden():
    inp, (_, s39, _) = _escenario()
    m = _maestro([("T1", s39, 1, 6, 5, 1, DIA - pd.Timedelta(days=1))])
    d = _det(inp, _params(dias_nivel_reporte=3), m)
    assert d.loc[s39, "stock_objetivo"] == 6 and d.loc[s39, "cantidad"] == 2
    # el mismo reporte con 10 días: vuelve la fórmula (el SMT de la clave sigue valiendo)
    m10 = _maestro([("T1", s39, 1, 6, 5, 1, DIA - pd.Timedelta(days=10))])
    assert _det(inp, _params(dias_nivel_reporte=3), m10).loc[s39, "stock_objetivo"] == 4


def test_smt_y_k_del_maestro():
    inp, (s38, _, _) = _escenario(stock38=0)
    m = _maestro(
        [("T1", s38, 8, 8, 7, 1, DIA - pd.Timedelta(days=20))],
        k=[("|ZAPATO||DAMA", 0.65, DIA)],
    )
    d = _det(inp, _params(), m)
    assert d.loc[s38, "stock_objetivo"] == 8  # SMT manda sobre la fórmula
    assert np.isclose(d.loc[s38, "demanda_semanal"], 0.65 * 16 / 2)  # k de la categoría


def test_unidad_de_empaque():
    inp, (s38, _, _) = _escenario()
    m = _maestro([("T1", s38, 1, 4, 3, 6, DIA - pd.Timedelta(days=1))])
    assert _det(inp, _params(dias_nivel_reporte=3), m).loc[s38, "cantidad"] == 6


def test_lo_vendido_se_repone_solo_bajo_el_punto_de_reorden():
    from dataclasses import replace

    inp, (s38, s39, _) = _escenario()
    vr = pd.DataFrame(
        {
            "tienda_id": ["T1", "T1"],
            "sku": [s38, s39],
            "venta_desde_ruta": [3.0, 3.0],
            "venta_post_corte": [0.0, 0.0],
        }
    )
    d = _det(replace(inp, venta_reciente=vr), _params())
    assert d.loc[s38, "cantidad"] == 3  # llegó al reorden: repone lo vendido (3 > 4 − 3)
    assert d.loc[s39, "cantidad"] == 0  # sobre el reorden: como el reporte, no envía


def test_maestro_desde_reporte_y_guardado():
    from forusight.data import reporte as R
    from tests.test_reporte import _fila

    df = pd.DataFrame(
        [
            _fila("1", "T1", "1")
            | {R.MAX: 4, R.ROP: 3, "Stock Mínimo Total": 2, "Prenda": "ZAP", "2026-09-21": 1},
            _fila("1", "T1", "2") | {"Stock Mínimo Total": 1, "Prenda": "ZAP", "2026-09-21": 1},
        ]
    )
    m = PLAN.desde_reporte(df, "2026-09-30")
    assert set(m.claves["sku"]) == {"1", "2"} and m.claves["smt"].tolist() == [2, 1]
    assert len(m.k) == 1 and np.isclose(
        m.k["k"].iloc[0], 1.0 / 8
    )  # pronóstico 1,0 / venta 4 semanas 8
    otro = PLAN.desde_reporte(df.assign(**{"Stock Mínimo Total": 5}), "2026-09-28")
    u = PLAN.combinar(otro, m)  # gana la fecha más reciente
    assert u.claves.set_index("sku").loc["1", "smt"] == 2
    r = PLAN.desde_bytes(PLAN.a_bytes(u))
    assert len(r.claves) == 2 and r.claves["sku"].tolist() == u.claves["sku"].tolist()
    assert len(PLAN.vigente(u, "2026-12-31", 30).claves) == 0


def test_tienda_sin_planificacion_toma_el_smt_de_su_espejo():
    """T1 no está en ningún reporte de Neogística. Sin espejo su SMT es la moda del SKU en las
    demás tiendas (8, tiendas grandes); con espejo toma el de la tienda parecida (1) y el nivel
    sale de su propia venta (4), como HP CHACARILLA y HP PLAZA ANGAMOS con HP LA MOLINA."""
    inp, (s38, _, _) = _escenario(stock38=0)
    f = DIA - pd.Timedelta(days=5)
    m = _maestro(
        [("T2", s38, 8, 9, 8, 1, f), ("T3", s38, 8, 9, 8, 1, f), ("ESP", s38, 1, 3, 2, 1, f)]
    )
    assert _det(inp, _params(), m).loc[s38, "stock_objetivo"] == 8
    tabla = pd.DataFrame({"tienda_id": ["T1"], "espejo_id": ["ESP"]})
    me = PLAN.con_espejos(m, tabla)
    fila = me.claves.loc[me.claves["tienda_id"].eq("T1")].iloc[0]
    assert fila["espejo"] == "ESP" and pd.isna(fila["nivel"])  # el nivel no se copia
    assert _det(inp, _params(), me).loc[s38, "stock_objetivo"] == 4


def test_el_espejo_no_pisa_una_tienda_con_claves_propias():
    f = DIA - pd.Timedelta(days=5)
    m = _maestro([("T1", "S", 5, 6, 5, 1, f), ("ESP", "S", 1, 2, 1, 1, f)])
    me = PLAN.con_espejos(m, pd.DataFrame({"tienda_id": ["T1"], "espejo_id": ["ESP"]}))
    assert len(me.claves) == 2 and "espejo" not in me.claves


def test_espejos_configurados():
    """Cada tienda con espejo falta en el maestro de planificación y su espejo está en él."""
    tabla, claves = PLAN.espejos(), PLAN.por_defecto().claves
    ids = set(claves["tienda_id"].astype(str))
    assert {"7", "30"} <= set(tabla["tienda_id"])
    assert not set(tabla["tienda_id"]) & ids
    assert set(tabla["espejo_id"]) <= ids
