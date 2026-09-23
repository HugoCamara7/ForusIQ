import pandas as pd

from forusight.data.schemas import DistribucionSchema
from forusight.engine.pipeline import EngineInputs, ejecutar
from forusight.engine.reasons import DESCRIPCION_CODIGOS
from tests.builders import params


def test_salida_cumple_contrato(resultado_sintetico):
    DistribucionSchema.validate(resultado_sintetico.detalle)
    assert resultado_sintetico.resumen["unidades_a_distribuir"] > 0


def test_nunca_supera_stock_cd(resultado_sintetico, sintetico):
    inp, _ = sintetico
    cd = inp.stock_cd.set_index("sku")
    disp = (cd["fisico"] - cd["reservado"] - cd["comprometido"]).clip(lower=0)
    asignado = resultado_sintetico.detalle.groupby("sku")["cantidad"].sum()
    assert (asignado <= disp.reindex(asignado.index).fillna(0)).all()


def test_cantidad_no_supera_necesidad(resultado_sintetico):
    d = resultado_sintetico.detalle
    assert (d["cantidad"] <= d["necesidad"]).all()


def test_determinismo_end_to_end(sintetico):
    inp, corte = sintetico
    a = ejecutar(inp, params(), corte, run_id="X").detalle
    barajado = EngineInputs(
        ventas=inp.ventas.sample(frac=1, random_state=1),
        stock_tienda=inp.stock_tienda.sample(frac=1, random_state=2),
        stock_cd=inp.stock_cd.sample(frac=1, random_state=3),
        dim_producto=inp.dim_producto.sample(frac=1, random_state=4),
        dim_tienda=inp.dim_tienda.sample(frac=1, random_state=5),
    )
    b = ejecutar(barajado, params(), corte, run_id="X").detalle
    pd.testing.assert_frame_equal(a, b)


def test_toda_fila_tiene_motivo(resultado_sintetico):
    d = resultado_sintetico.detalle
    assert d["motivo_texto"].str.len().gt(10).all()
    assert set(d["motivo_codigo"]) <= set(DESCRIPCION_CODIGOS)
    enviadas = d.loc[d["cantidad"] > 0, "motivo_texto"]
    assert enviadas.str.startswith("Se recomienda enviar").all()
    assert d.loc[d["cantidad"] == 0, "motivo_texto"].str.startswith("No se").all()


def test_filas_no_enviadas_se_conservan(resultado_sintetico):
    d = resultado_sintetico.detalle
    assert (d["cantidad"] == 0).any() and (d["cantidad"] > 0).any()


def test_tienda_inactiva_no_recibe(resultado_sintetico, sintetico):
    inp, _ = sintetico
    inactivas = set(inp.dim_tienda.loc[~inp.dim_tienda["activa"], "tienda_id"])
    assert inactivas
    assert not set(resultado_sintetico.detalle["tienda_id"]) & inactivas


def test_tope_de_tienda_de_dimension(resultado_sintetico, sintetico):
    inp, _ = sintetico
    t = inp.dim_tienda.dropna(subset=["max_unidades_corrida"])
    enviado = resultado_sintetico.detalle.groupby("tienda_id")["cantidad"].sum()
    for r in t.itertuples():
        assert enviado.get(r.tienda_id, 0) <= r.max_unidades_corrida


def test_cubre_todos_los_casos_especiales(resultado_sintetico):
    """El set sintético ejercita todos los estados y la mayoría de los motivos."""
    d = resultado_sintetico.detalle
    assert {"NUNCA_TUVO", "TUVO_Y_VENDE", "QUIEBRE", "TUVO_SIN_VENTA"} <= set(d["estado_mc"])
    assert {"ENVIO_QUIEBRE", "ENVIO_INTRODUCCION", "NO_SIN_STOCK_CD", "NO_AFINIDAD_BAJA"} <= set(
        d["motivo_codigo"]
    )
