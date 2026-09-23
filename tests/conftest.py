import pytest

from forusight.data.synthetic import ConfigSintetica, generar
from forusight.engine.pipeline import ejecutar
from tests.builders import params


@pytest.fixture(scope="session")
def sintetico():
    return generar(ConfigSintetica(seed=11))


@pytest.fixture(scope="session")
def resultado_sintetico(sintetico):
    inp, corte = sintetico
    return ejecutar(inp, params(), corte, run_id="RUN-TEST")
