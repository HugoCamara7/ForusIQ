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
    assert cal.loc["8", "mall"] == "Jockey Plaza" and cal.loc["23", "mall"] == "Chorrillos"
    assert cal.loc["43", "revision_dias"] == 3.5 and cal.loc["129", "revision_dias"] == 7.0
    assert CAL.dia_semana("2026-09-24") == "JU"


def test_rutas_por_mall():
    cal = CAL.calendario().set_index("codigo_tienda")
    # todas las tiendas del mismo mall comparten ruta, sea cual sea la cadena
    pn = cal.loc[["44", "46", "113", "149"], "mall"]
    assert set(pn) == {"Plaza Norte"} and CAL.dias_de_mall("Plaza Norte") == "MA,JU"
    assert CAL.dias_de_mall(cal.loc["97", "mall"]) == "MA,VI"  # Salaverry
    assert CAL.dias_de_mall(cal.loc["7", "mall"]) == "MA"  # Chacarilla
    assert CAL.dias_de_mall(cal.loc["61", "mall"]) == "LU,MI,VI"  # provincia
    assert cal.loc["61", "revision_dias"] == 2.33  # Cusco: 3 despachos por semana
    assert set(cal.loc[["143", "152", "129"], "mall"]) == {"La Molina"}  # misma ruta
    assert CAL.mall_de("CC MEGAPLAZA") == "Mega Plaza"
    assert CAL.mall_de("", "PROVINCIA") == "Provincia"
    assert CAL.mall_de("HP NUEVA") == ""
    dim = pd.DataFrame(
        {
            "tienda_id": ["900", "901", "902"],
            "nombre": ["HP NUEVA 1", "HP NUEVA 2", "HP NUEVA 3"],
            "centro_comercial": ["MEGA PLAZA", None, None],
            "zona": [None, "PROVINCIA", None],
        }
    )
    t = CAL.aplicar(dim, "2026-09-22", params(), None).set_index("tienda_id")  # martes
    assert t.loc["900", "mall"] == "Mega Plaza" and t.loc["900", "recibe_hoy"]
    assert t.loc["900", "revision_dias"] == 3.5
    assert t.loc["901", "mall"] == "Provincia" and not t.loc["901", "recibe_hoy"]
    assert t.loc["902", "recibe_hoy"]  # sin mall con ruta: cualquier día


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


def test_reponer_la_venta_desde_la_ultima_ruta():
    """Vendió 10 ayer y 15 hoy; mañana tiene ruta: se reponen las 25 (si el CD las tiene)."""
    from forusight.engine import venta_reciente as VR

    # Jockey Plaza: LU, MI, VI. Ruta del viernes 25/09 → la anterior es el miércoles 23/09.
    assert VR.ruta_anterior("2026-09-25", "LU,MI,VI") == pd.Timestamp("2026-09-23")
    assert VR.ruta_anterior("2026-09-29", "MA") == pd.Timestamp("2026-09-22")
    assert VR.ruta_anterior("2026-09-25", "", 2.33) == pd.Timestamp("2026-09-22")
    e = _escenario()
    p = params()
    inp = e.inputs()
    sku = inp.dim_producto["sku"].iloc[0]
    diaria = pd.DataFrame(
        {
            "fecha": pd.to_datetime(["2026-09-22", "2026-09-23", "2026-09-24"]),
            "tienda_id": "8",
            "sku": sku,
            "unidades": [4.0, 10.0, 15.0],  # el 22 ya lo cubrió la ruta del 23
        }
    )
    dt = CAL.aplicar(inp.dim_tienda, "2026-09-25", p, ["HUSH PUPPIES"])  # viernes
    vr = VR.resumir(diaria, dt, "2026-09-25", "2026-09-24")  # corte del 24 = cierre del 23
    fila = vr.set_index(["tienda_id", "sku"]).loc[("8", sku)]
    assert fila["venta_desde_ruta"] == 25 and fila["venta_post_corte"] == 15
    inp.dim_tienda = dt
    inp.venta_reciente = vr
    d = ejecutar(inp, p, CORTE).detalle.set_index(["tienda_id", "sku"])
    assert d.loc[("8", sku), "venta_desde_ruta"] == 25
    assert d.loc[("8", sku), "necesidad"] >= 25 and d.loc[("8", sku), "cantidad"] >= 25
    # sin la regla, la necesidad sale sólo del nivel máximo de 12 semanas
    p2 = params(reposicion_venta={"reponer_venta_desde_ruta": False})
    d2 = ejecutar(inp, p2, CORTE).detalle.set_index(["tienda_id", "sku"])
    assert d2.loc[("8", sku), "necesidad"] < 25
