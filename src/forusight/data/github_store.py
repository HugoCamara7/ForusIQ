"""Guardado de aprobaciones en GitHub (sin dataset de BigQuery).

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
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


class GitHubError(RuntimeError):
    pass


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
    ) -> None:
        owner, repo = repository.split("/", 1)
        self.base = f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/contents"
        self.token, self.branch, self.prefix = token, branch, prefix.strip("/")
        self.timeout, self._open = timeout, opener
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
            raise GitHubError(
                f"GitHub respondió {exc.code} al guardar en {self.repository} "
                f"(rama {self.branch}): {detalle}"
            ) from exc

    def guardar(self, ruta: str, contenido: bytes, mensaje: str) -> str:
        """Crea o reemplaza ``prefix/ruta``. Devuelve la ruta escrita."""
        path = f"{self.prefix}/{ruta}"
        actual = self._request("GET", path)
        body = {
            "message": mensaje,
            "content": base64.b64encode(contenido).decode("ascii"),
            "branch": self.branch,
        }
        if isinstance(actual, dict) and actual.get("sha"):
            body["sha"] = actual["sha"]
        self._request("PUT", path, body)
        return path

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
