"""Smoke test de la UI con streamlit.testing (sin navegador, datos sintéticos)."""

from pathlib import Path

import pytest

st_testing = pytest.importorskip("streamlit.testing.v1")
RAIZ = Path(__file__).resolve().parents[1]
PAGINAS = ["1_Dashboard", "8_Bloqueos", "9_Rutas", "5_Parametros"]  # vista única + configuración


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
    return any('class="hero"' in m.value for m in app.markdown)


@pytest.mark.parametrize("pagina", PAGINAS)
def test_paginas_modo_demo_sin_login(monkeypatch, pagina):
    app = _app(monkeypatch)
    assert app.session_state.auth_user == "demo"
    app.button(key="btn_ejecutar").click().run()
    assert app.session_state.resultado is not None
    app.switch_page(f"app/vistas/{pagina}.py").run()
    assert not app.exception, app.exception


def test_vista_unica_aprobar_y_velocimetro(monkeypatch):
    app = _app(monkeypatch)
    app.button(key="btn_ejecutar").click().run()
    res = app.session_state.resultado
    assert any('class="gauge"' in m.value for m in app.markdown)  # disponibilidad del retail
    descargas = [b.label for b in app.get("download_button")]
    assert "Aprobar y descargar" in descargas  # un solo botón: aprueba, guarda y descarga
    assert "Aprobar y guardar" not in [b.label for b in app.button]
    from app.components.aprobacion import base_aprobacion

    app.session_state["aprobacion"] = base_aprobacion(res.detalle)
    app.session_state["aprobacion_confirmada"] = res.run_id
    app.run()
    assert not app.exception, app.exception
    assert "Descargar archivo aprobado" in [b.label for b in app.get("download_button")]
    assert not any(u.label == "Reporte de distribución del día" for u in app.get("file_uploader"))


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
    app.button(key="btn_ejecutar").click().run()
    app.session_state["aprobacion"] = pd.DataFrame({"cantidad_aprobada": [1]})
    app.switch_page("app/vistas/1_Dashboard.py").run()
    assert not app.exception, app.exception


def test_mantenedor_de_bloqueos(monkeypatch):
    app = _app(monkeypatch)
    app.switch_page("app/vistas/8_Bloqueos.py").run()
    assert not app.exception, app.exception
    app.multiselect(key="blq_tiendas").select("8").run()
    app.text_area(key="blq_texto").input("HP10201162490-N11").run()
    app.button(key="btn_bloquear").click().run()
    assert not app.exception, app.exception
    m = app.session_state.bloqueos_manuales
    assert (
        m.iloc[-1]["accion"] == "BLOQUEAR" and m.iloc[-1]["modelo_color_id"] == "HP10201162490-N11"
    )


def test_mantenedor_de_rutas_mueve_un_despacho(monkeypatch):
    """Un aprobador mueve el despacho de Plaza Norte del jueves 08/10 (feriado) al miércoles
    07/10: queda registrado y la ruta de esos días cambia."""
    import datetime as dt

    from forusight.data import rutas as RU

    app = _app(monkeypatch, USUARIOS)
    app.text_input[0].input("ana@forus.pe")
    app.text_input[1].input("clave-ana")
    app.button[0].click().run()
    app.switch_page("app/vistas/9_Rutas.py").run()
    assert not app.exception, app.exception
    app.date_input(key="rt_mov_desde").set_value(dt.date(2026, 10, 8))
    app.date_input(key="rt_mov_hacia").set_value(dt.date(2026, 10, 7))
    app.multiselect(key="rt_mov_malls").set_value(["Plaza Norte"])
    app.text_input(key="rt_mov_motivo").input("Feriado")
    app.run()
    app.button(key="btn_mover").click().run()
    assert not app.exception, app.exception
    exc = app.session_state.rutas_excepciones
    base = app.session_state.rutas_base
    assert len(exc) == 2
    assert RU.despacha("Plaza Norte", "2026-10-07", base, exc)
    assert not RU.despacha("Plaza Norte", "2026-10-08", base, exc)
    assert any("movido" in s.value for s in app.success)
