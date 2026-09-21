from pathlib import Path
from uuid import uuid4

import pytest

from jhr_chiffrage.core import DomainError, Store, calculate, new_item
from jhr_chiffrage.offline import OfflineStore


CONFIG = {"mode": "server", "url": "https://server.test:8765", "token": "private-test-token", "ca_file": ""}


class Network:
    """Real server transactions behind a controllable network boundary."""
    def __init__(self, server):
        self.server = server
        self.connected = True
        self.lose_ack = False
        self.closed = False

    def check(self):
        if not self.connected:
            raise DomainError("CONNECTION_ERROR", "Network unavailable")

    def sync_snapshot(self):
        self.check()
        return self.server.sync_snapshot()

    def sync_push(self, changes, operation_id):
        self.check()
        result = self.server.sync_push(changes, operation_id=operation_id)
        if self.lose_ack:
            self.lose_ack = False
            self.connected = False
            raise DomainError("CONNECTION_ERROR", "Response lost after commit")
        return result

    def close(self):
        self.closed = True


@pytest.fixture
def setup(tmp_path):
    server = Store(tmp_path / "server.sqlite3")
    settings = server.get_settings()
    settings.update(hourly_rate="80", vat_rate="20", levy_rate="25")
    server.save_settings(settings, settings["revision"])
    remote = Network(server)
    return server, remote, tmp_path / "cache"


def working(remote, cache, config=CONFIG):
    return OfflineStore(config, remote=remote, cache_dir=cache)


def test_online_seed_includes_all_server_objects(setup):
    server, remote, cache = setup
    estimate = server.create_estimate("Affaire serveur")
    template = server.save_template({"name": "Gabarit serveur", "items": [new_item("Plan")]})
    local = working(remote, cache)
    assert local.online and local.pending_count == 0
    assert local.get_estimate(estimate["id"]) == estimate
    assert template in local.list_templates()
    assert local.get_settings() == server.get_settings()


def test_first_connection_required_without_default_empty_cache(setup):
    server, remote, cache = setup
    remote.connected = False
    with pytest.raises(DomainError) as exc:
        working(remote, cache)
    assert exc.value.code == "OFFLINE_NOT_READY"
    remote.connected = True
    estimate = server.create_estimate("Vraie affaire")
    local = working(remote, cache)
    assert local.get_estimate(estimate["id"]) == estimate


def test_offline_edits_survive_restart_and_reconnect(setup):
    server, remote, cache = setup
    local = working(remote, cache)
    remote.connected = False
    assert not local.synchronize()
    settings = local.get_settings()
    settings["hourly_rate"] = "100"
    local.save_settings(settings, settings["revision"])
    parent, child = new_item("Plan EXE"), new_item("Détail")
    parent.update(hours=None, duration_minutes=60)
    child.update(hours=None, duration_minutes=15, parent_id=parent["id"])
    template = local.save_template({"name": "Gabarit train", "items": [parent, child]})
    estimate = local.create_estimate("Chiffrage train")
    estimate["works"] = [{"id": str(uuid4()), "name": "Escalier", "items": [parent, child]}]
    estimate = local.save_estimate(estimate, estimate["revision"])
    assert calculate(estimate)["ht_cents"] == 12500
    assert local.pending_count == 3
    local.close()
    local = working(remote, cache)
    assert local.get_estimate(estimate["id"]) == estimate
    assert template in local.list_templates()
    assert local.pending_count == 3
    assert server.list_estimates() == []
    remote.connected = True
    assert local.synchronize()
    assert local.pending_count == 0
    assert server.get_estimate(estimate["id"]) == estimate
    assert server.get_settings()["hourly_rate"] == "100"
    assert template in server.list_templates()


def test_lost_ack_retry_after_restart_does_not_duplicate(setup):
    server, remote, cache = setup
    local = working(remote, cache)
    estimate = local.create_estimate("Dans le train")
    remote.lose_ack = True
    assert not local.synchronize()
    assert server.get_estimate(estimate["id"]) == estimate
    local.close()
    local = working(remote, cache)
    remote.connected = True
    assert local.synchronize()
    assert local.pending_count == 0
    assert len(server.list_estimates()) == 1


