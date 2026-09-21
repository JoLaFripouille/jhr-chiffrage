from copy import deepcopy
from decimal import Decimal
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest


def test_backup_releases_file_handle(tmp_path):
    from jhr_chiffrage.core import Store
    from pathlib import Path
    store = Store(tmp_path / "original.sqlite3")
    backup = Path(store.backup())
    # Windows refuses this rename while a SQLite connection still owns the file.
    moved = backup.with_name("moved.sqlite3")
    backup.rename(moved)
    assert moved.is_file()

from jhr_chiffrage.core import Store, DomainError, calculate, new_item


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "test.sqlite3")
    settings = result.get_settings()
    settings.update(hourly_rate="80", vat_rate="20", levy_rate="25")
    result.save_settings(settings, settings["revision"])
    return result


def filled(store):
    estimate = store.create_estimate("Escalier et garde-corps", "Client test", "TEST-001")
    estimate["works"] = []
    for name, durations in [("Garde-corps", [5, 1]), ("Escalier", [8, 2])]:
        items = [new_item(f"Poste {i + 1}") for i in range(len(durations))]
        for item, duration in zip(items, durations):
            item["hours"] = str(duration)
        estimate["works"].append({"id": str(uuid4()), "name": name, "items": items})
    return store.save_estimate(estimate, estimate["revision"])


def test_reference_and_persistence(store):
    estimate = filled(store)
    result = calculate(estimate)
    assert {k: result[k] for k in ("ht_cents", "vat_cents", "ttc_cents", "levy_cents", "balance_cents")} == {
        "ht_cents": 128000, "vat_cents": 25600, "ttc_cents": 153600, "levy_cents": 32000, "balance_cents": 96000}
    assert Decimal(result["hours"]) == 16
    assert not result["incomplete"]
    assert Store(store.path).get_estimate(estimate["id"]) == estimate


def test_settings_snapshot_and_explicit_refresh(store):
    estimate = filled(store)
    settings = store.get_settings()
    settings["hourly_rate"] = "100"
    store.save_settings(settings, settings["revision"])
    assert calculate(store.get_estimate(estimate["id"]))["ht_cents"] == 128000
    current = store.refresh_estimate_settings(estimate["id"], estimate["revision"])
    assert calculate(current)["ht_cents"] == 160000


def test_rounding_fixed_and_no_vat(store):
    obj = filled(store)
    obj["works"] = [obj["works"][0]]
    for item in obj["works"][0]["items"]:
        item.update(mode="fixed", quantity="1", price="0.03", hours="999", estimated_hours="0.5")
    result = calculate(obj)
    assert result["ht_cents"] == 6 and result["vat_cents"] == 1
    assert Decimal(result["hours"]) == 1
    obj["works"][0]["items"][0]["price"] = "10.005"
    assert calculate(obj)["lines"][0]["ht_cents"] == 1001
    obj["settings"]["vat_enabled"] = False
    assert calculate(obj)["vat_cents"] == 0


def test_quantity_decimal_comma(store):
    obj = filled(store)
    obj["works"] = [obj["works"][0]]
    obj["works"][0]["items"] = [obj["works"][0]["items"][0]]
    obj["works"][0]["items"][0].update(quantity="3", hours="1,5")
    result = calculate(obj)
    assert result["ht_cents"] == 36000 and Decimal(result["hours"]) == Decimal("4.5")


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "1e999999", "1e-999999", True, "abc"])
def test_invalid_input_rolls_back(store, value):
    obj = filled(store)
    before = deepcopy(obj)
    obj["works"][0]["items"][0]["hours"] = value
    with pytest.raises(DomainError):
        store.save_estimate(obj, obj["revision"])
    assert store.get_estimate(obj["id"]) == before


def test_freeze_and_revision_independent(store):
    obj = filled(store)
    frozen = store.freeze_estimate(obj["id"], obj["revision"])
    with pytest.raises(DomainError, match="figée"):
        store.save_estimate(frozen, frozen["revision"])
    revision = store.revise_estimate(frozen["id"])
    assert revision["version"] == 2 and revision["status"] == "draft"
    revision["works"][0]["items"][0]["hours"] = "99"
    store.save_estimate(revision, revision["revision"])
    assert store.get_estimate(frozen["id"]) == frozen
    assert store.revise_estimate(frozen["id"])["version"] == 3


def test_idempotence_and_conflicts(store):
    first = store.create_estimate("A", actor="mcp", operation_id="create-1")
    assert store.create_estimate("A", actor="mcp", operation_id="create-1") == first
    with pytest.raises(DomainError) as error:
        store.create_estimate("B", actor="mcp", operation_id="create-1")
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    second_store = Store(store.path)
    first["client"] = "Nouveau client"
    second_store.save_estimate(first, first["revision"])
    with pytest.raises(DomainError) as error:
        store.save_estimate(first, first["revision"])
    assert error.value.code == "REVISION_CONFLICT"
    assert len(store.list_estimates()) == 1


