"""Disponibilidad como la mide Forus: la talla quebrada que el CD no tiene no resta."""

import pandas as pd

from forusight.engine import disponibilidad as D


def _modelo(tienda, quebradas, en_cd, cantidad=0, venta=10, tallas=12):
    filas = []
    for i in range(tallas):
        rota = i < quebradas
        filas.append(
            {
                "tienda_id": tienda,
                "modelo_color_id": "M1",
                "stock_tienda": 0 if rota else 1,
                "cantidad": cantidad if rota else 0,
                "stock_cd_disponible": 5 if (rota and en_cd) else 0,
                "venta_12s": venta / tallas,
            }
        )
    return filas


def test_tallas_que_el_cd_no_tiene_no_restan():
    d = pd.DataFrame(_modelo("T1", quebradas=3, en_cd=False))
    assert D.total(d)["antes"] == 1.0


def test_tallas_que_el_cd_si_tiene_restan_y_el_envio_las_recupera():
    d = pd.DataFrame(_modelo("T1", quebradas=3, en_cd=True, cantidad=1))
    t = D.total(d)
    assert t["antes"] == 0.75 and t["despues"] == 1.0


def test_total_pondera_por_flujo_de_la_tienda():
    d = pd.DataFrame(
        _modelo("GRANDE", quebradas=0, en_cd=True, venta=900)
        + _modelo("CHICA", quebradas=6, en_cd=True, venta=9)
    )
    t = D.total(d)
    assert t["antes"] > 0.98  # la tienda con más flujo pesa casi todo
    assert set(D.por_tienda(d)["tienda_id"]) == {"GRANDE", "CHICA"}


def test_kpis_del_piloto_simple_ponderada_y_wos():
    filas = _modelo("T1", quebradas=1, en_cd=True, cantidad=1)  # 11/12 hoy, 12/12 después
    for f in filas:
        f["demanda_semanal"] = 0.5
        f["stock_transito"] = 0
    k = D.kpis(pd.DataFrame(filas))
    assert k["activos"] == 12
    assert round(k["simple_antes"], 3) == round(11 / 12, 3) and k["simple_despues"] == 1.0
    assert k["simple_antes"] < D.MINIMO and k["simple_despues"] == D.META
    assert k["wos_antes"] == 11 / 6 and k["wos_despues"] == 2.0
