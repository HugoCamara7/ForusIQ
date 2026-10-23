"""Disponibilidad como la mide Forus: SKU con venta en 8 semanas que tienen stock en la tienda."""

import pandas as pd

from forusight.engine import disponibilidad as D

#: Ejemplo de Forus (22/10): venta total de 8 semanas, stock en tienda y stock del CD por SKU.
EJEMPLO = [
    (17, 1, 100),
    (10, 0, 50),
    (12, 0, 50),
    (16, 0, 1),
    (0, 1, 0),  # sin venta: no se mide aunque tenga stock
    (15, 0, 0),  # vendió y no hay en ningún lado: es quiebre aunque el CD no tenga
    (11, 1, 250),
    (0, 0, 1),
    (9, 1, 600),
    (17, 1, 125),
    (10, 0, 2316),
    (11, 1, 545),
]


def _detalle(filas, cantidad=0, tienda="T1"):
    return pd.DataFrame(
        [
            {
                "tienda_id": tienda,
                "modelo_color_id": f"M{i}",
                "sku": f"S{i}",
                "talla": "38",
                "venta_8s": venta,
                "venta_12s": venta,
                "stock_tienda": stock,
                "stock_cd_disponible": cd,
                "stock_transito": 0,
                "cantidad": cantidad if stock == 0 and cd > 0 else 0,
                "demanda_semanal": 0.5,
            }
            for i, (venta, stock, cd) in enumerate(filas)
        ]
    )


def test_ejemplo_forus_da_50_por_ciento():
    k = D.kpis(_detalle(EJEMPLO))
    assert k["activos"] == 10  # SKU con venta en 8 semanas
    assert k["simple_antes"] == 0.5  # 5 con stock en tienda / 10


def test_el_cd_no_cuenta_y_el_envio_recupera_solo_lo_que_hay_en_el_cd():
    k = D.kpis(_detalle(EJEMPLO, cantidad=1))
    assert k["simple_antes"] == 0.5
    assert k["simple_despues"] == 0.9  # 4 quiebres con CD se reponen; el de CD 0 sigue en quiebre


def test_quiebres_incluyen_los_que_el_cd_no_tiene():
    q = D.quiebres(D.sku_activos(_detalle(EJEMPLO)))
    assert len(q) == 5
    assert (q["stock_cd_disponible"] == 0).sum() == 1


def test_por_tienda_cuadra_con_el_total():
    d = pd.concat([_detalle(EJEMPLO, tienda="T1"), _detalle(EJEMPLO[:4], tienda="T2")])
    skus = D.sku_activos(d)
    t = D.por_tienda_kpis(skus)
    k = D.kpis_skus(skus)
    assert t["activos"].sum() == k["activos"] == 14
    assert t["disponibles"].sum() / t["activos"].sum() == k["simple_antes"]


def test_con_transito_cuenta_lo_que_va_en_camino():
    d = _detalle([(5, 0, 3), (5, 1, 0)])
    d.loc[0, "stock_transito"] = 2
    k = D.kpis(d)
    assert k["simple_antes"] == 0.5 and k["con_transito_antes"] == 1.0
    assert k["wos_antes"] == 3 / 1.0


def test_corridas_sin_venta_8s_usan_12_semanas():
    d = _detalle(EJEMPLO).drop(columns="venta_8s")
    assert D.kpis(d)["simple_antes"] == 0.5
