import pytest

from forusight import auth

MULTI = {
    "app_auth": {
        "users": {"Hugo.Camara@Forus.pe": "s3cr3t", "ana@forus.pe": "x"},
        "roles": {"hugo.camara@forus.pe": "Administrador", "ana@forus.pe": "aprobador"},
    }
}
UNO = {"app_auth": {"username": "admin", "password": "clave"}}


def test_usuarios_multiples_normalizados():
    assert auth.usuarios(MULTI) == {"hugo.camara@forus.pe": "s3cr3t", "ana@forus.pe": "x"}


def test_usuario_unico_y_sin_configuracion():
    assert auth.usuarios(UNO) == {"admin": "clave"}
    assert auth.usuarios({}) == {} and auth.usuarios(None) == {}
    assert auth.usuarios({"app_auth": {"username": "admin"}}) == {}  # sin clave no hay usuario


@pytest.mark.parametrize(
    ("usuario", "clave", "ok"),
    [
        ("hugo.camara@forus.pe", "s3cr3t", True),
        ("  HUGO.CAMARA@forus.pe ", "s3cr3t", True),
        ("hugo.camara@forus.pe", "S3CR3T", False),
        ("nadie@forus.pe", "s3cr3t", False),
        ("", "", False),
        ("ana@forus.pe", "", False),
    ],
)
def test_verificar(usuario, clave, ok):
    assert auth.verificar(usuario, clave, MULTI) is ok


def test_roles_y_permisos():
    assert auth.rol("hugo.camara@forus.pe", MULTI) == auth.ROL_ADMIN
    assert auth.rol("ana@forus.pe", MULTI) == auth.ROL_APROBADOR
    con_roles = {
        "app_auth": {
            "users": {"z@forus.pe": "1", "a@forus.pe": "2"},
            "roles": {"a@forus.pe": "admin"},
        }
    }
    assert auth.rol("z@forus.pe", con_roles) == auth.ROL_ANALISTA  # no listado → mínimo
    assert auth.rol("admin", UNO) == auth.ROL_ADMIN
    assert auth.puede(auth.ROL_APROBADOR, "aprobar")
    assert not auth.puede(auth.ROL_ANALISTA, "aprobar")
    assert not auth.puede(auth.ROL_APROBADOR, "conexion")
    # Parámetros no es de un rol: sólo de la lista [app_auth.permisos] parametros
    assert not auth.puede(auth.ROL_ADMIN, "parametros")


def test_secrets_de_catalogo_funcionan_tal_cual():
    """Sin [app_auth.roles] (Catálogo) todos entran como admin; roles del Catálogo se traducen."""
    catalogo = {"app_auth": {"users": {"hugo.camara@forus.pe": "x", "luis.nunez@forus.pe": "y"}}}
    assert auth.rol("luis.nunez@forus.pe", catalogo) == auth.ROL_ADMIN
    traducidos = {
        "app_auth": {
            "users": {"a@f.pe": "1", "b@f.pe": "2", "c@f.pe": "3"},
            "roles": {"a@f.pe": "operator", "b@f.pe": "brand", "c@f.pe": "administrador"},
        }
    }
    assert auth.rol("a@f.pe", traducidos) == auth.ROL_APROBADOR
    assert auth.rol("b@f.pe", traducidos) == auth.ROL_ANALISTA
    assert auth.rol("c@f.pe", traducidos) == auth.ROL_ADMIN


PERMISOS = {
    "app_auth": {
        "users": {"uno@forus.pe": "1", "dos@forus.pe": "2", "tres@forus.pe": "3"},
        "permisos": {"parametros": [" UNO@forus.pe", "dos@forus.pe", "fantasma@forus.pe"]},
    }
}


def test_parametros_solo_para_la_lista_de_los_secrets():
    assert auth.acceso_restringido("uno@forus.pe", "parametros", PERMISOS)
    assert auth.acceso_restringido(" Dos@Forus.pe ", "parametros", PERMISOS)
    assert not auth.acceso_restringido("tres@forus.pe", "parametros", PERMISOS)  # admin sin roles
    assert auth.rol("tres@forus.pe", PERMISOS) == auth.ROL_ADMIN
    # en la lista pero sin usuario para iniciar sesión: nada
    assert not auth.acceso_restringido("fantasma@forus.pe", "parametros", PERMISOS)
    assert not auth.acceso_restringido("", "parametros", PERMISOS)
    # sin lista configurada nadie entra (ni los admin)
    assert not auth.acceso_restringido("hugo.camara@forus.pe", "parametros", MULTI)
    texto = {"app_auth": {**PERMISOS["app_auth"], "permisos": {"parametros": "uno@forus.pe,"}}}
    assert auth.autorizados("parametros", texto) == {"uno@forus.pe"}


def test_correos_autorizados_no_estan_en_el_codigo():
    """Los correos con acceso a Parámetros viven en los secrets, no en el código fuente."""
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[1]
    fuentes = [*raiz.glob("app/**/*.py"), *raiz.glob("src/**/*.py"), raiz / "streamlit_app.py"]
    for f in fuentes:
        texto = f.read_text(encoding="utf-8").lower()
        assert "luis.nunez@" not in texto, f
        assert "hugo.camara@" not in texto, f
