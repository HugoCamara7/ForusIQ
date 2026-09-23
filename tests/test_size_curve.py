import numpy as np
import pandas as pd
import pytest

from forusight.engine.pipeline import ejecutar
from forusight.engine.size_curve import hamilton, repartir_hamilton
from tests.builders import CORTE, Escenario, params


@pytest.mark.parametrize("seed", range(300))
def test_hamilton_suma_exacta(seed):
    rng = np.random.default_rng(seed)
    n = int(rng.integers(1, 12))
    shares = rng.dirichlet(np.ones(n)) if seed % 3 else rng.random(n)  # también sin normalizar
    total = int(rng.integers(0, 200))
    q = hamilton(total, shares)
    assert q.sum() == total
    assert (q >= 0).all()
    # cada talla queda a menos de 1 unidad de su cuota exacta
    exacta = total * shares / shares.sum()
    assert np.all(np.abs(q - exacta) < 1)


def test_hamilton_desempate_determinista():
    assert list(hamilton(1, [0.5, 0.5])) == [1, 0]
    assert list(hamilton(1, [0.5, 0.5], orden=[2, 1])) == [0, 1]
    assert list(hamilton(2, [1 / 3] * 3)) == [1, 1, 0]
    assert list(hamilton(0, [0.2, 0.8])) == [0, 0]


def test_hamilton_vectorizado_igual_al_escalar():
    rng = np.random.default_rng(0)
    filas = []
    for g in range(200):
        n = int(rng.integers(1, 9))
        sh = rng.dirichlet(np.ones(n))
        tot = int(rng.integers(0, 60))
        for i in range(n):
            filas.append({"g": g, "orden": i, "share": sh[i], "total": tot})
    df = pd.DataFrame(filas).sample(frac=1, random_state=1)  # orden de filas arbitrario
    df["q"] = repartir_hamilton(df, "total", "share", ["g"], ["orden"])
    for _, grp in df.groupby("g"):
        grp = grp.sort_values("orden")
        assert grp["q"].sum() == grp["total"].iat[0]
        assert list(grp["q"]) == list(hamilton(grp["total"].iat[0], grp["share"], grp["orden"]))


def _curva(res, tienda, mc):
    d = res.detalle
    return d.loc[(d.tienda_id == tienda) & (d.modelo_color_id == mc)].set_index("talla")


def _escenario_curva():
    e = Escenario()
    for t in ("T1", "T2", "T3"):
        e.tienda(t)
    skus = e.modelo("M1-NEG", tallas=("36", "37", "38", "39"))  # no fabrica la 40
    e.modelo("M2-NEG", tallas=("36", "37", "38", "39", "40"))
    for s in skus:
        e.stock_cd(s, 30)
    return e, skus


def test_curva_solo_tallas_fabricadas_y_suma_uno():
    e, skus = _escenario_curva()
    e.venta_constante("T1", skus, unidades=2)
    e.venta_constante("T1", [f"M2-NEG-{t}" for t in (36, 37, 38, 39, 40)], unidades=1)
    res = ejecutar(e.inputs(), params(), CORTE, run_id="r")
    c = _curva(res, "T1", "M1-NEG")
    assert set(c.index) == {"36", "37", "38", "39"}
    assert c["share_talla"].sum() == pytest.approx(1.0)
    shares = res.detalle.groupby(["tienda_id", "modelo_color_id"])["share_talla"].sum()
    assert np.allclose(shares, 1.0)


def test_objetivo_curva_suma_exacta_el_objetivo_mc(resultado_sintetico):
    d = resultado_sintetico.detalle
    m = resultado_sintetico.mc.set_index(["tienda_id", "modelo_color_id"])["objetivo_mc"]
    # Hamilton sobre share → la curva redondeada suma exactamente el objetivo del MC
    df = d[["tienda_id", "modelo_color_id", "talla_orden", "sku", "share_talla"]].copy()
    df["objetivo_mc"] = [m.loc[k] for k in zip(df.tienda_id, df.modelo_color_id, strict=True)]
    df["q"] = repartir_hamilton(
        df, "objetivo_mc", "share_talla", ["tienda_id", "modelo_color_id"], ["talla_orden", "sku"]
    )
    s = df.groupby(["tienda_id", "modelo_color_id"])["q"].sum()
    assert (s == m.loc[s.index]).all()


def test_suavizado_bayesiano_sin_datos_usa_prior_y_con_muchos_datos_el_local():
    e, skus = _escenario_curva()
    # T2 y T3 definen el prior del cluster: curva concentrada en 38
    for t in ("T2", "T3"):
        for s, u in zip(skus, (1, 2, 6, 1), strict=True):
            e.venta_constante(t, [s], unidades=u)
    # T1 vende mucho, curva plana
    e.venta_constante("T1", skus, unidades=10)
    res = ejecutar(e.inputs(), params(), CORTE, run_id="r")
    c = _curva(res, "T1", "M1-NEG")
    n = 4 * 12 * 10
    k = params().curva.k_prior
    assert c.loc["38", "share_talla"] == pytest.approx(
        (n * 0.25 + k * c.loc["38", "prior_talla"]) / (n + k), rel=1e-6
    )
    assert abs(c.loc["38", "share_talla"] - 0.25) < 0.02  # dominado por lo local

    e2, skus2 = _escenario_curva()
    for t in ("T2", "T3"):
        for s, u in zip(skus2, (1, 2, 6, 1), strict=True):
            e2.venta_constante(t, [s], unidades=u)
    res2 = ejecutar(e2.inputs(), params(), CORTE, run_id="r")
    c2 = _curva(res2, "T1", "M1-NEG")  # T1 nunca lo tuvo: share = prior
    assert c2.loc["38", "share_talla"] == pytest.approx(6 / 10)
    assert c2["nivel_prior"].iat[0] == "CLUSTER_MODELO"


def test_jerarquia_de_prior_cae_a_nacional_sin_volumen_en_cluster():
    e, skus = _escenario_curva()
    e.tienda("T9", cluster="Z")
    for s, u in zip(skus, (1, 1, 2, 1), strict=True):  # cluster A con poco volumen
        e.venta("T2", s, 1, u)
    for s, u in zip(skus, (2, 2, 4, 2), strict=True):  # cluster Z con volumen
        e.venta_constante("T9", [s], unidades=u)
    res = ejecutar(e.inputs(), params(), CORTE, run_id="r")
    c = _curva(res, "T1", "M1-NEG")
    assert c["nivel_prior"].iat[0] == "NACIONAL"


def test_venta_corregida_por_disponibilidad_de_talla():
    """Una talla con quiebre la mitad del tiempo no debe perder participación."""
    e, skus = _escenario_curva()
    for s in skus:
        for rel in range(1, 13):
            dias = 0 if (s.endswith("38") and rel <= 6) else 7
            e.venta("T1", s, rel, 0 if dias == 0 else 3, dias)
    res = ejecutar(e.inputs(), params(curva={"k_prior": 0}), CORTE, run_id="r")
    c = _curva(res, "T1", "M1-NEG")
    assert c["share_talla"].to_numpy() == pytest.approx([0.25] * 4)
