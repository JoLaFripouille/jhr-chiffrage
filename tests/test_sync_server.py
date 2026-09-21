from copy import deepcopy
from uuid import uuid4

import pytest
from starlette.testclient import TestClient

from jhr_chiffrage.core import DomainError, Store, new_item
from jhr_chiffrage.server import build_app


@pytest.fixture
def stores(tmp_path):
    server = Store(tmp_path / "server.sqlite3")
    settings = server.get_settings()
    settings.update(hourly_rate="80", vat_rate="20", levy_rate="25")
    server.save_settings(settings, settings["revision"])
    server.backup(tmp_path / "offline.sqlite3")
    return server, Store(tmp_path / "offline.sqlite3")


def changes(server, offline):
    baseline = {(x["kind"], x["id"]): x["body"] for x in server.sync_snapshot()["objects"]}
    return [{**x, "base": baseline.get((x["kind"], x["id"]))}
            for x in offline.sync_snapshot()["objects"]
            if baseline.get((x["kind"], x["id"])) != x["body"]]


def complete(store, name):
    estimate = store.create_estimate(name)
    item = new_item("Dessin")
    item["duration_minutes"] = 60
    estimate["works"] = [{"id": str(uuid4()), "name": "Ouvrage", "items": [item]}]
    return store.save_estimate(estimate, estimate["revision"])


def test_offline_create_edit_freeze_revise_and_retry(stores):
    server, offline = stores
    estimate = complete(offline, "Hors réseau")
    estimate["name"] = "Affaire préparée dans le train"
    estimate = offline.save_estimate(estimate, estimate["revision"])
    frozen = offline.freeze_estimate(estimate["id"], estimate["revision"])
    revised = offline.revise_estimate(frozen["id"])
    batch = changes(server, offline)
    assert server.sync_push(batch, operation_id="trip-1") == {"applied": 2}
    assert server.sync_push(batch, operation_id="trip-1") == {"applied": 2}
    assert server.get_estimate(frozen["id"]) == frozen
    assert server.get_estimate(revised["id"]) == revised
    assert server.sync_snapshot() == offline.sync_snapshot()
    modified = deepcopy(batch)
    modified[0]["body"]["name"] = "Different"
    with pytest.raises(DomainError, match="autre contenu"):
        server.sync_push(modified, operation_id="trip-1")


def test_conflict_rolls_back_whole_batch(stores):
    server, offline = stores
    created = offline.create_estimate("Doit rester local")
    settings = offline.get_settings()
    settings["hourly_rate"] = "90"
    offline.save_settings(settings, settings["revision"])
    batch = changes(server, offline)
    settings = server.get_settings()
    settings["hourly_rate"] = "100"
    server.save_settings(settings, settings["revision"])
    before = server.sync_snapshot()
    with pytest.raises(DomainError) as exc:
        server.sync_push(batch, operation_id="conflict")
    assert exc.value.code == "SYNC_CONFLICT"
    assert server.sync_snapshot() == before
    assert offline.get_estimate(created["id"])["name"] == "Doit rester local"


def test_settings_templates_and_existing_estimate_sync(stores):
    server, offline = stores
    original = server.create_estimate("Préparée au bureau")
    with offline.connection() as db:
        offline._put(db, "estimate", original["id"], original)
        db.commit()
    edited = {**original, "name": "Complétée dans le train"}
    offline.save_estimate(edited, original["revision"])
    settings = offline.get_settings()
    settings["hourly_rate"] = "95"
    offline.save_settings(settings, settings["revision"])
    template = offline.list_templates()[0]
    template["name"] = "Plan modifié"
    offline.save_template(template, template["revision"])
    offline.save_template({"name": "Nouveau gabarit", "items": []})
    assert server.sync_push(changes(server, offline)) == {"applied": 4}
    assert server.sync_snapshot() == offline.sync_snapshot()