def test_templates_are_copies_and_missing_times_block_freeze(store):
    obj = filled(store)
    template = store.list_templates()[0]
    updated = store.apply_template(obj["id"], template["id"], obj["works"][0]["id"], obj["revision"], actor="mcp", operation_id="apply1")
    assert store.apply_template(obj["id"], template["id"], obj["works"][0]["id"], obj["revision"], actor="mcp", operation_id="apply1") == updated
    template["items"][0]["hours"] = "100"
    store.save_template(template, template["revision"])
    assert store.get_estimate(obj["id"]) == updated
    with pytest.raises(DomainError, match="incomplet"):
        store.freeze_estimate(updated["id"], updated["revision"])


def test_backup_and_export_separation(store):
    obj = filled(store)
    backup = store.backup()
    restored = Store(backup)
    assert restored.get_estimate(obj["id"]) == obj
    with open(store.export_estimate(obj["id"]), encoding="utf-8") as handle:
        content = handle.read()
    assert "levy" not in content and "balance" not in content and "hourly_rate" not in content
    assert json.loads(content)["totals"]["ht_cents"] == 128000
    assert any(event["operation"] == "save_estimate" for event in restored.list_changes())


def test_unconfigured_zero_not_assumed(tmp_path):
    store = Store(tmp_path / "fresh.sqlite3")
    obj = store.create_estimate("Nouveau")
    assert calculate(obj)["incomplete"]
    settings = store.get_settings()
    with pytest.raises(DomainError):
        store.save_settings(settings, settings["revision"])


@pytest.mark.skipif(os.name != "nt", reason="Emplacement Windows")
def test_windows_storage_independent_of_virtualized_appdata(tmp_path, monkeypatch):
    monkeypatch.delenv("JHR_CHIFFRAGE_DB", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "virtualized"))
    assert Store().path == (tmp_path / ".jhr-chiffrage" / "chiffrage.sqlite3").resolve()


def test_settings_change_cannot_hide_inside_estimate_save(store):
    obj = filled(store)
    obj["settings"]["hourly_rate"] = "1"
    with pytest.raises(DomainError, match="paramètres"):
        store.save_estimate(obj, obj["revision"])
    assert store.get_estimate(obj["id"])["settings"]["hourly_rate"] == "80"


def test_disabled_vat_accepts_blank_rate(store):
    settings = store.get_settings()
    settings.update(vat_enabled=False, vat_rate="")
    assert store.save_settings(settings, settings["revision"])["vat_rate"] == "0"


@pytest.mark.parametrize("minutes, quantity, expected_cents, expected_hours", [
    (1, "3", 400, "0.05"), (90, "1", 12000, "1.5"), (0, "1", 0, "0"),
])
def test_minute_duration_exact_pricing(store, minutes, quantity, expected_cents, expected_hours):
    obj = filled(store)
    item = new_item("Dessin")
    item.update(duration_minutes=minutes, quantity=quantity)
    obj["works"] = [{"id": str(uuid4()), "name": "Plan", "items": [item]}]
    saved = store.save_estimate(obj, obj["revision"])
    result = calculate(saved)
    assert result["ht_cents"] == expected_cents
    assert Decimal(result["hours"]) == Decimal(expected_hours)
    assert store.get_estimate(saved["id"])["works"][0]["items"][0]["duration_minutes"] == minutes


def test_fixed_minutes_are_total_charge_not_multiplied_by_quantity(store):
    obj = filled(store)
    item = new_item("Dessin")
    item.update(mode="fixed", quantity="3", price="100", estimated_minutes=90)
    obj["works"] = [{"id": str(uuid4()), "name": "Plan", "items": [item]}]
    result = calculate(obj)
    assert result["ht_cents"] == 30000
    assert Decimal(result["hours"]) == Decimal("1.5")


@pytest.mark.parametrize("mode,key,hours_key", [
    ("hourly", "duration_minutes", "hours"), ("fixed", "estimated_minutes", "estimated_hours"),
])
@pytest.mark.parametrize("minutes", [True, False, "90", 1.0, 1.5, -1, 60_000_000_001, 10**100])
def test_invalid_minute_fields_rejected(store, mode, key, hours_key, minutes):
    obj = filled(store)
    item = obj["works"][0]["items"][0]
    item.update(mode=mode, price="100")
    item[hours_key] = None
    item[key] = minutes
    with pytest.raises(DomainError, match="minutes entières"):
        store.save_estimate(obj, obj["revision"])


