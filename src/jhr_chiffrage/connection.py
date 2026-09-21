"""Explicit local/server selection and verified TLS transport."""
from __future__ import annotations

import json
import os
from pathlib import Path
import ssl
import tempfile
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from .core import DomainError, Store


def connection_path():
    return Path.home() / ".jhr-chiffrage" / "connection.json"


def _validated(config):
    result = {key: config.get(key, "") for key in ("mode", "url", "token", "ca_file")}
    if any(not isinstance(value, str) for value in result.values()):
        raise DomainError("CONNECTION_CONFIG", "La configuration de connexion est invalide.")
    if result["mode"] not in ("local", "server"):
        raise DomainError("CONNECTION_CONFIG", "Choisissez le mode local ou serveur.")
    if result["mode"] == "server":
        result["url"] = result["url"].strip().rstrip("/")
        try:
            parsed = urlsplit(result["url"])
            valid = parsed.scheme == "https" and parsed.hostname and parsed.port != 0
        except ValueError:
            valid = False
        if not valid or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
            raise DomainError("CONNECTION_CONFIG", "Saisissez une adresse HTTPS de serveur, sans chemin ni identifiants.")
        if not result["token"].strip() or any(c.isspace() for c in result["token"]):
            raise DomainError("CONNECTION_CONFIG", "Le jeton de connexion est manquant ou invalide.")
    return result


def load_connection(path=None, environ=None):
    env = os.environ if environ is None else environ
    # An environment configuration is a whole profile, never mixed with disk credentials.
    if any(key in env for key in ("JHR_SERVER_URL", "JHR_SERVER_TOKEN", "JHR_SERVER_CA")):
        return _validated({"mode": "server", "url": env.get("JHR_SERVER_URL", ""),
                           "token": env.get("JHR_SERVER_TOKEN", ""), "ca_file": env.get("JHR_SERVER_CA", "")})
    if env.get("JHR_CHIFFRAGE_DB"):
        return {"mode": "local", "url": "", "token": "", "ca_file": ""}
    target = Path(path) if path is not None else connection_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError()
        return _validated(data)
    except FileNotFoundError:
        return {"mode": "local", "url": "", "token": "", "ca_file": ""}
    except (OSError, ValueError):
        raise DomainError("CONNECTION_CONFIG", "Impossible de lire la configuration de connexion.") from None


def save_connection(config, path=None):
    data = _validated(config)
    target = Path(path) if path is not None else connection_path()
    temporary = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".connection-", dir=target.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    except OSError:
        raise DomainError("CONNECTION_CONFIG", "Impossible d'enregistrer la configuration de connexion.") from None
    finally:
        if temporary and Path(temporary).exists():
            Path(temporary).unlink()
    return data


def open_store(config=None):
    config = load_connection() if config is None else _validated(config)
    if config["mode"] == "server":
        from .offline import OfflineStore
        return OfflineStore(config)
    return Store()


