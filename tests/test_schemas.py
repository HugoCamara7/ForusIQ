import pandas as pd
import pytest
from pandera.errors import SchemaErrors

from forusight.data.schemas import validar


def _ventas(**kw):
    base = {
        "semana_inicio": ["2026-09-14"],
        "tienda_id": ["T1"],
        "sku": ["A"],
        "unidades": [1],
        "dias_con_stock": [7],
    }
    base.update(kw)
    return pd.DataFrame(base)


def test_coercion_de_tipos():
    v = validar("ventas", _ventas(tienda_id=[101]))
    assert v["tienda_id"].iat[0] == "101"
    assert pd.api.types.is_datetime64_any_dtype(v["semana_inicio"])


def test_dias_con_stock_fuera_de_rango():
    with pytest.raises(SchemaErrors):
        validar("ventas", _ventas(dias_con_stock=[8]))


def test_llave_duplicada():
    df = pd.concat([_ventas(), _ventas()])
    with pytest.raises(SchemaErrors):
        validar("ventas", df)


def test_stock_negativo():
    df = pd.DataFrame(
        {"tienda_id": ["T1"], "sku": ["A"], "stock_disponible": [-1], "stock_transito": [0]}
    )
    with pytest.raises(SchemaErrors):
        validar("stock_tienda", df)


def test_talla_duplicada_en_modelo():
    fila = {
        "modelo_id": "M",
        "modelo_color_id": "M-N",
        "color": "N",
        "talla": "38",
        "talla_orden": 38,
        "categoria": "Z",
        "genero": "DAMA",
        "rango_precio": "B",
    }
    df = pd.DataFrame([{"sku": "A", **fila}, {"sku": "B", **fila}])
    with pytest.raises(SchemaErrors):
        validar("dim_producto", df)


def test_columna_faltante():
    with pytest.raises(SchemaErrors):
        validar("stock_cd", pd.DataFrame({"sku": ["A"], "fisico": [1]}))
