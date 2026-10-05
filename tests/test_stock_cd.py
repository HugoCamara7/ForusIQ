"""El stock del CD nunca se sobrepasa; el tránsito sale de los pedidos del sistema."""

import numpy as np
import pandas as pd

from forusight.data import reporte as R
from forusight.data import transito as TR
from forusight.engine.pipeline import ejecutar
from tests.builders import CORTE, Escenario, params
from tests.test_reporte import _excel, _fila


def _diez_unidades(tiendas=6):
    e = Escenario()
    skus = e.modelo("A-NEG", tallas=("38",))
    for i in range(tiendas):
        e.tienda(f"T{i}", nombre=f"HP TIENDA {i}")
        e.venta_constante(f"T{i}", skus, unidades=3)
        e.stock_tienda(f"T{i}", skus[0], 0)
    e.stock_cd(skus[0], 10)
    return e, skus[0]


def test_diez_unidades_nunca_se_reparten_de_mas():
    e, sku = _diez_unidades()
    d = ejecutar(e.inputs(), params(), CORTE).detalle
    assert d["necesidad"].sum() > 10  # todas piden
    assert d.loc[d["sku"] == sku, "cantidad"].sum() == 10


def test_transito_de_pedidos_reduce_la_necesidad_de_la_tienda():
    e, sku = _diez_unidades()
    inp = e.inputs()
    inp.pedidos = pd.DataFrame(
        {
            "tienda_id": ["T0", "T0"],
            "sku": [sku, sku],
            "estado": pd.array([2, 6], dtype="Int64"),  # en picking; en transporte = recibido
            "clasificacion": pd.array([1, 1], dtype="Int64"),
            "recibido_post_corte": [False, False],
            "unidades": [6.0, 9.0],
        }
    )
    p = params()
    assert TR.usa_pedidos(inp, p)
    con = TR.aplicar_pedidos(inp, p)
    st = con.stock_tienda.set_index("tienda_id")
    assert st.loc["T0", "stock_transito"] == 6 and st["stock_transito"].sum() == 6
    sin = ejecutar(inp, p, CORTE).detalle.set_index("tienda_id")
    d = ejecutar(con, p, CORTE).detalle.set_index("tienda_id")
    assert d.loc["T0", "stock_transito"] == 6
    assert d.loc["T0", "necesidad"] < sin.loc["T0", "necesidad"]
    # el CD se reparte tal cual: el tránsito no lo descuenta
    assert con.stock_cd.equals(inp.stock_cd)


def test_escenarios_aleatorios_respetan_el_stock_del_cd():
    rng = np.random.default_rng(7)
    for _ in range(25):
        e = Escenario()
        skus = e.modelo("A-NEG", tallas=("37", "38", "39"))
        for i in range(int(rng.integers(2, 8))):
            e.tienda(f"T{i}", nombre=f"HP T{i}")
            e.venta_constante(f"T{i}", skus, unidades=float(rng.integers(0, 4)))
            for s in skus:
                e.stock_tienda(f"T{i}", s, float(rng.integers(0, 3)))
        cd = {s: int(rng.integers(0, 12)) for s in skus}
        for s, q in cd.items():
            e.stock_cd(s, q)
        d = ejecutar(e.inputs(), params(), CORTE).detalle
        enviado = d.groupby("sku")["cantidad"].sum()
        assert all(enviado.get(s, 0) <= q for s, q in cd.items())


def test_stock_cd_por_componente_y_comparacion_con_reporte():
    from forusight.engine.universe import preparar_cd

    cd = pd.DataFrame(
        {
            "sku": ["1", "2"],
            "fisico": [30.0, 5.0],
            "reservado": 0.0,
            "comprometido": 0.0,
            "cd_stock_tiendas": [10.0, 5.0],
            "cd_stock_bodega": [20.0, 0.0],
        }
    )
    p = params()
    assert preparar_cd(cd, p)["disponible"].tolist() == [30, 5]
    p.stock_cd.componentes = "tiendas"
    assert preparar_cd(cd, p)["disponible"].tolist() == [10, 5]
    # la columna «disponible» de stock_bi (ya sin reservas) es la opción por defecto
    p.stock_cd.componentes = "disponible"
    cd["cd_stock_disponible"] = [12.0, 4.0]
    assert preparar_cd(cd, p)["disponible"].tolist() == [12, 4]
    rep, _ = R.leer_reporte(
        _excel(
            [_fila("8", "HP JOCKEY", "1", **{R.CD: 10}), _fila("8", "HP JOCKEY", "2", **{R.CD: 5})]
        )
    )
    resumen, detalle = R.comparar_stock_cd(cd, rep)
    assert resumen.iloc[0]["opcion"] == "tiendas" and resumen.iloc[0]["sku_iguales"] == 1.0
    assert len(detalle) == 2