class RemoteStore:
    """Store-compatible synchronous client. Writes are sent once, with an operation ID."""

    def __init__(self, config, *, transport=None, exports_dir=None):
        self.config = _validated(config)
        if self.config["mode"] != "server":
            raise DomainError("CONNECTION_CONFIG", "Le mode serveur est requis.")
        try:
            context = ssl.create_default_context(cafile=self.config["ca_file"] or None)
        except (OSError, ssl.SSLError):
            raise DomainError("CONNECTION_CONFIG", "Le certificat du serveur est illisible ou invalide.") from None
        self._client = httpx.Client(base_url=self.config["url"], verify=context, timeout=5,
                                   headers={"Authorization": "Bearer " + self.config["token"]},
                                   transport=transport, trust_env=False, follow_redirects=False)
        self.exports_dir = Path(exports_dir) if exports_dir else Path.home() / ".jhr-chiffrage" / "exports"

    def close(self):
        self._client.close()

    def _request(self, method, path, **kwargs):
        try:
            response = self._client.request(method, path, **kwargs)
        except (httpx.HTTPError, OSError):
            raise DomainError("CONNECTION_ERROR", "Serveur injoignable ou connexion non sécurisée. Aucun basculement local n'a été effectué. Après un enregistrement interrompu, rechargez l'affaire pour vérifier son état.") from None
        try:
            body = response.json()
        except (ValueError, UnicodeError):
            raise DomainError("CONNECTION_ERROR", "Réponse du serveur invalide.") from None
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            error = body["error"]
            if isinstance(error.get("code"), str) and isinstance(error.get("message"), str):
                raise DomainError(error["code"], error["message"])
        if not response.is_success or not isinstance(body, dict):
            raise DomainError("CONNECTION_ERROR", "Le serveur a refusé la demande. Vérifiez la connexion et le jeton.")
        return body

    def health(self):
        result = self._request("GET", "/api/v1/health").get("result")
        if not isinstance(result, dict) or result.get("api_version") != 1:
            raise DomainError("SERVER_VERSION", "Cette version du serveur n'est pas compatible avec l'application.")
        return result

    def _call(self, method, params=None, *, mutation=False):
        params = dict(params or {})
        params.pop("actor", None)  # Attribution is assigned by the trusted server.
        if mutation and not params.get("operation_id"):
            params["operation_id"] = str(uuid4())
        body = self._request("POST", "/api/v1/call", json={"method": method, "params": params})
        if "result" not in body:
            raise DomainError("CONNECTION_ERROR", "Réponse du serveur incomplète.")
        return body["result"]

    def get_settings(self):
        return self._call("get_settings")

    def sync_snapshot(self):
        return self._call("sync_snapshot")

    def sync_push(self, changes, operation_id):
        return self._call("sync_push", dict(changes=changes, operation_id=operation_id), mutation=True)

    def save_settings(self, data, expected_revision, actor="ui", operation_id=None):
        return self._call("save_settings", dict(data=data, expected_revision=expected_revision, actor=actor, operation_id=operation_id), mutation=True)

    def list_estimates(self):
        return self._call("list_estimates")

    def get_estimate(self, id):
        return self._call("get_estimate", dict(id=id))

    def create_estimate(self, name, client="", reference="", actor="ui", operation_id=None):
        return self._call("create_estimate", dict(name=name, client=client, reference=reference, actor=actor, operation_id=operation_id), mutation=True)

    def save_estimate(self, data, expected_revision, actor="ui", operation_id=None):
        return self._call("save_estimate", dict(data=data, expected_revision=expected_revision, actor=actor, operation_id=operation_id), mutation=True)

    def freeze_estimate(self, id, expected_revision, actor="ui", operation_id=None):
        return self._call("freeze_estimate", dict(id=id, expected_revision=expected_revision, actor=actor, operation_id=operation_id), mutation=True)

    def revise_estimate(self, id, actor="ui", operation_id=None, expected_revision=None):
        return self._call("revise_estimate", dict(id=id, expected_revision=expected_revision, actor=actor, operation_id=operation_id), mutation=True)

    def refresh_estimate_settings(self, id, expected_revision, actor="ui", operation_id=None):
        return self._call("refresh_estimate_settings", dict(id=id, expected_revision=expected_revision, actor=actor, operation_id=operation_id), mutation=True)

    def list_templates(self):
        return self._call("list_templates")

    def save_template(self, data, expected_revision=None, actor="ui", operation_id=None):
        return self._call("save_template", dict(data=data, expected_revision=expected_revision, actor=actor, operation_id=operation_id), mutation=True)

    def apply_template(self, estimate_id, template_id, work_id, expected_revision, actor="ui", operation_id=None):
        return self._call("apply_template", dict(estimate_id=estimate_id, template_id=template_id, work_id=work_id, expected_revision=expected_revision, actor=actor, operation_id=operation_id), mutation=True)

    def list_changes(self, limit=50):
        return self._call("list_changes", dict(limit=limit))

    def backup(self, destination=None):
        if destination is not None:
            raise DomainError("VALIDATION_ERROR", "La destination des sauvegardes est gérée par le serveur.")
        result = self._call("backup")
        filename = str(result.get("filename", "")) if isinstance(result, dict) else ""
        return "Sauvegarde créée sur le serveur : " + filename.replace("\\", "/").rsplit("/", 1)[-1]

    def export_estimate(self, id, kind="commercial"):
        result = self._call("export_estimate", dict(id=id, kind=kind))
        if not isinstance(result, dict) or not isinstance(result.get("content"), str):
            raise DomainError("CONNECTION_ERROR", "Le contenu de l'export est invalide.")
        try:
            self.exports_dir.mkdir(parents=True, exist_ok=True)
            target = self.exports_dir / ("chiffrage-" + uuid4().hex + ".json")
            with target.open("x", encoding="utf-8") as handle:
                handle.write(result["content"])
        except OSError:
            raise DomainError("EXPORT_ERROR", "Impossible d'enregistrer l'export sur cet ordinateur.") from None
        return str(target)
