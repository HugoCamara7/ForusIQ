"""Detalle por tienda del velocímetro de disponibilidad (Excel descargable en Inicio)."""

import io

import pandas as pd
import pytest

from forusight.data.repository import SyntheticRepository
from forusight.engine import disponibilidad as DISP
from forusight.engine.pipeline import ejecutar
from forusight.export import disponibilidad as XD
from tests.builders import params


@pytest.fixture(scope="module")
def res():
    inp = SyntheticRepository().cargar_entradas()
    return ejecutar(inp, params(calendario={"aplicar": False}))


def test_total_por_tienda_cuadra_con_el_velocimetro(res):
    assert res.disponibilidad is not None and len(res.disponibilidad)
    t = DISP.por_tienda_kpis(res.disponibilidad)
    r = res.resumen
    assert t["activos"].sum() == r["sku_activos_retail"]
    assert t["disponibles"].sum() / t["activos"].sum() == pytest.approx(r["disponibilidad_retail"])
    k = DISP.kpis_skus(res.disponibilidad)
    assert k["simple_despues"] == pytest.approx(r["disponibilidad_retail_despues"])
    assert k["con_transito_antes"] == pytest.approx(r["disponibilidad_retail_transito"])
    # quiebres = activos − disponibles, por tienda
    assert (t["activos"] - t["disponibles"]).eq(t["quiebres"]).all()


def test_excel_por_tienda(res):
    xlsx = XD.a_excel(res, res.tiendas, "15/10/2026")
    hojas = pd.read_excel(io.BytesIO(xlsx), sheet_name=None, header=None)
    assert list(hojas) == ["Por tienda", "Tallas en quiebre", "Cómo se calcula"]
    por = pd.read_excel(io.BytesIO(xlsx), sheet_name="Por tienda", header=2)
    total = por.iloc[0]
    assert total["Tienda"] == "TOTAL RETAIL"
    assert total["Disponibilidad"] == pytest.approx(res.resumen["disponibilidad_retail"])
    assert len(por) - 1 == res.disponibilidad["tienda_id"].nunique()
    assert por["SKU activos"].iloc[1:].sum() == total["SKU activos"]
    assert set(por["Estado"].iloc[1:]) <= {"Óptima", "En alerta", "Crítica"}
    q = pd.read_excel(io.BytesIO(xlsx), sheet_name="Tallas en quiebre")
    assert len(q) == por["Tallas en quiebre"].iloc[0]


def test_sin_dato_de_retail_usa_el_detalle(res):
    from dataclasses import replace

    viejo = replace(res, disponibilidad=None)
    por, _, completo = XD.tablas(viejo, res.tiendas)
    assert not completo and por["Tienda"].iloc[0] == "TOTAL TIENDAS DE HOY"
