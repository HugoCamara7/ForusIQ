"""Smoke test de la UI con streamlit.testing (sin navegador, datos sintéticos)."""

from pathlib import Path

import pytest

st_testing = pytest.importorskip("streamlit.testing.v1")
RAIZ = Path(__file__).resolve().parents[1]
PAGINAS = [
    "1_Dashboard",
    "2_Recomendaciones",
    "3_Revision_Aprobacion",
    "4_Exportacion",
    "7_Tiendas_Cadenas",
    "5_Parametros",
]


def _app(monkeypatch, secrets=None):
    monkeypatch.setenv("FORUSIGHT_DATA_SOURCE", "synthetic")
    monkeypatch.chdir(RAIZ)
    at = st_testing.AppTest.from_file(str(RAIZ / "streamlit_app.py"), default_timeout=120)
    for k, v in (secrets or {}).items():
        at.secrets[k] = v
    at.run()
    assert not at.exception, at.exception
    return at


def _en_dashboard(app) -> bool:
    return any('class="hero"' in m.value and ">Dashboard<" in m.value for m in app.markdown)


@pytest.mark.parametrize("pagina", PAGINAS)
def test_paginas_modo_demo_sin_login(monkeypatch, pagina):
    app = _app(monkeypatch)
    assert app.session_state.auth_user == "demo"
    app.sidebar.button[0].click().run()
    assert app.session_state.resultado is not None
    app.switch_page(f"app/vistas/{pagina}.py").run()
    assert not app.exception, app.exception


USUARIOS = {
    "app_auth": {
        "users": {"ana@forus.pe": "clave-ana", "luis@forus.pe": "clave-luis"},
        "roles": {"ana@forus.pe": "aprobador"},
    }
}


def test_login_pide_credenciales_y_rechaza_clave_mala(monkeypatch):
    app = _app(monkeypatch, USUARIOS)
    assert "authenticated" not in app.session_state
    assert app.text_input[0].label == "Correo electrónico"
    app.text_input[0].input("ana@forus.pe")
    app.text_input[1].input("mala")
    app.button[0].click().run()
    assert any("incorrectos" in e.value for e in app.error)
    assert not _en_dashboard(app)


def test_login_correcto_con_rol(monkeypatch):
    app = _app(monkeypatch, USUARIOS)
    app.text_input[0].input(" ANA@forus.pe ")
    app.text_input[1].input("clave-ana")
    app.button[0].click().run()
    assert app.session_state.auth_user == "ana@forus.pe"
    assert app.session_state.auth_rol == "aprobador"
    assert _en_dashboard(app)
    # un aprobador no tiene registrada la página Conexión (sólo admin)
    with pytest.raises(ValueError, match="Could not find a navigation page"):
        app.switch_page("app/vistas/6_Conexion.py")


def test_modo_demo_no_expone_bigquery_aunque_haya_secrets(monkeypatch):
    """[bigquery] configurado + data_source synthetic + sin [app_auth] → sólo datos sintéticos."""
    secrets = {
        "forusight": {"data_source": "synthetic"},
        "bigquery": {"enabled": True, "project_id": "p", "stock_table": "p.d.t"},
    }
    import app.components.estado as estado

    monkeypatch.setattr(estado, "SECRETS", secrets)
    app = _app(monkeypatch, secrets)
    assert app.session_state.sin_login
    assert app.session_state.fuente == "synthetic"
    with pytest.raises(ValueError, match="Could not find a navigation page"):
        app.switch_page("app/vistas/6_Conexion.py")


def test_dashboard_con_aprobacion_en_curso(monkeypatch):
    import pandas as pd

    app = _app(monkeypatch)
    app.sidebar.button[0].click().run()
    app.session_state["aprobacion"] = pd.DataFrame({"cantidad_aprobada": [1]})
    app.switch_page("app/vistas/1_Dashboard.py").run()
    assert not app.exception, app.exception
