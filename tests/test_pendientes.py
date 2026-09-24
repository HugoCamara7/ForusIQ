"""El stock del CD nunca se sobrepasa, tampoco entre corridas (envíos aún no recibidos)."""

import base64
import io
import json

import numpy as np
import pandas as pd

from forusight.data import pendientes as PEND
from forusight.data import reporte as R
from forusight.data.github_store import GitHubStore
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


def test_envio_pendiente_se_descuenta_del_cd_y_no_se_duplica():
    e, sku = _diez_unidades()
    pend = pd.DataFrame({"tienda_id": ["T0"], "sku": [sku], "cantidad": [6]})
    inp = PEND.aplicar_a_entradas(e.inputs(), pend)
    d = ejecutar(inp, params(), CORTE).detalle.set_index("tienda_id")
    assert d["cantidad"].sum() == 4  # 10 − 6 ya comprometidas
    assert d.loc["T0", "stock_transito"] == 6  # la tienda ya lo tiene en camino


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


def test_reporte_descuenta_pendientes_y_el_archivo_lo_muestra():
    filas = [_fila(c, n, "1", **{R.CD: 10}) for c, n in (("8", "HP JOCKEY"), ("12", "HP CHICLAYO"))]
    df, _ = R.leer_reporte(_excel(filas))
    pend = pd.DataFrame({"tienda_id": ["8"], "sku": ["1"], "cantidad": [9]})
    base = PEND.aplicar_a_reporte(df, pend)
    assert R._num(base[R.CD]).tolist() == [1, 1]
    out = R.distribuir(base)
    assert out[R.Q].sum() <= 1
    res = R.resultado_desde_reporte(out, "X", R.corte(df), "320", params())
    tabla = R.tabla_para_archivo(df, res.detalle, None)
    assert R._num(tabla[R.CD]).tolist() == [1, 1] and R._num(tabla[R.TR_INT]).tolist() == [9, 0]


def test_leer_aprobacion_csv_y_github():
    csv = "tienda_id,sku,cantidad_aprobada\n008,0005221652,3\n8,5221652.0,2\n12,77,0\n"
    p = PEND.leer_archivo(csv.encode("utf-8-sig"), "a.csv")
    assert p.to_dict("records") == [{"tienda_id": "8", "sku": "5221652", "cantidad": 5}]

    hoy = pd.Timestamp("2026-09-24")
    archivos = {
        "forusight/aprobaciones": [
            {
                "type": "file",
                "name": "R1_20260923_101500.csv",
                "path": "forusight/aprobaciones/R1_20260923_101500.csv",
            },
            {
                "type": "file",
                "name": "R1_20260923_120000.csv",
                "path": "forusight/aprobaciones/R1_20260923_120000.csv",
            },
            {
                "type": "file",
                "name": "R0_20260901_090000.csv",
                "path": "forusight/aprobaciones/R0_20260901_090000.csv",
            },
        ],
        "forusight/aprobaciones/R1_20260923_120000.csv": {
            "content": base64.b64encode(csv.encode()).decode()
        },
    }

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout=30):
        ruta = req.full_url.split("/contents/")[1].split("?")[0]
        return _Resp(json.dumps(archivos[ruta]).encode())

    import forusight.data.github_store as G

    original = G.GitHubStore.__init__

    def init(self, *a, **k):
        original(self, *a, **{**k, "opener": opener})

    G.GitHubStore.__init__ = init
    try:
        sec = {"ticketing": {"repository": "o/r", "token": "t", "branch": "b"}}
        p, usados = PEND.desde_github(sec, 3, hoy)
    finally:
        G.GitHubStore.__init__ = original
    assert usados == ["R1_20260923_120000.csv"]  # la última aprobación de R1; R0 es vieja
    assert p["cantidad"].sum() == 5
    assert isinstance(GitHubStore, type)


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