def test_lost_ack_then_more_edits_replays_durable_receipt(setup):
    server, remote, cache = setup
    local = working(remote, cache)
    estimate = local.create_estimate("Première saisie")
    remote.lose_ack = True
    assert not local.synchronize()
    local.close()
    local = working(remote, cache)
    estimate["name"] = "Complété hors réseau"
    estimate = local.save_estimate(estimate, estimate["revision"])
    second = local.create_estimate("Deuxième affaire")
    local.close()
    local = working(remote, cache)
    remote.connected = True
    assert local.synchronize()
    assert local.get_estimate(estimate["id"]) == estimate
    assert local.get_estimate(second["id"]) == second
    # Replaying the durable receipt acknowledges the old batch only; later
    # offline edits remain pending and are sent by the next synchronization.
    assert local.pending_count == 2
    assert local.synchronize()
    assert local.pending_count == 0
    assert server.get_estimate(estimate["id"]) == estimate
    assert server.get_estimate(second["id"]) == second
    assert len(server.list_estimates()) == 2


def test_conflicts_preserve_both_then_explicit_copy_resolution(setup):
    server, remote, cache = setup
    estimate = server.create_estimate("Affaire commune")
    local = working(remote, cache)
    ours = local.get_estimate(estimate["id"])
    ours["name"] = "Travail train"
    ours = local.save_estimate(ours, ours["revision"])
    theirs = server.get_estimate(estimate["id"])
    theirs["name"] = "Travail bureau"
    theirs = server.save_estimate(theirs, theirs["revision"])
    with pytest.raises(DomainError) as exc:
        local.synchronize()
    assert exc.value.code == "SYNC_CONFLICT"
    assert local.has_conflict
    assert local.get_estimate(estimate["id"]) == ours
    assert server.get_estimate(estimate["id"]) == theirs
    backup = Path(local.resolve_conflict_keep_both())
    assert backup.is_file()
    assert Store(backup).get_estimate(estimate["id"]) == ours
    copies = [e for e in local.list_estimates() if e["id"] != estimate["id"]]
    assert len(copies) == 1
    assert copies[0]["name"] == "Travail train (copie hors ligne)"
    assert local.get_estimate(estimate["id"]) == theirs
    assert local.synchronize()
    assert len(server.list_estimates()) == 2
    assert server.get_estimate(estimate["id"]) == theirs


def test_independent_server_and_offline_affairs_merge(setup):
    server, remote, cache = setup
    local = working(remote, cache)
    ours = local.create_estimate("Train")
    theirs = server.create_estimate("Bureau")
    assert local.synchronize()
    assert local.get_estimate(theirs["id"]) == theirs
    assert server.get_estimate(ours["id"]) == ours
    assert len(local.list_estimates()) == len(server.list_estimates()) == 2


def test_offline_freeze_and_revision_survive_sync(setup):
    server, remote, cache = setup
    local = working(remote, cache)
    estimate = local.create_estimate("Version train")
    item = new_item("Plan")
    item.update(hours=None, duration_minutes=30)
    estimate["works"] = [{"id": str(uuid4()), "name": "Ouvrage", "items": [item]}]
    estimate = local.save_estimate(estimate, estimate["revision"])
    frozen = local.freeze_estimate(estimate["id"], estimate["revision"])
    revised = local.revise_estimate(frozen["id"], expected_revision=frozen["revision"])
    assert local.synchronize()
    assert server.get_estimate(frozen["id"]) == frozen
    assert server.get_estimate(revised["id"]) == revised
    assert revised["parent_id"] == frozen["id"]


@pytest.mark.parametrize("field,value", [("url", "https://other.test:8765"), ("token", "different-token")])
def test_cache_profiles_cannot_borrow_another_servers_affairs(setup, field, value):
    server, remote, cache = setup
    local = working(remote, cache)
    local.create_estimate("Données privées")
    remote.connected = False
    with pytest.raises(DomainError) as exc:
        working(remote, cache, dict(CONFIG, **{field: value}))
    assert exc.value.code == "OFFLINE_NOT_READY"
    assert working(remote, cache).list_estimates()[0]["name"] == "Données privées"
    assert all(CONFIG["token"] not in p.name for p in cache.iterdir())