def test_invalid_late_object_does_not_write_earlier_valid_one(stores):
    server, offline = stores
    offline.create_estimate("Premier")
    offline.create_estimate("Second")
    batch = changes(server, offline)
    batch[-1]["body"]["name"] = ""
    before = server.sync_snapshot()
    with pytest.raises(DomainError):
        server.sync_push(batch)
    assert server.sync_snapshot() == before


def test_unchanged_revision_and_duplicate_object_rejected(stores):
    server, offline = stores
    settings = offline.get_settings()
    body = {**settings, "hourly_rate": "90"}
    batch = [{"kind": "settings", "id": "default", "base": settings, "body": body}]
    with pytest.raises(DomainError):
        server.sync_push(batch)
    body["revision"] += 1
    with pytest.raises(DomainError):
        server.sync_push(batch * 2)
    assert server.get_settings() == settings


@pytest.mark.parametrize("mutation", [
    lambda x: x.update(kind="receipts"),
    lambda x: x.update(id="bad-id"),
    lambda x: x["body"].update(id=str(uuid4())),
    lambda x: x["body"].update(revision=True),
    lambda x: x["body"].update(revision=0),
    lambda x: x["body"].update(status="other"),
    lambda x: x["body"].update(version=2),
    lambda x: x["body"].update(frozen_totals={}),
    lambda x: x["body"].update(works="invalid"),
])
def test_invalid_batches_leave_server_unchanged(stores, mutation):
    server, offline = stores
    offline.create_estimate("Test")
    batch = changes(server, offline)
    mutation(batch[0])
    before = server.sync_snapshot()
    with pytest.raises(DomainError):
        server.sync_push(batch)
    assert server.sync_snapshot() == before


def test_frozen_server_estimate_cannot_be_modified(stores):
    server, _ = stores
    estimate = complete(server, "Figé")
    frozen = server.freeze_estimate(estimate["id"], estimate["revision"])
    altered = {**frozen, "name": "Altéré", "revision": frozen["revision"] + 1}
    with pytest.raises(DomainError) as exc:
        server.sync_push([{"kind": "estimate", "id": frozen["id"], "base": frozen, "body": altered}])
    assert exc.value.code == "LOCKED_VERSION"
    assert server.get_estimate(frozen["id"]) == frozen


def test_forged_frozen_total_rejected(stores):
    server, offline = stores
    estimate = complete(offline, "Figé")
    offline.freeze_estimate(estimate["id"], estimate["revision"])
    batch = changes(server, offline)
    batch[0]["body"]["frozen_totals"]["ht_cents"] = 100
    with pytest.raises(DomainError):
        server.sync_push(batch)


def test_duplicate_revision_number_conflicts(stores):
    server, _ = stores
    estimate = complete(server, "Famille")
    frozen = server.freeze_estimate(estimate["id"], estimate["revision"])
    revision = server.revise_estimate(frozen["id"])
    duplicate = {**revision, "id": str(uuid4())}
    with pytest.raises(DomainError) as exc:
        server.sync_push([{"kind": "estimate", "id": duplicate["id"], "base": None, "body": duplicate}])
    assert exc.value.code == "SYNC_CONFLICT"


def test_https_rpc_capability_and_authenticated_sync(stores):
    server, offline = stores
    token = "x" * 48
    offline.create_estimate("RPC")
    with TestClient(build_app(server, token)) as client:
        assert client.get("/api/v1/health").status_code == 401
        client.headers["Authorization"] = "Bearer " + token
        assert client.get("/api/v1/health").json()["result"]["offline_sync_version"] == 1
        response = client.post("/api/v1/call", json={"method": "sync_push", "params": {"changes": changes(server, offline), "operation_id": "rpc-1"}})
        assert response.status_code == 200
        assert response.json()["result"]["applied"] == 1
        snapshot = client.post("/api/v1/call", json={"method": "sync_snapshot", "params": {}})
        assert snapshot.json()["result"] == offline.sync_snapshot()
        assert server.list_changes()[0]["actor"] == "remote"
