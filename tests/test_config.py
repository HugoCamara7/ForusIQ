import pytest
from pydantic import ValidationError

from forusight.config.settings import (
    AppSettings,
    EngineParams,
    dump_params,
    load_params,
    save_params,
)


def test_params_yaml_carga_y_es_valido():
    p = load_params()
    assert p.demanda.pesos_bloques == [0.5, 0.3, 0.2]
    assert p.demanda.factor_sin_historia == 0.7
    assert p.disponibilidad.dias_min_exposicion == 14
    assert p.exhibicion.tallas_core("CUALQUIERA", "CABALLERO") == {"39", "40", "41", "42"}


def test_roundtrip(tmp_path):
    p = load_params()
    ruta = save_params(p, tmp_path / "p.yaml")
    assert load_params(ruta) == p
    assert "pesos_prioridad" in dump_params(p)


@pytest.mark.parametrize(
    "cambio",
    [
        {"demanda": {"pesos_bloques": [0.5, 0.5]}},
        {"demanda": {"tendencia_min": 1.1}},
        {"rotacion": {"percentil_alta": 0.2, "percentil_baja": 0.5}},
        {"asignacion": {"multiplo_envio": 0}},
        {"afinidad": {"umbral_introduccion": 1.5}},
        {"desconocido": {}},
    ],
)
def test_parametros_invalidos(cambio):
    with pytest.raises(ValidationError):
        EngineParams.model_validate(cambio)


def test_app_settings_desde_entorno(monkeypatch):
    monkeypatch.setenv("FORUSIGHT_GCP_PROJECT", "mi-proyecto")
    monkeypatch.setenv("FORUSIGHT_DATA_SOURCE", "bigquery")
    s = AppSettings()
    assert s.gcp_project == "mi-proyecto" and s.data_source == "bigquery" and s.cd_id == "320"
