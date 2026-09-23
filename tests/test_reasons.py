from forusight.engine.reasons import DESCRIPCION_CODIGOS, texto_motivo
from tests.builders import params

BASE = {
    "motivo_parcial": "",
    "cantidad": 3,
    "necesidad": 5,
    "stock_disponible": 0.0,
    "stock_transito": 1.0,
    "stock_objetivo": 6,
    "cobertura_semanas": 3.0,
    "rotacion": "alta",
    "demanda_sku": 1.5,
    "demanda_semanal": 6.0,
    "venta_12s": 20.0,
    "venta_4s": 8.0,
    "talla": "38",
    "afinidad": 0.7,
    "fuente_demanda": "SIMILARES",
    "dias_12s_mc": 84,
    "cobertura_actual": 15.0,
    "tope_sku_aplicado": False,
}


def test_todos_los_codigos_tienen_texto():
    for c in DESCRIPCION_CODIGOS:
        t = texto_motivo(
            {**BASE, "motivo_codigo": c, "cantidad": 3 if c.startswith("ENVIO") else 0}, params()
        )
        assert len(t) > 20
        assert t.startswith(
            "Se recomienda enviar 3 unidades porque" if c.startswith("ENVIO") else "No se"
        )


def test_texto_quiebre_con_parcial():
    t = texto_motivo(
        {**BASE, "motivo_codigo": "ENVIO_QUIEBRE", "motivo_parcial": "PARCIAL_CD"}, params()
    )
    assert "quiebre" in t and "20 pares" in t and "CD no alcanza" in t and "(5)" in t
