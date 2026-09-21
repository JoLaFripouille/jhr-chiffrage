import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from jhr_chiffrage.core import Store, DomainError, new_item
from jhr_chiffrage.server import build_app, MAX_BODY, load_config

TOKEN = "test-token-" + "a" * 40
HEADERS = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
def service(tmp_path):
    store = Store(tmp_path / "database.sqlite3")
    with TestClient(build_app(store, TOKEN)) as client:
        yield store, client


def rpc(client, method, **params):
    return client.post("/api/v1/call", headers=HEADERS, json={"method": method, "params": params})


def test_auth_and_health(service):
    store, client = service
    assert client.get("/api/v1/health").status_code == 401
    assert client.post("/api/v1/call", json={}).status_code == 401
    assert client.get("/api/v1/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/v1/health", headers=HEADERS).json()["result"]["api_version"] == 1
    assert len(list((store.path.parent / "backups").glob("*.sqlite3"))) == 1


def test_remote_revision_and_idempotence(service):
    store, client = service
    first = rpc(client, "create_estimate", name="Test", operation_id="request-1").json()["result"]
    assert rpc(client, "create_estimate", name="Test", operation_id="request-1").json()["result"] == first
    saved = rpc(client, "save_estimate", data=first, expected_revision=first["revision"])
    assert saved.status_code == 200
    conflict = rpc(client, "save_estimate", data=first, expected_revision=first["revision"])
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "REVISION_CONFLICT"
    assert all(event["actor"] == "remote" for event in store.list_changes())


@pytest.mark.parametrize("method,params", [
    ("connection", {}), ("_put", {}), ("path", {}),
    ("backup", {"destination": "C:/arbitrary.sqlite3"}),
    ("create_estimate", {"name": "Test", "actor": "ui"}),
    ("get_estimate", {}), ("get_settings", {"unknown": 1}),
])
def test_no_unsafe_operations(service, method, params):
    _, client = service
    assert rpc(client, method, **params).status_code == 400


def test_body_limit_and_invalid_json(service):
    _, client = service
    assert client.post("/api/v1/call", headers=HEADERS, content=b" " * (MAX_BODY + 1)).status_code == 413
    for content in [b"broken", b"[]", b"{\"method\":NaN,\"params\":{}}", b"\xff"]:
        assert client.post("/api/v1/call", headers=HEADERS, content=content).status_code == 400


def test_export_and_backup_hide_server_paths(service):
    store, client = service
    settings = store.get_settings()
    settings.update(hourly_rate="80", vat_rate="20", levy_rate="25")
    store.save_settings(settings, settings["revision"])
    obj = rpc(client, "create_estimate", name="Empty test").json()["result"]
    item = new_item("Test line")
    item["duration_minutes"] = 60
    obj["works"] = [{"id": "work-test", "name": "Work", "items": [item]}]
    store.save_estimate(obj, obj["revision"])
    response = rpc(client, "export_estimate", id=obj["id"])
    assert response.status_code == 200, response.text
    exported = response.json()["result"]
    assert set(exported) == {"filename", "content"}
    assert Path(exported["filename"]).name == exported["filename"]
    assert json.loads(exported["content"])["name"] == "Empty test"
    backup = rpc(client, "backup").json()["result"]
    assert set(backup) == {"filename"}
    assert Path(backup["filename"]).name == backup["filename"]
    assert (store.path.parent / "backups" / backup["filename"]).exists()


def test_internal_errors_sanitized_and_busy_mapped(tmp_path, monkeypatch, caplog):
    store = Store(tmp_path / "db.sqlite3")
    def fail():
        raise RuntimeError("secret token and private path")
    monkeypatch.setattr(store, "get_settings", fail)
    with TestClient(build_app(store, TOKEN)) as client:
        response = rpc(client, "get_settings")
    assert response.status_code == 500
    assert "secret" not in response.text
    assert "secret" not in caplog.text
    def busy():
        raise DomainError("DATABASE_BUSY", "Base occupée.")
    monkeypatch.setattr(store, "get_settings", busy)
    with TestClient(build_app(store, TOKEN)) as client:
        assert rpc(client, "get_settings").status_code == 503


def test_requires_strong_token_and_tls_config(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    for token in ["short", "é" * 40, "a " * 30, None]:
        with pytest.raises(ValueError):
            build_app(store, token)
    path = tmp_path / "server.json"
    path.write_text(json.dumps({"host": "127.0.0.1", "port": 8765, "database": str(store.path), "token": TOKEN}))
    with pytest.raises(ValueError):
        load_config(path)
