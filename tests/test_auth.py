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
    otro = {"app_auth": {"users": {"z@forus.pe": "1"}}}
    assert auth.rol("z@forus.pe", otro) == auth.ROL_ANALISTA  # mínimo privilegio por defecto
    assert auth.rol("admin", UNO) == auth.ROL_ADMIN  # opción A: usuario único = admin
    assert auth.puede(auth.ROL_APROBADOR, "aprobar")
    assert not auth.puede(auth.ROL_ANALISTA, "aprobar")
    assert not auth.puede(auth.ROL_APROBADOR, "conexion")
    assert auth.puede(auth.ROL_ADMIN, "parametros")
