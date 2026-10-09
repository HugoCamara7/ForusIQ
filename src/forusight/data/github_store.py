"""Archivos de Forusight en GitHub: bloqueos manuales y maestro de planificación.

Mismo mecanismo que las solicitudes de Catálogo Control Center: archivos en una rama de
un repositorio privado vía la API de contenidos. Configuración (primera que exista):

  [forusight]  github_repository = "owner/repo", github_token = "...", github_branch = "..."
  [ticketing]  repository = "owner/repo", token = "...", branch = "..."   ← la del Catálogo

Se escribe bajo la carpeta ``forusight/`` (no toca los archivos del Catálogo). El token
necesita permiso *Contents: read & write* sobre ese repositorio.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


class GitHubError(RuntimeError):
    pass


class GitHubConflicto(GitHubError):
    """El archivo cambió entre leer su versión (sha) y guardarlo."""


#: Última escritura de cada archivo en este proceso: (sha que se reemplazó, sha nuevo). Justo
#: después de guardar, GitHub puede seguir devolviendo unos segundos la versión anterior; con el
#: sha viejo el siguiente guardado choca (409). Si la lectura trae el sha que ya se reemplazó, se
#: usa el nuevo.
_ULTIMA_ESCRITURA: dict[tuple[str, str, str], tuple[str | None, str]] = {}


def config_github(secrets: Mapping[str, Any] | None) -> dict | None:
    s = secrets or {}
    fz = dict(s.get("forusight", {}) or {})
    tk = dict(s.get("ticketing", {}) or {})
    repo = fz.get("github_repository") or tk.get("repository")
    if not repo and tk.get("owner") and tk.get("repo"):
        repo = f"{tk['owner']}/{tk['repo']}"
    token = fz.get("github_token") or tk.get("token")
    branch = fz.get("github_branch") or tk.get("branch") or "main"
    if not repo or not token or "/" not in str(repo) or str(token).startswith("GITHUB_TOKEN"):
        return None
    return {
        "repository": str(repo).strip(),
        "token": str(token).strip(),
        "branch": str(branch).strip(),
        "prefix": str(fz.get("github_prefix") or "forusight"),
    }


class GitHubStore:
    def __init__(
        self,
        repository: str,
        token: str,
        branch: str = "main",
        prefix: str = "forusight",
        timeout: int = 30,
        opener=urlopen,
        dormir=time.sleep,
    ) -> None:
        owner, repo = repository.split("/", 1)
        self.base = f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/contents"
        self.token, self.branch, self.prefix = token, branch, prefix.strip("/")
        self.timeout, self._open, self._dormir = timeout, opener, dormir
        self.repository = repository

    def _request(self, method: str, path: str, payload: dict | None = None):
        url = f"{self.base}/{quote(path, safe='/')}"
        if method == "GET":
            url += f"?ref={quote(self.branch)}"
        req = Request(
            url,
            data=json.dumps(payload).encode() if payload is not None else None,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "forusight",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._open(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 404 and method == "GET":
                return None
            detalle = exc.read().decode("utf-8", errors="replace")[:300]
            if exc.code == 409 or (exc.code == 422 and "sha" in detalle):
                raise GitHubConflicto(detalle) from exc
            raise GitHubError(
                f"GitHub respondió {exc.code} ({method}) en {self.repository} "
                f"(rama {self.branch}): {detalle}"
            ) from exc

    def guardar(self, ruta: str, contenido: bytes, mensaje: str, intentos: int = 4) -> str:
        """Crea o reemplaza ``prefix/ruta``. Devuelve la ruta escrita. Si otro guardado del mismo
        archivo se cruza (dos clics seguidos, dos usuarios), vuelve a leer la versión y reintenta
        en vez de mostrar el error de GitHub."""
        path = f"{self.prefix}/{ruta}"
        clave = (self.repository, self.branch, path)
        for intento in range(intentos):
            if intento:
                self._dormir(0.8 * intento)
            actual = self._request("GET", path)
            sha = actual.get("sha") if isinstance(actual, dict) else None
            previa = _ULTIMA_ESCRITURA.get(clave)
            if previa and sha == previa[0]:  # GitHub aún devuelve la versión ya reemplazada
                sha = previa[1]
            body = {
                "message": mensaje,
                "content": base64.b64encode(contenido).decode("ascii"),
                "branch": self.branch,
            }
            if sha:
                body["sha"] = sha
            try:
                r = self._request("PUT", path, body)
            except GitHubConflicto:
                _ULTIMA_ESCRITURA.pop(clave, None)
                continue
            nuevo = ((r or {}).get("content") or {}).get("sha")
            if nuevo:
                _ULTIMA_ESCRITURA[clave] = (sha, nuevo)
            return path
        raise GitHubError(
            "No se pudo guardar: el archivo se estaba guardando al mismo tiempo desde otro lado. "
            "Espera unos segundos y vuelve a intentarlo."
        )

    def borrar(self, ruta_completa: str, sha: str, mensaje: str) -> None:
        """Borra un archivo (ruta completa y sha, tal como los da ``listar``)."""
        _ULTIMA_ESCRITURA.pop((self.repository, self.branch, ruta_completa), None)
        self._request(
            "DELETE", ruta_completa, {"message": mensaje, "sha": sha, "branch": self.branch}
        )

    def listar(self, carpeta: str) -> list[dict]:
        """Archivos de ``prefix/carpeta`` (nombre, ruta completa); [] si no existe."""
        r = self._request("GET", f"{self.prefix}/{carpeta.strip('/')}")
        return [x for x in (r or []) if isinstance(x, dict) and x.get("type") == "file"]

    def leer(self, ruta_completa: str) -> bytes | None:
        """Contenido de un archivo (ruta completa, tal como la da ``listar``)."""
        r = self._request("GET", ruta_completa)
        if not isinstance(r, dict):
            return None
        if r.get("content"):
            return base64.b64decode(r["content"])
        if r.get("download_url"):  # archivos > 1 MB: la API no trae el contenido
            with self._open(
                Request(r["download_url"], headers={"User-Agent": "forusight"}),
                timeout=self.timeout,
            ) as f:
                return f.read()
        return None
