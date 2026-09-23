"""Smoke test de la UI con streamlit.testing (sin navegador, datos sintéticos)."""

from pathlib import Path

import pytest

st_testing = pytest.importorskip("streamlit.testing.v1")
RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("FORUSIGHT_DATA_SOURCE", "synthetic")
    monkeypatch.chdir(RAIZ)
    at = st_testing.AppTest.from_file(str(RAIZ / "streamlit_app.py"), default_timeout=120)
    at.run()
    assert not at.exception
    return at


@pytest.mark.parametrize(
    "pagina",
    ["1_Dashboard", "2_Recomendaciones", "3_Revision_Aprobacion", "4_Exportacion", "5_Parametros"],
)
def test_paginas_con_corrida(app, pagina):
    app.sidebar.button[0].click().run()
    assert app.session_state.resultado is not None
    app.switch_page(f"app/pages/{pagina}.py").run()
    assert not app.exception, app.exception
