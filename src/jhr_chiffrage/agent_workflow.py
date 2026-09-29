"""Agent preparation of lots and nested ouvrages, committed as one revision."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from .core import DomainError, calculate, clone_items, move_item_branch, new_item, MAX_DURATION_MINUTES


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ItemFields(StrictModel):
    label: str | None = None
    mode: Literal["hourly", "fixed"] | None = None
    quantity: str | None = None
    duration_minutes: int | None = Field(default=None, strict=True, ge=0, le=MAX_DURATION_MINUTES)
    rate: str | None = None
    price: str | None = None
    estimated_minutes: int | None = Field(default=None, strict=True, ge=0, le=MAX_DURATION_MINUTES)
    cctp_reference: str | None = Field(default=None, max_length=2000)
    time_basis: str | None = Field(default=None, max_length=4000)
    notes: str | None = Field(default=None, max_length=4000)


class CopySource(StrictModel):
    estimate_id: str
    revision: int = Field(ge=1)
    lot_id: str
    item_id: str | None = None


class Change(StrictModel):
    action: Literal["add_lot", "rename_lot", "copy_lot", "add_ouvrage", "add_post", "copy_ouvrage", "update_post", "move_post", "remove_post", "remove_lot"]
    key: str | None = Field(default=None, pattern=r"^@[A-Za-z][A-Za-z0-9_-]{0,63}$")
    lot_id: str | None = None
    item_id: str | None = None
    parent_id: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=1000)
    fields: ItemFields | None = None
    source: CopySource | None = None
    target_lot_id: str | None = None
    target_item_id: str | None = None
    placement: Literal["before", "after", "inside", "end"] = "end"

    @model_validator(mode="after")
    def required_and_relevant(self):
        rules = {
            "add_lot": ({"name"}, {"key"}),
            "rename_lot": ({"lot_id", "name"}, set()),
            "copy_lot": ({"source", "name"}, {"key"}),
            "add_ouvrage": ({"lot_id", "name"}, {"key", "fields"}),
            "add_post": ({"lot_id", "fields"}, {"key", "parent_id"}),
            "copy_ouvrage": ({"lot_id", "source", "name"}, {"key", "parent_id", "fields"}),
            "update_post": ({"lot_id", "item_id", "fields"}, set()),
            "move_post": ({"lot_id", "item_id", "target_lot_id"}, {"target_item_id", "placement"}),
            "remove_post": ({"lot_id", "item_id"}, set()),
            "remove_lot": ({"lot_id"}, set()),
        }
        required, optional = rules[self.action]
        if any(getattr(self, key) is None for key in required):
            raise ValueError(f"{self.action}: required fields {sorted(required)}")
        extras = self.model_fields_set - required - optional - {"action"}
        if extras:
            raise ValueError(f"{self.action}: irrelevant fields {sorted(extras)}")
        if self.action == "add_post" and not self.fields.label:
            raise ValueError("add_post requires fields.label")
        if self.source and self.action == "copy_lot" and self.source.item_id:
            raise ValueError("copy_lot copies a whole tab; use copy_ouvrage for a branch")
        return self


def fail(message, code="VALIDATION_ERROR"):
    raise DomainError(code, message)


def branch_ids(items, item_id):
    children = {}
    for item in items:
        children.setdefault(item.get("parent_id"), []).append(item["id"])
    result, pending = set(), [item_id]
    while pending:
        ident = pending.pop()
        if ident not in result:
            result.add(ident)
            pending.extend(children.get(ident, []))
    return result


def outline(estimate):
    totals = calculate(estimate)
    amounts = {line["id"]: line for line in totals["lines"]}
    lots = []
    for work in estimate["works"]:
        nodes = {}
        for item in work["items"]:
            line = amounts[item["id"]]
            nodes[item["id"]] = dict(deepcopy(item), own_ht_cents=line["ht_cents"], own_hours=line["hours"], children=[])
        roots = []
        for item in work["items"]:
            node = nodes[item["id"]]
            if item.get("parent_id"):
                nodes[item["parent_id"]]["children"].append(node)
            else:
                roots.append(node)
        # Iterative postorder: all generations count once, without recursion.
        pending = [(node, False) for node in roots]
        while pending:
            node, ready = pending.pop()
            if not ready:
                pending.append((node, True))
                pending.extend((child, False) for child in node["children"])
            else:
                node["subtree_hours"] = str(Decimal(node["own_hours"]) + sum((Decimal(child["subtree_hours"]) for child in node["children"]), Decimal(0)))
                node["subtree_ht_cents"] = node["own_ht_cents"] + sum(child["subtree_ht_cents"] for child in node["children"])
        lots.append({"lot_id": work["id"], "name": work["name"], "ouvrages": roots})
    return {"estimate_id": estimate["id"], "revision": estimate["revision"], "status": estimate["status"],
            "lots": lots, "totals": totals,
            "rule": "Own amounts and hours are additive. Never add subtree totals to their descendants."}


def find_reusable(store, query, limit=30):
    words = query.casefold().split()
    matches = []
    for brief in store.list_estimates():
        estimate = store.get_estimate(brief["id"])
        for work in estimate["works"]:
            candidates = [(None, work["name"], len(work["items"]), "lot_or_legacy_work")]
            candidates += [(item["id"], item["label"], len(branch_ids(work["items"], item["id"])), "ouvrage_or_post") for item in work["items"]]
            for ident, name, count, kind in candidates:
                if all(word in name.casefold() for word in words):
                    matches.append({"estimate_id": estimate["id"], "estimate_name": estimate["name"], "revision": estimate["revision"],
                                    "lot_id": work["id"], "item_id": ident, "name": name, "kind": kind, "branch_size": count})
                    if len(matches) >= limit:
                        return matches
    return matches


def _patch(item, fields):
    if fields is None:
        return
    changes = fields.model_dump(exclude_unset=True)
    if "duration_minutes" in changes:
        item["hours"] = None
    if "estimated_minutes" in changes:
        item["estimated_hours"] = None
    for field in ("label", "mode", "quantity"):
        if field in changes and changes[field] is None:
            fail(f"{field} ne peut pas être vide.")
    item.update(changes)


def build_changes(store, original, changes):
    data = deepcopy(original)
    aliases, sources = {}, []

    def resolve(ref):
        if ref is None:
            return None
        if ref.startswith("@"):
            if ref not in aliases:
                fail(f"Référence temporaire inconnue : {ref}")
            return aliases[ref]
        return ref

    def work(ref):
        ident = resolve(ref)
        found = next((w for w in data["works"] if w["id"] == ident), None)
        if found is None:
            fail("Lot introuvable.")
        return found

    def item_of(lot, ref):
        ident = resolve(ref)
        found = next((i for i in lot["items"] if i["id"] == ident), None)
        if found is None:
            fail("Poste introuvable dans ce lot.")
        return found

    def remember(key, ident):
        if key:
            if key in aliases:
                fail("Référence temporaire dupliquée.")
            aliases[key] = ident

    for change in changes:
        action = change.action
        if action in {"add_lot", "copy_lot"}:
            lot = {"id": str(uuid4()), "name": change.name.strip(), "items": []}
        else:
            lot = work(change.lot_id)
        source_items, source_work, source_estimate = None, None, None
        if change.source:
            source_estimate = store.get_estimate(change.source.estimate_id)
            if source_estimate["revision"] != change.source.revision:
                fail("La source de copie a changé. Relisez-la avant de préparer le plan.", "REVISION_CONFLICT")
            source_work = next((w for w in source_estimate["works"] if w["id"] == change.source.lot_id), None)
            if source_work is None:
                fail("Lot source introuvable.")
            source_items = source_work["items"]
            if change.source.item_id:
                if not any(i["id"] == change.source.item_id for i in source_items):
                    fail("Ouvrage source introuvable.")
                ids = branch_ids(source_items, change.source.item_id)
                source_items = [deepcopy(i) for i in source_items if i["id"] in ids]
                next(i for i in source_items if i["id"] == change.source.item_id)["parent_id"] = None
            copied = clone_items(source_items)
            for old, new in zip(source_items, copied):
                new["origin"] = {"estimate_id": source_estimate["id"], "revision": source_estimate["revision"], "lot_id": source_work["id"], "item_id": old["id"]}
                # Old document references are not evidence for a new CCTP.
                new.pop("cctp_reference", None)
                new["time_basis"] = "Temps repris de la source ; adaptation au CCTP à vérifier."
                if change.key:
                    remember(change.key + "/" + old["id"], new["id"])
            sources.append(change.source.model_dump())
        if action in {"add_lot", "copy_lot"}:
            if action == "copy_lot":
                lot["items"] = copied
            data["works"].append(lot)
            remember(change.key, lot["id"])
        elif action == "rename_lot":
            lot["name"] = change.name.strip()
        elif action in {"add_ouvrage", "add_post", "copy_ouvrage"}:
            parent = resolve(change.parent_id)
            if parent is not None:
                item_of(lot, parent)
            if action == "copy_ouvrage" and change.source.item_id:
                root = next(i for i in copied if i.get("parent_id") is None)
                root["label"] = change.name.strip()
                root["parent_id"] = parent
                lot["items"].extend(copied)
            else:
                root = new_item(change.name.strip() if change.name else change.fields.label)
                root["parent_id"] = parent
                if action in {"add_ouvrage", "copy_ouvrage"}:
                    # A grouping ouvrage has no own effort unless explicitly provided.
                    root["duration_minutes"] = 0
                lot["items"].append(root)
                if action == "copy_ouvrage":
                    for item in copied:
                        if item.get("parent_id") is None:
                            item["parent_id"] = root["id"]
                    lot["items"].extend(copied)
                    root["origin"] = {"estimate_id": source_estimate["id"], "revision": source_estimate["revision"], "lot_id": source_work["id"]}
            _patch(root, change.fields)
            remember(change.key, root["id"])
        elif action == "update_post":
            _patch(item_of(lot, change.item_id), change.fields)
        elif action == "remove_post":
            root = item_of(lot, change.item_id)
            ids = branch_ids(lot["items"], root["id"])
            lot["items"] = [i for i in lot["items"] if i["id"] not in ids]
        elif action == "remove_lot":
            data["works"].remove(lot)
        elif action == "move_post":
            data["works"] = move_item_branch(data["works"], lot["id"], resolve(change.item_id),
                work(change.target_lot_id)["id"], resolve(change.target_item_id), change.placement)
    calculate(data)
    return data, aliases, sources


def summarize_changes(before, after):
    def flattened(estimate):
        return {i["id"]: dict(i, lot_id=w["id"]) for w in estimate["works"] for i in w["items"]}
    old, new = flattened(before), flattened(after)
    return {"added_posts": [i for ident, i in new.items() if ident not in old],
            "removed_posts": [i for ident, i in old.items() if ident not in new],
            "updated_posts": [{"before": old[ident], "after": item} for ident, item in new.items() if ident in old and old[ident] != item],
            "lots_before": [{"lot_id": w["id"], "name": w["name"]} for w in before["works"]],
            "lots_after": [{"lot_id": w["id"], "name": w["name"]} for w in after["works"]]}


class AgentPlans:
    def __init__(self, store):
        self.store = store
        identity = str(store.path) if hasattr(store, "path") else json.dumps(store.config, sort_keys=True)
        scope = hashlib.sha256(identity.encode()).hexdigest()
        root = Path(store.path).parent if hasattr(store, "path") else Path.home() / ".jhr-chiffrage"
        self.folder = root / "agent-plans" / scope

    def check_local_draft(self, estimate_id):
        from .recovery import RecoveryFile
        try:
            recovery = RecoveryFile(self.store).read()
        except (OSError, ValueError):
            fail("Copie de secours locale illisible. Vérifiez-la avant de modifier cette affaire.", "LOCAL_DRAFT_UNREADABLE")
        if recovery and (recovery.get("estimate") or {}).get("id") == estimate_id:
            fail("L’application contient du travail non enregistré sur cette affaire. Enregistrez-le avant l’intervention de l’agent.", "UNSAVED_LOCAL_CHANGES")

    def prepare(self, estimate_id, expected_revision, changes, context):
        self.check_local_draft(estimate_id)
        before = self.store.get_estimate(estimate_id)
        if before["revision"] != expected_revision:
            fail("L’affaire a changé. Relisez-la avant de préparer les modifications.", "REVISION_CONFLICT")
        if before["status"] != "draft":
            fail("Créez une révision avant de modifier une version figée.", "LOCKED_VERSION")
        data, aliases, sources = build_changes(self.store, before, changes)
        plan_id = str(uuid4())
        preview = {"plan_id": plan_id, "estimate_id": estimate_id, "expected_revision": expected_revision,
                   "context": context, "created_references": aliases, "copied_sources": sources,
                   "before": calculate(before), "after": calculate(data), "changes": summarize_changes(before, data),
                   "saved": False, "notice": "Préparation uniquement. Appliquer ce plan pour enregistrer ; synchroniser ensuite en mode serveur."}
        record = {"preview": preview, "data": data}
        self.folder.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=self.folder, prefix=".plan-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.folder / (plan_id + ".json"))
        finally:
            Path(temp).unlink(missing_ok=True)
        return preview

    def read(self, plan_id):
        try:
            if str(UUID(plan_id)) != plan_id:
                raise ValueError()
            return json.loads((self.folder / (plan_id + ".json")).read_text(encoding="utf-8"))
        except (ValueError, OSError):
            fail("Plan introuvable ou invalide. Préparez un nouveau plan.")

    def apply(self, plan_id):
        record = self.read(plan_id)
        preview = record["preview"]
        self.check_local_draft(preview["estimate_id"])
        saved = self.store.save_estimate(record["data"], preview["expected_revision"], actor="mcp", operation_id=plan_id)
        return {"plan_id": plan_id, "estimate_id": saved["id"], "revision": saved["revision"], "saved": True,
                "totals": calculate(saved), "server_mode": hasattr(self.store, "synchronize"),
                "pending": getattr(self.store, "pending_count", 0), "conflict": getattr(self.store, "has_conflict", False),
                "notice": "Enregistré localement. Vérifier synchronize/get_sync_status pour le partage serveur."}
