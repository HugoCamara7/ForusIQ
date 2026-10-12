"""Calibración del tránsito contra el reporte de Neogística (Conexión)."""

from types import SimpleNamespace

import pandas as pd

from forusight.data import fuentes as F
from forusight.data import transito as TR
from tests.builders import params

REPORTE = pd.Timestamp("2026-10-12")  # cierre de stock del 11/10


def _lineas() -> pd.DataFrame:
    """97: SKU 5 en transporte hace 1 día (Neogística lo ve), SKU 6 en transporte de hace 9 días
    (ya llegó: Neogística no lo ve), SKU 7 aprobado el día del reporte (el pedido de hoy) y
    SKU 8 aprobado ayer (Neogística lo ve)."""
    return pd.DataFrame(
        {
            "nro_pedido": ["P1", "P2", "P3", "P4"],
            "tienda_cod": ["097"] * 4,
            "id_producto": ["5", "6", "7", "8"],
            "estado": ["6. en Transporte", "6", "1", "1"],
            "clasificacion": ["1"] * 4,
            "fecha_pedido": pd.to_datetime(
                ["2026-10-10", "2026-10-02", "2026-10-12", "2026-10-11"]
            ),
            "recepcionado": [False] * 4,
            "unidades": [2.0, 1.0, 3.0, 1.0],
            "cuenta_transito": [True] * 4,
        }
    )


def _reporte() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Código SKU": ["5", "8", "9"],
            "Código Centro": ["97", "97", "97"],
            "Stock Trán. Int. [un]": [2, 1, 0],
            "Stock Trán. Prov. [un]": [0, 0, 0],
        }
    )


def test_comparar_no_cuenta_los_pedidos_creados_el_dia_del_reporte():
    res, _ = F.comparar_transito(_lineas(), _reporte(), REPORTE)
    assert res["creados_dia_reporte"] == 3  # el pedido del 12/10 no puede estar en el reporte
    assert res["transito_forusight"] == 4 and res["transito_neogistica"] == 3


def test_calibrar_sugiere_la_ventana_que_cuadra():
    regla = F.regla_de(params().recepcion)  # 6/7 hasta 15 días
    cal = F.calibrar_transito(_lineas(), _reporte(), REPORTE, regla)
    # con 15 días el SKU 6 (llegado hace días) sobra: 2 de 3 tienda × SKU iguales
    assert round(cal["actual"]["filas_iguales"], 3) == 0.667
    sug = cal["sugerida"]
    assert sug["filas_iguales"] == 1.0 and sug["unidades_distintas"] == 0
    assert sug["regla"]["dias"] < 9 and 6 in sug["regla"]["estados_recientes"]
    assert 1 in sug["regla"]["estados"]
    assert F.cuenta_con_regla(F._lineas_calibrables(_lineas(), REPORTE), sug["regla"]).tolist() == [
        True,
        False,
        True,
        True,
    ]
    ant = cal["por_antiguedad"].set_index(["estado_cod", "dias_tramo"])
    assert ant.loc[(6, "8-15"), "% con tránsito Neo"] == 0


def test_detalle_dice_que_pedidos_forman_la_diferencia():
    regla = F.regla_de(params().recepcion)
    det = F.detalle_transito(_lineas(), _reporte(), REPORTE, regla)
    assert det["sku"].tolist() == ["6"]
    fila = det.iloc[0]
    assert fila["tipo"] == "Forusight cuenta de más" and fila["diferencia"] == 1
    assert "P2 · en Transporte · Reposición · 02/10 · 1 u" in fila["pedidos"]


def test_transito_creado_despues_del_corte():
    p = pd.DataFrame(
        {
            "tienda_id": ["97", "97"],
            "sku": ["5", "6"],
            "estado": [1, 1],
            "clasificacion": [1, 1],
            "recibido_post_corte": [False, False],
            "recepcionado": [False, False],
            "dias_pedido": [-1, 3],
            "unidades": [4.0, 2.0],
        }
    )
    inputs = SimpleNamespace(pedidos=p)
    assert TR.transito_post_corte(inputs, params()) == 4
