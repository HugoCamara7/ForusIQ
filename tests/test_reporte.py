"""Reporte de distribución del día como base: misma regla de necesidad y reparto del CD."""

import io

import pandas as pd

from forusight.config.settings import EngineParams
from forusight.data import reporte as R
from forusight.export.archivo import a_excel_forusight

SEMANAS = ["2026-08-31", "2026-09-07", "2026-09-14"]


def _fila(centro, nombre, sku, **kw):
    base = {
        R.SKU: sku,
        "Código Modelo": "M1",
        "Código Color": "NEG",
        "Talla": "390",
        "Marca": "HUSH PUPPIES",
        "Clase": "CALZADO",
        "Género": "HOMBRE",
        R.CENTRO: centro,
        "Nombre Centro": nombre,
        R.GRUPO: "Revision de stock",
        "Reposición Bloqueada": "NO",
        **{s: 1 for s in SEMANAS},  # la tienda vende la talla
        "Demanda Periodo Actual": 0,
        R.PRONOSTICO: 0.5,
        R.ROP: 1,
        R.MAX: 2,
        R.CD: 10,
        R.FISICO: 0,
        R.TR_INT: 0,
        R.TR_PROV: 0,
        R.COMPROMETIDO: 0,
        R.BACKORDER: 0,
        R.POSICION: 0,
        R.Q: 0,
        R.P: 0,
        R.MOT: R.SIN_PENDIENTE,
        R.UE: 1,
        "VTA 2 SEM": None,
        "Nivel de Disponibilidad Planificado [%]": 95,
    }
    base.update(kw)
    return base


def _excel(filas) -> bytes:
    df = pd.DataFrame(filas)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as w:
        cab = pd.DataFrame(
            [["Empresa:", "Forus Peru"], ["Reporte:", "534"], ["Fecha:", "23/09/2026"]]
        )
        cab.to_excel(w, sheet_name="Hoja1", header=False, index=False, startrow=2)
        df.to_excel(w, sheet_name="Hoja1", index=False, startrow=6)
    return buf.getvalue()


def test_lee_reporte_y_regla_de_necesidad():
    filas = [
        _fila("8", "HP JOCKEY", "1", **{R.FISICO: 0, R.POSICION: 0, R.MAX: 2, R.ROP: 1}),  # 2
        _fila("12", "HP CHICLAYO", "1", **{R.FISICO: 2, R.POSICION: 2, R.MAX: 3, R.ROP: 1}),  # 0
        _fila("22", "HP TRUJILLO", "2", **{R.MAX: 7, R.ROP: 3, R.UE: 6}),  # 7 → 1 empaque = 6
        _fila("43", "HP SAN MIGUEL 2", "2", **{R.GRUPO: "Carga Pedidos", R.P: 4, R.MAX: None}),
        # talla que la tienda nunca tuvo ni vendió: no se llena la curva
        _fila("44", "HP PLAZA NORTE", "2", **{s: 0 for s in SEMANAS}),
    ]
    df, fecha = R.leer_reporte(_excel(filas))
    assert fecha == pd.Timestamp("2026-09-23")
    assert df[R.CENTRO].tolist() == ["8", "12", "22", "43", "44"]
    assert R.necesidad(df).tolist() == [2, 0, 6, 0, 0]  # la carga manual no es reposición
    assert R.solo_revision(df)[R.CENTRO].tolist() == ["8", "12", "22", "44"]
    assert R.corte(df) == pd.Timestamp("2026-09-21")


def test_cd_escaso_prioridad_jockey():
    filas = [
        _fila("12", "HP CHICLAYO", "1", **{R.CD: 1}),
        _fila("8", "HP JOCKEY", "1", **{R.CD: 1}),
    ]
    df, _ = R.leer_reporte(_excel(filas))
    out = R.distribuir(df, ["JOCKEY"], R.PRIORIDAD_FORUSIGHT).set_index(R.CENTRO)
    assert out.loc["8", R.Q] == 1 and out.loc["12", R.Q] == 0
    assert out.loc["12", R.MOT] == R.STOCK_CD


def test_igual_al_reporte_respeta_sus_decisiones_y_archivo_mismo_formato():
    filas = [  # el reporte mandó a CHICLAYO (orden que no está en sus columnas)
        _fila("12", "HP CHICLAYO", "1", **{R.CD: 1, R.Q: 1}),
        _fila("8", "HP JOCKEY", "1", **{R.CD: 1, R.P: 2, R.MOT: R.STOCK_CD}),
        _fila("16", "FB MINKA", "3", **{R.P: 2, R.MOT: "Almacenamiento"}),
    ]
    df, _ = R.leer_reporte(_excel(filas))
    out = R.distribuir(df, ["JOCKEY"], R.IGUAL_REPORTE)
    assert out[R.Q].tolist() == R._num(df[R.Q]).astype(int).tolist()
    assert out[R.MOT].tolist() == df[R.MOT].tolist()
    res = R.resultado_desde_reporte(out, "R1", R.corte(df), "320", EngineParams())
    assert R.coincidencia(df, res.detalle)["pct_filas"] == 1.0
    tabla = R.tabla_para_archivo(df, res.detalle, None)
    assert list(tabla.columns) == list(df.columns)
    assert tabla["VTA 2 SEM"].iloc[0].startswith("=")
    assert a_excel_forusight(tabla, pd.Timestamp("2026-09-23"))[:2] == b"PK"
    inp = R.entradas_desde_reporte(df)
    assert set(inp.dim_tienda["cadena"]) == {"HP", "FB"}
