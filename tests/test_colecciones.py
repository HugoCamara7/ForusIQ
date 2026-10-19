"""Colecciones activas por tienda × clase × marca (reporte SAP 1037)."""

import io

import pandas as pd

from forusight.config.settings import EngineParams
from forusight.data import bloqueos as B
from forusight.data import colecciones as COL

MAPA = {
    "TODO|CALZADO|ROCKFORD": ["VERANO 2026"],
    "TODO|CALZADO|KEDS": ["INVIERNO 2026", "VERANO 2025", "VERANO 2026"],
    "147|CALZADO|ROCKFORD": ["INVIERNO 2026"],
    "133|CALZADO|ROCKFORD": ["VERANO 2023"],
}


def _repone(tienda, marca, clase, temporadas, liquidadoras=()) -> dict:
    n = len(temporadas)
    _, fuera = B.fuera_de_surtido(
        pd.Series([tienda] * n),
        pd.Series(["M-C"] * n),
        pd.Series(temporadas, dtype="string"),
        None,
        ["ESCOLAR 2026", "FUTURA"],
        "VERANO 2026",
        liquidadoras=set(liquidadoras),
        hasta_liquidadoras="INVIERNO 2026",
        colecciones=MAPA,
        marca=pd.Series([marca] * n),
        clase=pd.Series([clase] * n),
    )
    return {t: not f for t, f in zip(temporadas, fuera, strict=True)}


def test_suma_todo_y_lo_propio_de_la_tienda():
    temps = ["VERANO 2023", "INVIERNO 2026", "VERANO 2026", "INVIERNO 2027"]
    # tienda sin filas propias: sólo el parámetro general (VERANO 2026 en adelante)
    assert _repone("8", "ROCKFORD", "CALZADO", temps) == {
        "VERANO 2023": False,
        "INVIERNO 2026": False,
        "VERANO 2026": True,
        "INVIERNO 2027": True,
    }
    # 147 RKF CAYMA: además su INVIERNO 2026 (no pierde VERANO 2026 de TODO ni lo general)
    assert _repone("147", "ROCKFORD", "CALZADO", temps)["INVIERNO 2026"]
    assert _repone("147", "ROCKFORD", "CALZADO", temps)["VERANO 2026"]
    # 133: lo suyo es VERANO 2023, no el INVIERNO 2026 de otra tienda
    r = _repone("133", "ROCKFORD", "CALZADO", temps)
    assert r["VERANO 2023"] and not r["INVIERNO 2026"]


def test_todo_aplica_a_todas_las_tiendas_de_linea_por_marca_y_clase():
    temps = ["VERANO 2025", "INVIERNO 2026"]
    assert all(_repone("8", "keds ", "calzado", temps).values())
    assert not any(_repone("8", "KEDS", "VESTUARIO", temps).values())  # otra clase
    assert not any(_repone("8", "VANS", "CALZADO", temps).values())  # otra marca


def test_liquidadoras_siguen_por_jerarquia():
    temps = ["VERANO 2023", "INVIERNO 2026", "VERANO 2026"]
    assert _repone("133", "ROCKFORD", "CALZADO", temps, liquidadoras={"133"}) == {
        "VERANO 2023": True,
        "INVIERNO 2026": True,
        "VERANO 2026": False,
    }


def test_sin_marca_ni_clase_no_cambia_nada():
    _, fuera = B.fuera_de_surtido(
        pd.Series(["147"]),
        pd.Series(["M-C"]),
        pd.Series(["INVIERNO 2026"], dtype="string"),
        None,
        [],
        "VERANO 2026",
        colecciones=MAPA,
    )
    assert fuera.tolist() == [True]


def _reporte_sap(filas) -> bytes:
    crudo = [
        [None] * 4,
        ["Empresa:", "Forus Peru", None, None],
        ["Reporte:", "1037 - Colecciones Activas Marca", None, None],
        ["Fecha:", "15/10/2026", None, None],
        [None] * 4,
        ["Código centro", "Clase", "Marca", "Coleccion activa"],
        *filas,
    ]
    buf = io.BytesIO()
    pd.DataFrame(crudo).to_excel(buf, header=False, index=False)
    return buf.getvalue()


def test_lee_el_reporte_de_sap_con_titulos():
    d, fecha = COL.leer_reporte(
        _reporte_sap(
            [
                ["0147", "calzado", "Rockford", "invierno  2026"],
                ["TODO", "CALZADO", "ROCKFORD", "VERANO 2026"],
                ["TODO", "CALZADO", "ROCKFORD", "VERANO 2026"],
                [None, None, None, None],
            ]
        ),
        "20261015_Colecciones.xlsx",
    )
    assert fecha == "15/10/2026"
    assert COL.a_mapa(d) == {
        "147|CALZADO|ROCKFORD": ["INVIERNO 2026"],
        "TODO|CALZADO|ROCKFORD": ["VERANO 2026"],
    }


def test_por_defecto_es_el_reporte_del_15_10():
    m = EngineParams().surtido.colecciones_activas
    assert m == COL.por_defecto()
    assert m["TODO|CALZADO|KEDS"] == ["INVIERNO 2026", "VERANO 2025", "VERANO 2026"]
    assert m["147|CALZADO|ROCKFORD"] == ["INVIERNO 2026"]
    assert sum(len(v) for v in m.values()) == 108


def test_ultima_carga_reemplaza_completo():
    t1 = COL.a_tabla({"TODO|CALZADO|KEDS": ["VERANO 2025"]})
    t2 = COL.a_tabla({"TODO|CALZADO|VANS": ["INVIERNO 2027"]})
    hist = pd.concat(
        [
            COL.nuevos(t1, "01/10/2026", "a", "2026-10-01").assign(carga="1"),
            COL.nuevos(t2, "", "b", "x").assign(carga="2"),
        ]
    )
    assert COL.a_mapa(COL.vigente(hist)) == {"TODO|CALZADO|VANS": ["INVIERNO 2027"]}
    assert COL.vigente(hist.iloc[0:0]) is None