@pytest.mark.parametrize("mode,key,hours_key", [
    ("hourly", "duration_minutes", "hours"), ("fixed", "estimated_minutes", "estimated_hours"),
])
def test_ambiguous_durations_rejected(store, mode, key, hours_key):
    obj = filled(store)
    item = obj["works"][0]["items"][0]
    item.update(mode=mode, price="100")
    item[key], item[hours_key] = 90, "1.5"
    with pytest.raises(DomainError, match="ambiguë"):
        calculate(obj)


def test_legacy_fractional_hours_unchanged(store):
    obj = filled(store)
    item = new_item("Fraction ancienne")
    item.update(hours="0.333333", quantity="3")
    obj["works"] = [{"id": str(uuid4()), "name": "Plan", "items": [item]}]
    saved = store.save_estimate(obj, obj["revision"])
    assert saved["works"][0]["items"][0]["hours"] == "0.333333"
    result = calculate(saved)
    assert result["ht_cents"] == 8000
    assert Decimal(result["hours"]) == Decimal("0.999999")


def hierarchical_items():
    parent, child, grandchild = [new_item(label) for label in ("Plan", "Coupe", "Détail")]
    parent.update(duration_minutes=60, quantity="2")
    child.update(parent_id=parent["id"], duration_minutes=15)
    grandchild.update(parent_id=child["id"], mode="fixed", price="30", estimated_minutes=30)
    # Child first deliberately: stored array order must not constrain links.
    return [grandchild, parent, child]


def test_subposts_roundtrip_and_each_node_priced_once(store):
    obj = store.create_estimate("Sous-postes")
    obj["works"] = [{"id": str(uuid4()), "name": "Plan", "items": hierarchical_items()}]
    saved = store.save_estimate(obj, obj["revision"])
    assert Store(store.path).get_estimate(saved["id"]) == saved
    result = calculate(saved)
    assert result["ht_cents"] == 21000
    assert Decimal(result["hours"]) == Decimal("2.75")
    assert len(result["lines"]) == 3
    assert sum(line["ht_cents"] for line in result["lines"]) == result["ht_cents"]


@pytest.mark.parametrize("invalid", ["missing", "foreign", "self", "cycle", "empty", "number", "list"])
def test_invalid_hierarchy_rejected_atomically(store, invalid):
    obj = filled(store)
    before = deepcopy(obj)
    first, second = obj["works"][0]["items"]
    if invalid == "cycle":
        first["parent_id"], second["parent_id"] = second["id"], first["id"]
    else:
        first["parent_id"] = {"missing": "missing", "foreign": obj["works"][1]["items"][0]["id"],
                              "self": first["id"], "empty": "", "number": 1, "list": []}[invalid]
    with pytest.raises(DomainError):
        store.save_estimate(obj, obj["revision"])
    assert store.get_estimate(obj["id"]) == before


def test_hierarchy_survives_template_application_and_revision(store):
    source = hierarchical_items()
    template = store.save_template({"name": "Plan détaillé", "items": source})
    obj = store.create_estimate("Sous-postes")
    obj["works"] = [{"id": str(uuid4()), "name": "Plan", "items": []}]
    obj = store.save_estimate(obj, obj["revision"])
    for _ in range(2):
        obj = store.apply_template(obj["id"], template["id"], obj["works"][0]["id"], obj["revision"])
    frozen = store.freeze_estimate(obj["id"], obj["revision"])
    revised = store.revise_estimate(frozen["id"])
    original_ids = {item["id"] for item in source}
    applied_ids = {item["id"] for item in frozen["works"][0]["items"]}
    revised_ids = {item["id"] for item in revised["works"][0]["items"]}
    assert len(applied_ids) == len(revised_ids) == 6
    assert not original_ids & applied_ids
    assert not applied_ids & revised_ids
    for estimate in (frozen, revised):
        items = estimate["works"][0]["items"]
        for group in (items[:3], items[3:]):
            grandchild, parent, child = group
            assert grandchild["parent_id"] == child["id"]
            assert child["parent_id"] == parent["id"]
            assert parent.get("parent_id") is None
        assert calculate(estimate)["ht_cents"] == 42000
    assert next(t for t in store.list_templates() if t["id"] == template["id"])["items"] == source


def test_deep_hierarchy_uses_iterative_validation(store):
    obj = store.create_estimate("Hiérarchie profonde")
    items = []
    for index in range(1500):
        item = new_item(str(index))
        item.update(duration_minutes=0, parent_id=items[-1]["id"] if items else None)
        items.append(item)
    obj["works"] = [{"id": "deep-work", "name": "Plan", "items": list(reversed(items))}]
    assert calculate(obj)["ht_cents"] == 0
    items[0]["parent_id"] = items[-1]["id"]
    with pytest.raises(DomainError, match="cycle"):
        calculate(obj)
