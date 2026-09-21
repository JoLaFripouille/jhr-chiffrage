import json
from pathlib import Path

import httpx
import pytest

from jhr_chiffrage.connection import RemoteStore, load_connection, open_store, save_connection
from jhr_chiffrage.core import DomainError


CONFIG = {"mode": "server", "url": "https://server.test:8765", "token": "test-secret", "ca_file": ""}


def remote(handler, **kwargs):
    return RemoteStore(CONFIG, transport=httpx.MockTransport(handler), **kwargs)


def test_connection_config_roundtrip(tmp_path):
    path = tmp_path / "private" / "connection.json"
    assert load_connection(path, environ={})["mode"] == "local"
    save_connection(CONFIG, path)
    assert load_connection(path, environ={}) == CONFIG
    assert list(path.parent.iterdir()) == [path]


def test_environment_never_borrows_disk_credentials(tmp_path):
    path = tmp_path / "connection.json"
    save_connection(CONFIG, path)
    with pytest.raises(DomainError) as exc:
        load_connection(path, environ={"JHR_SERVER_URL": "https://other.test"})
    assert exc.value.code == "CONNECTION_CONFIG"
    assert load_connection(path, environ={"JHR_CHIFFRAGE_DB": "test.sqlite3"})["mode"] == "local"
    assert load_connection(path, environ={"JHR_CHIFFRAGE_DB": "test.sqlite3", "JHR_SERVER_URL": CONFIG["url"],
                                          "JHR_SERVER_TOKEN": CONFIG["token"]}) == CONFIG


@pytest.mark.parametrize("url", ["http://server.test", "https://a:b@server.test", "https://server.test/path", "https://server.test?token=secret", "https://server.test:bad"])
def test_unsafe_urls_rejected(url):
    with pytest.raises(DomainError):
        RemoteStore(dict(CONFIG, url=url))


def test_invalid_file_does_not_fallback_local(tmp_path):
    path = tmp_path / "connection.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(DomainError):
        load_connection(path, environ={})
    with pytest.raises(DomainError):
        open_store(dict(CONFIG, ca_file=str(tmp_path / "missing.pem")))


def test_mutations_have_operation_id_revision_and_server_actor():
    requests = []
    def handler(request):
        assert request.headers["Authorization"] == "Bearer test-secret"
        assert request.url.path == "/api/v1/call"
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"result": {"revision": 3}})
    client = remote(handler)
    assert client.save_estimate({"id": "x"}, 2)["revision"] == 3
    assert requests[0]["method"] == "save_estimate"
    assert requests[0]["params"]["expected_revision"] == 2
    assert requests[0]["params"]["operation_id"]
    assert "actor" not in requests[0]["params"]
    client.save_estimate({"id": "x"}, 2, operation_id="stable-id")
    assert requests[1]["params"]["operation_id"] == "stable-id"
    client.close()


def test_conflict_retains_domain_code():
    client = remote(lambda request: httpx.Response(409, json={"error": {"code": "REVISION_CONFLICT", "message": "Rechargez l'affaire."}}))
    with pytest.raises(DomainError) as exc:
        client.save_estimate({"id": "x"}, 1)
    assert exc.value.code == "REVISION_CONFLICT"


def test_lost_write_is_not_retried_or_secret_leaked():
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("test-secret server.test private")
    client = remote(handler)
    with pytest.raises(DomainError) as exc:
        client.create_estimate("name")
    assert len(calls) == 1
    assert exc.value.code == "CONNECTION_ERROR"
    assert "test-secret" not in str(exc.value)
    assert "server.test" not in str(exc.value)


def test_export_uses_local_generated_path(tmp_path):
    client = remote(lambda request: httpx.Response(200, json={"result": {"filename": "../../secret.json", "content": '{"name":"test"}'}}), exports_dir=tmp_path / "exports")
    target = Path(client.export_estimate("x"))
    assert target.parent == tmp_path / "exports"
    assert target.name.startswith("chiffrage-")
    assert json.loads(target.read_text(encoding="utf-8")) == {"name": "test"}


def test_health_and_backup():
    def handler(request):
        if request.url.path.endswith("health"):
            return httpx.Response(200, json={"result": {"api_version": 1, "version": "0.2.0"}})
        return httpx.Response(200, json={"result": {"filename": "copy.sqlite3"}})
    client = remote(handler)
    assert client.health()["api_version"] == 1
    assert client.backup() == "Sauvegarde créée sur le serveur : copy.sqlite3"


def test_incompatible_health_rejected():
    client = remote(lambda request: httpx.Response(200, json={"result": {"api_version": 2}}))
    with pytest.raises(DomainError) as exc:
        client.health()
    assert exc.value.code == "SERVER_VERSION"


def test_redirect_is_never_followed():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://elsewhere.test"})
    with pytest.raises(DomainError):
        remote(handler).get_settings()
    assert len(calls) == 1
