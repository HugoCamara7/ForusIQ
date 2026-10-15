"""Smoke test de la UI con streamlit.testing (sin navegador, datos sintéticos)."""

from pathlib import Path

import pandas as pd
import pytest

st_testing = pytest.importorskip("streamlit.testing.v1")
RAIZ = Path(__file__).resolve().parents[1]
PAGINAS = [
    "1_Dashboard",
    "10_Descargas",
    "8_Bloqueos",
    "13_Temporadas_Tienda",
    "9_Rutas",
    "5_Parametros",
]  # vista única + configuración


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
    # icono de descarga bajo el velocímetro: disponibilidad por tienda
    assert "btn_disp_tienda" in {b.key for b in app.get("download_button")}
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
    """Bloquear y desbloquear solo se hace subiendo Excel: no hay controles manuales."""
    app = _app(monkeypatch)
    app.switch_page("app/vistas/8_Bloqueos.py").run()
    assert not app.exception, app.exception
    claves = {w.key for w in app.multiselect} | {w.key for w in app.text_area}
    assert not claves & {"blq_tiendas", "blq_modelos", "blq_texto"}
    assert "btn_bloquear" not in {b.key for b in app.button}
    app.text_input(key="dbl_texto").input("HP").run()
    assert not app.exception, app.exception
    assert not app.get("arrow_data_editor")
    assert "btn_desbloquear" not in {b.key for b in app.button}


def test_rutas_un_solo_cuadro_y_feriado_con_un_clic(monkeypatch):
    """Un aprobador marca feriado el jueves 08/10 desde el cuadro de la semana."""
    import datetime as dt

    from forusight.data import rutas as RU

    app = _app(monkeypatch, USUARIOS)
    app.text_input[0].input("ana@forus.pe")
    app.text_input[1].input("clave-ana")
    app.button[0].click().run()
    app.switch_page("app/vistas/9_Rutas.py").run()
    assert not app.exception, app.exception
    app.date_input(key="rt_fecha").set_value(dt.date(2026, 10, 8)).run()
    assert app.session_state.rt_fecha == dt.date(2026, 10, 5)  # se ajusta al lunes
    app.pills(key="rt_feriados").set_value(["JU 08/10"]).run()
    assert not app.exception, app.exception
    exc = app.session_state.rutas_excepciones
    assert RU.feriados(exc, RU.fechas_semana("2026-10-08")) == [pd.Timestamp("2026-10-08")]
    assert any("Feriados de la semana: JU 08/10" in s.value for s in app.success)
    app.segmented_control(key="rt_modo").set_value("Ruta fija").run()
    assert not app.exception, app.exception


def test_descargas_lista_las_corridas_de_la_semana(monkeypatch):
    """La página Descargas muestra un botón por cada Excel guardado en el repositorio."""
    import app.components.corridas_guardadas as CG

    from forusight.data import corridas as C

    hoy = C.hoy_lima()

    class Store:
        def listar(self, carpeta):
            if carpeta != C.carpeta_del_mes(hoy):
                return []
            nombre = C.nombre_diario(hoy)
            return [{"name": nombre, "path": f"forusight/{carpeta}/{nombre}", "sha": "1"}]

        def leer(self, path):
            return b"xlsx"

    monkeypatch.setattr(CG, "_store_bloqueos", lambda: Store())
    CG._listado.clear()
    app = _app(monkeypatch)
    app.switch_page("app/vistas/10_Descargas.py").run()
    assert not app.exception, app.exception
    assert [b.label for b in app.get("download_button")] == ["Descargar"]
    assert any(f"{hoy:%d/%m/%Y}" in m.value for m in app.markdown)


def test_parametros_mantenedores_buscar_y_guardar(monkeypatch):
    from app.components import estado

    estado._MANT.clear()
    app = _app(monkeypatch)
    app.switch_page("app/vistas/5_Parametros.py").run()
    app.text_input(key="mt_buscar").input("HP").run()
    app.text_input(key="mt_smt_tienda").input("8").run()
    app.text_input(key="mt_smt_sku").input("123456").run()
    assert not app.exception, app.exception
    app.button(key="mt_smt_guardar").click().run()  # tienda × SKU nueva: se crea con 1
    assert not app.exception, app.exception
    from forusight.data import mantenedores as MT

    assert estado._MANT[MT.RUTA_STOCK_MINIMO]["sku"].tolist() == ["123456"]
    estado._MANT.clear()


PERMISOS = {
    "app_auth": {
        "users": {"ana@forus.pe": "clave-ana", "luis@forus.pe": "clave-luis"},
        "roles": {"ana@forus.pe": "admin", "luis@forus.pe": "analista"},
        "permisos": {"parametros": ["luis@forus.pe"]},
    }
}


def _ingresar(monkeypatch, usuario, clave):
    app = _app(monkeypatch, PERMISOS)
    app.text_input[0].input(usuario)
    app.text_input[1].input(clave)
    app.button[0].click().run()
    assert not app.exception, app.exception
    return app


def _menu(app) -> list[str]:
    return [p.proto.label for p in app.get("page_link")]


def test_parametros_solo_para_usuarios_autorizados(monkeypatch):
    """Ni un admin entra a Parámetros si no está en [app_auth.permisos] parametros: la página
    no se registra (tampoco por URL) y no aparece en el menú."""
    app = _ingresar(monkeypatch, "ana@forus.pe", "clave-ana")
    assert "Parámetros" not in _menu(app)
    with pytest.raises(ValueError, match="Could not find a navigation page"):
        app.switch_page("app/vistas/5_Parametros.py")
    app = _ingresar(monkeypatch, "luis@forus.pe", "clave-luis")
    assert "Parámetros" in _menu(app)
    app.switch_page("app/vistas/5_Parametros.py").run()
    assert not app.exception, app.exception
    assert not app.error


def test_menu_sin_manual_ni_comparar(monkeypatch):
    app = _app(monkeypatch)
    menu = _menu(app)
    assert "Manual" not in menu and "Comparar con Neogística" not in menu
    assert menu[:2] == ["Inicio", "Descargas"]
    assert {"Bloqueos", "Temporadas por tienda", "Rutas"} <= set(menu)
    secciones = [m.value for m in app.sidebar.markdown if 'class="sb-sec"' in m.value]
    assert any("Mantenedores" in s for s in secciones)


def test_corrida_usa_la_temporada_minima_por_tienda(monkeypatch):
    """Lo guardado en «Temporadas por tienda» llega a los parámetros de la corrida."""
    from app.components import estado

    from forusight.config.settings import EngineParams
    from forusight.data import mantenedores as MT

    estado._MANT.clear()
    app = _app(monkeypatch)
    app.switch_page("app/vistas/13_Temporadas_Tienda.py").run()
    assert not app.exception, app.exception
    cambios = pd.DataFrame({"tienda_id": ["8"], "temporada_minima": ["INVIERNO 2026"]})
    estado._MANT[MT.RUTA_TEMPORADA_TIENDA] = MT.nuevos_temporada_tienda(
        cambios, MT.ASIGNAR, "demo", "2026-10-15"
    )
    p = EngineParams.model_validate_json(
        estado.con_temporadas_tienda(EngineParams().model_dump_json())
    )
    assert p.surtido.temporada_desde_tienda == {"8": "INVIERNO 2026"}
    app.switch_page("app/vistas/1_Dashboard.py").run()
    app.button(key="btn_ejecutar").click().run()
    assert not app.exception, app.exception
    assert app.session_state.resultado.params.surtido.temporada_desde_tienda == {
        "8": "INVIERNO 2026"
    }
    estado._MANT.clear()
