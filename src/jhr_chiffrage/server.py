"""Authenticated HTTPS transport for the shared Store; no remote filesystem API."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import hmac
import inspect
import ipaddress
import json
import logging
from pathlib import Path
import ssl

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import __version__
from .core import DomainError, Store

LOGGER = logging.getLogger(__name__)
MAX_BODY = 2 * 1024 * 1024
BACKUP_INTERVAL = 24 * 60 * 60


def error(code, message, status=400):
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def build_app(store: Store, token: str) -> Starlette:
    if not isinstance(token, str) or len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError("Jeton ASCII sans espaces de 32 caractères minimum requis.")
    expected_auth = ("Bearer " + token).encode("ascii")

    def backup():
        return {"filename": Path(store.backup()).name}

    def export_estimate(id, kind="commercial"):
        target = Path(store.export_estimate(id, kind))
        return {"filename": target.name, "content": target.read_text(encoding="utf-8")}

    # Deliberately explicit: new Store methods never become remotely callable by accident.
    methods = {
        "get_settings": store.get_settings,
        "sync_snapshot": store.sync_snapshot,
        "sync_push": store.sync_push,
        "save_settings": store.save_settings,
        "list_estimates": store.list_estimates,
        "get_estimate": store.get_estimate,
        "create_estimate": store.create_estimate,
        "save_estimate": store.save_estimate,
        "freeze_estimate": store.freeze_estimate,
        "revise_estimate": store.revise_estimate,
        "refresh_estimate_settings": store.refresh_estimate_settings,
        "list_templates": store.list_templates,
        "save_template": store.save_template,
        "apply_template": store.apply_template,
        "list_changes": store.list_changes,
        "backup": backup,
        "export_estimate": export_estimate,
    }

    def authorized(request):
        supplied = request.headers.get("authorization", "").encode("utf-8")
        return hmac.compare_digest(supplied, expected_auth)

    async def health(request):
        if not authorized(request):
            return error("UNAUTHORIZED", "Connexion non autorisée.", 401)
        return JSONResponse({"result": {"version": __version__, "api_version": 1, "offline_sync_version": 1}})

    async def call(request: Request):
        if not authorized(request):
            return error("UNAUTHORIZED", "Connexion non autorisée.", 401)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_BODY:
                return error("REQUEST_TOO_LARGE", "Requête trop volumineuse.", 413)
            body.extend(chunk)
        try:
            payload = json.loads(body, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeDecodeError, RecursionError):
            return error("VALIDATION_ERROR", "Requête JSON invalide.")
        if not isinstance(payload, dict) or set(payload) != {"method", "params"}:
            return error("VALIDATION_ERROR", "Méthode et paramètres requis.")
        name, params = payload["method"], payload["params"]
        if not isinstance(name, str) or name not in methods:
            return error("UNKNOWN_METHOD", "Opération non disponible.")
        if not isinstance(params, dict) or "actor" in params:
            return error("VALIDATION_ERROR", "Paramètres invalides.")
        function = methods[name]
        signature = inspect.signature(function)
        if "actor" in signature.parameters:
            params = {**params, "actor": "remote"}
        try:
            signature.bind(**params)
        except TypeError:
            return error("VALIDATION_ERROR", "Arguments absents ou non autorisés.")
        try:
            result = await run_in_threadpool(function, **params)
            return JSONResponse({"result": result})
        except DomainError as exc:
            status = {"REVISION_CONFLICT": 409, "IDEMPOTENCY_CONFLICT": 409, "SYNC_CONFLICT": 409,
                      "DATABASE_BUSY": 503, "NOT_FOUND": 404}.get(exc.code, 400)
            return error(exc.code, str(exc), status)
        except (TypeError, ValueError, KeyError, AttributeError, RecursionError):
            return error("VALIDATION_ERROR", "Contenu des paramètres invalide.")
        except Exception as exc:
            # Exception messages/traces may contain private paths or customer data.
            LOGGER.error("Server operation failed: %s (%s)", name, type(exc).__name__)
            return error("SERVER_ERROR", "Erreur interne du serveur.", 500)

    async def periodic_backup():
        while True:
            await asyncio.sleep(BACKUP_INTERVAL)
            try:
                await run_in_threadpool(store.backup)
            except Exception as exc:
                LOGGER.error("Scheduled backup failed (%s)", type(exc).__name__)

    @asynccontextmanager
    async def lifespan(app):
        # Refuse startup if the initial coherent backup cannot be created.
        await run_in_threadpool(store.backup)
        task = asyncio.create_task(periodic_backup())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    return Starlette(routes=[Route("/api/v1/health", health),
                             Route("/api/v1/call", call, methods=["POST"])],
                     lifespan=lifespan, debug=False)


def load_config(path):
    config = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    required = {"host", "port", "database", "token", "certfile", "keyfile"}
    if not isinstance(config, dict) or set(config) != required:
        raise ValueError("Configuration serveur invalide.")
    ipaddress.ip_address(config["host"])
    if type(config["port"]) is not int or not 1 <= config["port"] <= 65535:
        raise ValueError("Port invalide.")
    for key in ("database", "certfile", "keyfile"):
        if not isinstance(config[key], str) or not Path(config[key]).is_absolute():
            raise ValueError("Chemins absolus requis.")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(config["certfile"], config["keyfile"])
    return config


def main():
    parser = argparse.ArgumentParser(description="Serveur HTTPS privé JHR Chiffrage")
    parser.add_argument("--config", required=True, help="Configuration JSON privée")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        config = load_config(args.config)
        app = build_app(Store(config["database"]), config["token"])
    except Exception as exc:
        LOGGER.error("Invalid server configuration (%s)", type(exc).__name__)
        parser.exit(2, "Impossible de démarrer : vérifiez la configuration privée et le certificat.\n")
    import uvicorn
    uvicorn.run(app, host=config["host"], port=config["port"],
                ssl_certfile=config["certfile"], ssl_keyfile=config["keyfile"],
                access_log=False, proxy_headers=False, server_header=False)


if __name__ == "__main__":
    main()
