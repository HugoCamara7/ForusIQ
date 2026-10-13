import pandas as pd

from forusight.data import mantenedores as MT
from forusight.data import planificacion as PLAN


def test_temporadas_ultimo_cambio_manda():
    h = pd.concat(
        [
            MT.nuevos_temporadas(["hp1-n11", "HP2-B01"], "invierno 2026", "ana", "2026-10-01"),
            MT.nuevos_temporadas(["HP1-N11 "], "VERANO 2026", "luis", "2026-10-02"),
        ],
        ignore_index=True,
    )
    v = MT.temporadas_vigentes(h)
    assert v["HP1-N11"] == "VERANO 2026"
    assert v["HP2-B01"] == "INVIERNO 2026"
    assert MT.temporadas_vigentes(MT.vacio(MT.COLS_TEMPORADAS)).empty


def test_csv_ida_y_vuelta():
    h = MT.nuevos_temporadas(["HP1-N11"], "VERANO 2026", "ana", "2026-10-01")
    assert MT.leer(MT.a_csv(h), MT.COLS_TEMPORADAS).equals(h.astype(str))
    assert MT.leer(None, MT.COLS_STOCK_MINIMO).columns.tolist() == MT.COLS_STOCK_MINIMO


def test_stock_minimo_normaliza_y_descarta_invalidos():
    filas = pd.DataFrame(
        {
            "tienda_id": ["008", "8", "9"],
            "sku": ["05389545", "4417904", "1"],
            "stock_minimo": ["2", "x", "-1"],
        }
    )
    n = MT.nuevos_stock_minimo(filas, "ana", "2026-10-01")
    assert len(n) == 1
    assert n.iloc[0][["tienda_id", "sku", "stock_minimo"]].tolist() == ["8", "5389545", "2"]


def test_aplicar_stock_minimo_reemplaza_y_agrega():
    claves = PLAN.vacio().claves.reindex(range(1))
    claves.loc[0, ["tienda_id", "sku", "smt"]] = ["8", "100", 3]
    claves["fecha"] = pd.Timestamp("2026-09-01")
    m = PLAN.Maestro(claves, PLAN.vacio().k)
    hist = pd.DataFrame(
        {
            "tienda_id": ["8", "8", "9"],
            "sku": ["100", "100", "200"],
            "stock_minimo": ["1", "2", "4"],
            "usuario": "ana",
            "fecha": "2026-10-01",
        }
    )
    out = MT.aplicar_stock_minimo(m, hist, "2026-10-13").claves.set_index(["tienda_id", "sku"])
    assert out.loc[("8", "100"), "smt"] == 2
    assert out.loc[("9", "200"), "smt"] == 4
    assert out.loc[("9", "200"), "fecha"] == pd.Timestamp("2026-10-13")
    assert MT.aplicar_stock_minimo(m, None, "2026-10-13") is m
