"""Autenticación y roles, con el mismo esquema de secrets que Catálogo/Repo Control Center.

```toml
[app_auth]
username = "admin"            # opción A: un usuario
password = "..."

[app_auth.users]              # opción B: varios usuarios (reemplaza a la A)
"nombre.apellido@forus.pe" = "..."

[app_auth.roles]              # opcional: admin | aprobador | analista
"nombre.apellido@forus.pe" = "aprobador"

[app_auth.permisos]           # módulos restringidos a una lista de usuarios
parametros = ["nombre.apellido@forus.pe"]
```

El módulo **Parámetros** sólo lo ven y usan los usuarios de ``[app_auth.permisos]
parametros`` (además deben poder iniciar sesión). Sin esa lista nadie accede: los correos no
se escriben en el código, viven en los secrets.

No hay usuarios por defecto: tenerlos en el código sería publicarlos. Roles:
  - sin sección [app_auth.roles]: todos los usuarios son admin (igual que en Catálogo, así
    los secrets se pegan tal cual);
  - con [app_auth.roles]: quien no esté listado queda como ``analista`` (mínimo privilegio).
Los roles del Catálogo se traducen: admin → admin, operator → aprobador, brand → analista.
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from typing import Any

ROL_ADMIN = "admin"
ROL_APROBADOR = "aprobador"
ROL_ANALISTA = "analista"
ROL_POR_DEFECTO = ROL_ANALISTA

_ALIAS_ROL = {
    "admin": ROL_ADMIN,
    "administrador": ROL_ADMIN,
    "aprobador": ROL_APROBADOR,
    "approver": ROL_APROBADOR,
    "planificador": ROL_APROBADOR,
    "analista": ROL_ANALISTA,
    "analyst": ROL_ANALISTA,
    "lector": ROL_ANALISTA,
    "operator": ROL_APROBADOR,
    "operador": ROL_APROBADOR,
    "operaciones": ROL_APROBADOR,
    "brand": ROL_ANALISTA,
    "marca": ROL_ANALISTA,
    "comercial": ROL_ANALISTA,
}

# Qué puede hacer cada rol. «parametros» no es de un rol: es de la lista de
# [app_auth.permisos] parametros (ver acceso_restringido).
PERMISOS = {
    ROL_ADMIN: {"ver", "ejecutar", "aprobar", "conexion"},
    ROL_APROBADOR: {"ver", "ejecutar", "aprobar"},
    ROL_ANALISTA: {"ver", "ejecutar"},
}


def normalizar(valor: Any) -> str:
    return str(valor or "").strip().lower()


def _seccion(secrets: Mapping[str, Any] | None) -> dict:
    try:
        return dict((secrets or {}).get("app_auth", {}) or {})
    except Exception:
        return {}


def usuarios(secrets: Mapping[str, Any] | None) -> dict[str, str]:
    """usuario normalizado → contraseña. Vacío si no hay [app_auth]."""
    cfg = _seccion(secrets)
    lista = cfg.get("users")
    if lista:
        return {normalizar(u): str(p) for u, p in dict(lista).items() if normalizar(u) and p}
    u, p = normalizar(cfg.get("username", "")), str(cfg.get("password", "") or "")
    return {u: p} if u and p else {}


def rol(usuario: str, secrets: Mapping[str, Any] | None) -> str:
    seccion = _seccion(secrets)
    roles = {normalizar(k): normalizar(v) for k, v in dict(seccion.get("roles", {}) or {}).items()}
    if not roles:
        return ROL_ADMIN if normalizar(usuario) in usuarios(secrets) else ROL_POR_DEFECTO
    return _ALIAS_ROL.get(roles.get(normalizar(usuario), ""), ROL_POR_DEFECTO)


def verificar(usuario: str, clave: str, secrets: Mapping[str, Any] | None) -> bool:
    """Comparación en tiempo constante; nunca revela si el usuario existe."""
    esperado = usuarios(secrets).get(normalizar(usuario))
    candidato = str(clave or "").encode()
    referencia = (esperado or "\x00" * 16).encode()
    ok = hmac.compare_digest(candidato, referencia)
    return bool(esperado) and ok


def puede(rol_usuario: str, accion: str) -> bool:
    return accion in PERMISOS.get(rol_usuario, set())


#: Módulos que no dependen del rol sino de una lista explícita de usuarios en los secrets.
RESTRINGIDOS = {"parametros"}


def autorizados(modulo: str, secrets: Mapping[str, Any] | None) -> set[str]:
    """Usuarios (normalizados) de ``[app_auth.permisos] <modulo>``. Vacío = nadie."""
    try:
        permisos = dict(_seccion(secrets).get("permisos", {}) or {})
    except Exception:
        return set()
    lista = permisos.get(modulo) or []
    if isinstance(lista, str):  # «a@x, b@x» también vale
        lista = lista.split(",")
    return {normalizar(u) for u in lista if normalizar(u)}


def acceso_restringido(usuario: str, modulo: str, secrets: Mapping[str, Any] | None) -> bool:
    """True sólo si ``usuario`` es un usuario con login configurado y está en la lista del
    módulo. Comparación en tiempo constante, sin revelar quién está en la lista."""
    u = normalizar(usuario)
    if not u or u not in usuarios(secrets):
        return False
    ok = False
    for autorizado in autorizados(modulo, secrets):
        ok |= hmac.compare_digest(u.encode(), autorizado.encode())
    return ok
