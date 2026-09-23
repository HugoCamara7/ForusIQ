"""Autenticación y roles, con el mismo esquema de secrets que Catálogo/Repo Control Center.

```toml
[app_auth]
username = "admin"            # opción A: un usuario
password = "..."

[app_auth.users]              # opción B: varios usuarios (reemplaza a la A)
"nombre.apellido@forus.pe" = "..."

[app_auth.roles]              # opcional: admin | aprobador | analista
"nombre.apellido@forus.pe" = "aprobador"
```

No hay usuarios por defecto: tenerlos en el código sería publicarlos. Un usuario sin rol
explícito queda como ``analista`` (mínimo privilegio), al revés que en Catálogo, donde
el rol por defecto es admin.
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
}

# Qué puede hacer cada rol.
PERMISOS = {
    ROL_ADMIN: {"ver", "ejecutar", "aprobar", "parametros", "conexion"},
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
    roles = {
        normalizar(k): normalizar(v)
        for k, v in dict(_seccion(secrets).get("roles", {}) or {}).items()
    }
    usuario = normalizar(usuario)
    if usuario in roles:
        return _ALIAS_ROL.get(roles[usuario], ROL_POR_DEFECTO)
    # Opción A (un solo usuario): es el administrador de la app.
    if not _seccion(secrets).get("users") and usuarios(secrets):
        return ROL_ADMIN
    return ROL_POR_DEFECTO


def verificar(usuario: str, clave: str, secrets: Mapping[str, Any] | None) -> bool:
    """Comparación en tiempo constante; nunca revela si el usuario existe."""
    esperado = usuarios(secrets).get(normalizar(usuario))
    candidato = str(clave or "").encode()
    referencia = (esperado or "\x00" * 16).encode()
    ok = hmac.compare_digest(candidato, referencia)
    return bool(esperado) and ok


def puede(rol_usuario: str, accion: str) -> bool:
    return accion in PERMISOS.get(rol_usuario, set())
