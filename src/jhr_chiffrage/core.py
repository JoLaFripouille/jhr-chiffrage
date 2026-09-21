"""Shared application services. No dependency on Qt or the MCP transport."""
from __future__ import annotations

from contextlib import contextmanager, closing
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from uuid import UUID, uuid4

from platformdirs import user_data_path


class DomainError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def fail(message, code="VALIDATION_ERROR"):
    raise DomainError(code, message)


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def number(value, label, *, optional=False, positive=False, maximum="1000000000"):
    if value is None or value == "":
        if optional:
            return None
        fail(f"{label} : valeur manquante.")
    try:
        if isinstance(value, bool):
            raise ValueError()
        text = str(value).strip().replace(",", ".")
        if len(text) > 32:
            raise ValueError()
        result = Decimal(text)
        if not result.is_finite() or result < 0 or result > Decimal(maximum):
            raise ValueError()
        if positive and result == 0:
            raise ValueError()
        if result.as_tuple().exponent < -6:
            raise ValueError()
        return result
    except (InvalidOperation, ValueError):
        fail(f"{label} : nombre {'strictement positif' if positive else 'positif ou nul'} requis (6 décimales maximum).")


def cents(value):
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


MAX_DURATION_MINUTES = 60_000_000_000


def item_duration(item, minutes_key, hours_key, label):
    """Keep legacy decimal hours intact; new durations are stored as integer minutes."""
    minutes = item.get(minutes_key)
    if minutes is None:
        return number(item.get(hours_key), label, optional=True)
    if type(minutes) is not int or not 0 <= minutes <= MAX_DURATION_MINUTES:
        fail(f"{label} : minutes entières requises entre 0 et {MAX_DURATION_MINUTES}.")
    if item.get(hours_key) not in (None, ""):
        fail(f"{label} : durée ambiguë, renseigner les minutes ou les heures, pas les deux.")
    return Decimal(minutes) / Decimal(60)


def checked_settings(data, allow_missing=False):
    result = {"company": str(data.get("company", "JHR")).strip(),
              "vat_enabled": data.get("vat_enabled", True)}
    if not isinstance(result["vat_enabled"], bool):
        fail("Le mode TVA doit être un booléen.")
    for key, label, maximum, positive in [
        ("hourly_rate", "Taux horaire", "1000000", True),
        ("vat_rate", "TVA", "100", False),
        ("levy_rate", "Prélèvements", "100", False),
    ]:
        raw_value = data.get(key)
        if key == "vat_rate" and not result["vat_enabled"] and raw_value in (None, ""):
            raw_value = "0"
        value = number(raw_value, label, optional=allow_missing, positive=positive, maximum=maximum)
        result[key] = "" if value is None else str(value)
    return result


def validate_item_hierarchy(items):
    """Parents belong to this work; input order does not define the hierarchy."""
    parents = {}
    for item in items:
        if not isinstance(item, dict):
            fail("Poste invalide.")
        ident = item.get("id")
        if not isinstance(ident, str) or not ident or ident in parents:
            fail("Identifiant de poste absent ou dupliqué.")
        parent = item.get("parent_id")
        if parent is not None and (not isinstance(parent, str) or not parent):
            fail("Identifiant du poste parent invalide.")
        parents[ident] = parent
    for ident, parent in parents.items():
        if parent is not None and parent not in parents:
            fail("Le poste parent doit appartenir au même ouvrage.")
        if parent == ident:
            fail("Un poste ne peut pas être son propre parent.")
    checked = set()
    for ident in parents:
        path = set()
        while ident is not None and ident not in checked:
            if ident in path:
                fail("La hiérarchie des sous-postes contient un cycle.")
            path.add(ident)
            ident = parents[ident]
        checked.update(path)


def clone_items(items):
    """Copy a complete item forest with fresh identities and remapped parents."""
    validate_item_hierarchy(items)
    copied = deepcopy(items)
    identifiers = {item["id"]: str(uuid4()) for item in copied}
    for item in copied:
        item["id"] = identifiers[item["id"]]
        if item.get("parent_id") is not None:
            item["parent_id"] = identifiers[item["parent_id"]]
    return copied


def calculate(estimate):
    settings = checked_settings(estimate.get("settings", {}), allow_missing=True)
    incomplete, lines, work_totals = [], [], []
    total = 0
    hours_total = Decimal(0)
    works = estimate.get("works", [])
    if not isinstance(works, list) or len(works) > 1000:
        fail("Liste d'ouvrages invalide (maximum 1000).")
    if not works:
        incomplete.append("Ajouter au moins un ouvrage et un poste.")
    ids = set()
    for work in works:
        if not isinstance(work, dict):
            fail("Ouvrage invalide.")
        wid = work.get("id")
        if not isinstance(wid, str) or not wid or wid in ids:
            fail("Identifiant d'ouvrage absent ou dupliqué.")
        ids.add(wid)
        label = str(work.get("name", "")).strip()
        if not label:
            incomplete.append("Nom d'ouvrage manquant.")
        items = work.get("items", [])
        if not isinstance(items, list) or len(items) > 2000:
            fail("Liste de postes invalide (maximum 2000 par ouvrage).")
        validate_item_hierarchy(items)
        if not items:
            incomplete.append(f"{label} : aucun poste.")
        work_ht, work_hours = 0, Decimal(0)
        for item in items:
            if not isinstance(item, dict):
                fail("Poste invalide.")
            iid = item.get("id")
            if not isinstance(iid, str) or not iid or iid in ids:
                fail("Identifiant de poste absent ou dupliqué.")
            ids.add(iid)
            name = str(item.get("label", "")).strip()
            if not name:
                incomplete.append(f"{label} : désignation de poste manquante.")
            qty = number(item.get("quantity", "1"), f"{name} — quantité", positive=True)
            mode = item.get("mode", "hourly")
            # Validate supplied minute fields even when their mode is inactive.
            for minutes_key, hours_key in (("duration_minutes", "hours"), ("estimated_minutes", "estimated_hours")):
                if item.get(minutes_key) is not None:
                    item_duration(item, minutes_key, hours_key, f"{name} — durée")
            amount, duration = None, Decimal(0)
            if mode == "hourly":
                unit_hours = item_duration(item, "duration_minutes", "hours", f"{name} — durée unitaire")
                raw_rate = item.get("rate")
                rate = number(settings["hourly_rate"] if raw_rate in (None, "") else raw_rate,
                              f"{name} — taux horaire", optional=True, positive=True, maximum="1000000")
                if unit_hours is None:
                    incomplete.append(f"{name} : temps à renseigner.")
                else:
                    # Divide after quantity so 3 x 1 minute stays exactly 0.05 h.
                    duration = (qty * Decimal(item["duration_minutes"]) / 60
                                if item.get("duration_minutes") is not None else qty * unit_hours)
                if rate is None:
                    incomplete.append(f"{name} : taux horaire à renseigner.")
                if unit_hours is not None and rate is not None:
                    amount = (qty * Decimal(item["duration_minutes"]) * rate / 60
                              if item.get("duration_minutes") is not None else qty * unit_hours * rate)
            elif mode == "fixed":
                price = number(item.get("price"), f"{name} — prix forfaitaire", optional=True)
                duration = item_duration(item, "estimated_minutes", "estimated_hours", f"{name} — charge totale") or Decimal(0)
                if price is None:
                    incomplete.append(f"{name} : prix forfaitaire à renseigner.")
                else:
                    amount = qty * price
            else:
                fail(f"{name} : mode de calcul inconnu.")
            value = cents(amount) if amount is not None else 0
            if value > 10**15:
                fail("Montant de poste trop élevé.")
            lines.append({"id": iid, "ht_cents": value, "hours": str(duration)})
            work_ht += value
            work_hours += duration
        work_totals.append({"id": wid, "ht_cents": work_ht, "hours": str(work_hours)})
        total += work_ht
        hours_total += work_hours
    vat_rate = number(settings["vat_rate"], "TVA", optional=True, maximum="100")
    levy_rate = number(settings["levy_rate"], "Prélèvements", optional=True, maximum="100")
    if settings["vat_enabled"] and vat_rate is None:
        incomplete.append("Taux de TVA à renseigner.")
    if levy_rate is None:
        incomplete.append("Taux de prélèvements à renseigner (0 si aucun).")
    base = Decimal(total) / 100
    vat = cents(base * (vat_rate or Decimal(0)) / 100) if settings["vat_enabled"] else 0
    levy = cents(base * (levy_rate or Decimal(0)) / 100)
    return {"ht_cents": total, "vat_cents": vat, "ttc_cents": total + vat,
            "levy_cents": levy, "balance_cents": total - levy, "hours": str(hours_total),
            "incomplete": incomplete, "lines": lines, "works": work_totals}


DEFAULT_SETTINGS = {"revision": 1, "company": "JHR", "hourly_rate": "",
                    "vat_enabled": True, "vat_rate": "", "levy_rate": ""}


def new_item(label):
    return {"id": str(uuid4()), "parent_id": None, "label": label, "mode": "hourly", "quantity": "1",
            "hours": None, "duration_minutes": None, "rate": None, "price": None,
            "estimated_hours": None, "estimated_minutes": None}


class Store:
    def __init__(self, path=None):
        selected = path or os.environ.get("JHR_CHIFFRAGE_DB")
        # A packaged agent host may virtualize LocalAppData on Windows. A home
        # directory keeps the desktop launcher and MCP on the same physical DB.
        default_dir = Path.home() / ".jhr-chiffrage" if os.name == "nt" else user_data_path("JHRChiffrage", appauthor=False)
        self.path = Path(selected) if selected else default_dir / "chiffrage.sqlite3"
        self.path = self.path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                fail("Version de base non prise en charge.", "MAINTENANCE_REQUIRED")
            db.execute("CREATE TABLE IF NOT EXISTS objects (kind TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY(kind,id))")
            db.execute("CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL, operation TEXT NOT NULL, before_json TEXT, after_json TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS receipts (actor TEXT NOT NULL, operation_id TEXT NOT NULL, fingerprint TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(actor,operation_id))")
            if db.execute("SELECT 1 FROM objects WHERE kind='settings'").fetchone() is None:
                self._put(db, "settings", "default", DEFAULT_SETTINGS)
                for name, labels in [
                    ("Plan EXE AutoCAD", ["Analyse et préparation", "Plans EXE 2D", "Cotation et détails", "Contrôle et diffusion"]),
                    ("Plan FAB Advance Steel", ["Préparation et modélisation", "Plans d'ensemble", "Plans de fabrication", "Nomenclatures et contrôle"]),
                ]:
                    template = {"id": str(uuid4()), "revision": 1, "name": name, "description": "Temps à adapter à l'ouvrage.", "items": [new_item(x) for x in labels]}
                    self._put(db, "template", template["id"], template)
            db.execute("PRAGMA user_version=1")
            db.commit()

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            yield db
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise DomainError("DATABASE_BUSY", "Base occupée. Réessayez après quelques instants.") from exc
            raise
        finally:
            db.close()

    def _get(self, db, kind, ident):
        row = db.execute("SELECT body FROM objects WHERE kind=? AND id=?", (kind, ident)).fetchone()
        if row is None:
            fail("Élément introuvable.", "NOT_FOUND")
        return json.loads(row[0])

    def _put(self, db, kind, ident, data):
        db.execute("INSERT INTO objects VALUES(?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET revision=excluded.revision,body=excluded.body",
                   (kind, ident, data["revision"], encode(data)))

    def _check_revision(self, obj, expected):
        if isinstance(expected, bool) or expected != obj["revision"]:
            fail(f"L'élément a changé (révision actuelle {obj['revision']}). Rechargez avant de modifier.", "REVISION_CONFLICT")

    def _mutation(self, operation, payload, actor, operation_id, callback):
        fingerprint = hashlib.sha256(encode([operation, payload]).encode()).hexdigest()
        if operation_id is not None and (not isinstance(operation_id, str) or not operation_id.strip() or len(operation_id) > 200):
            fail("Clé d'opération invalide.")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if operation_id:
                row = db.execute("SELECT fingerprint,result FROM receipts WHERE actor=? AND operation_id=?", (actor, operation_id)).fetchone()
                if row:
                    if row[0] != fingerprint:
                        fail("Clé d'opération déjà utilisée avec un autre contenu.", "IDEMPOTENCY_CONFLICT")
                    return json.loads(row[1])
            before, result = callback(db)
            db.execute("INSERT INTO events(at,actor,operation,before_json,after_json) VALUES(?,?,?,?,?)", (now(), actor, operation, encode(before), encode(result)))
            if operation_id:
                db.execute("INSERT INTO receipts VALUES(?,?,?,?)", (actor, operation_id, fingerprint, encode(result)))
            db.commit()
            return result

    def get_settings(self):
        with self.connection() as db:
            return self._get(db, "settings", "default")

    def sync_snapshot(self):
        """One consistent read, including all object types needed while offline."""
        with self.connection() as db:
            db.execute("BEGIN")
            objects = [{"kind": row[0], "id": row[1], "body": json.loads(row[2])}
                       for row in db.execute("SELECT kind,id,body FROM objects ORDER BY kind,id")]
        return {"offline_sync_version": 1, "objects": objects}

    def sync_push(self, changes, actor="ui", operation_id=None):
        """Compare-and-swap an offline batch; never partially merge conflicting data."""
        changes = deepcopy(changes)
        if not isinstance(changes, list) or len(changes) > 10000:
            fail("Lot de synchronisation invalide.")

        def push(db):
            current = {(r[0], r[1]): json.loads(r[2]) for r in db.execute("SELECT kind,id,body FROM objects")}
            pending = {}
            for change in changes:
                if not isinstance(change, dict) or set(change) != {"kind", "id", "base", "body"}:
                    fail("Objet de synchronisation invalide.")
                kind, ident, base, body = (change[k] for k in ("kind", "id", "base", "body"))
                if kind not in ("settings", "template", "estimate") or not isinstance(ident, str):
                    fail("Type ou identifiant de synchronisation invalide.")
                key = (kind, ident)
                if key in pending or not isinstance(body, dict) or (base is not None and not isinstance(base, dict)):
                    fail("Objet dupliqué ou contenu de synchronisation invalide.")
                if encode(current.get(key)) != encode(base):
                    fail("Des données ont changé sur le serveur. Vos modifications restent conservées sur ce PC.", "SYNC_CONFLICT")
                if kind == "settings":
                    if ident != "default" or base is None:
                        fail("Identifiant des paramètres invalide.")
                else:
                    try:
                        if str(UUID(ident)) != ident:
                            raise ValueError()
                    except ValueError:
                        fail("Identifiant de synchronisation invalide.")
                    if body.get("id") != ident:
                        fail("Identifiant du contenu incohérent.")
                revision = body.get("revision")
                if type(revision) is not int or not 1 <= revision <= 2147483647 or (base and revision <= base["revision"]):
                    fail("Révision de synchronisation invalide.")
                pending[key] = body

            combined = {**current, **pending}
            for (kind, ident), body in pending.items():
                base = current.get((kind, ident))
                try:
                    self._validate_sync_body(kind, body, base, combined)
                except (TypeError, KeyError, AttributeError, ValueError, RecursionError):
                    fail("Contenu de synchronisation invalide.")
            for (kind, ident), body in pending.items():
                self._put(db, kind, ident, body)
            return changes, {"applied": len(pending)}

        return self._mutation("sync_push", changes, actor, operation_id, push)

    def _validate_sync_body(self, kind, body, base, combined):
        if kind == "settings":
            if set(body) != set(DEFAULT_SETTINGS):
                fail("Champs des paramètres invalides.")
            checked_settings(body)
            return
        if kind == "template":
            if set(body) != {"id", "revision", "name", "description", "items"}:
                fail("Champs du gabarit invalides.")
            if not isinstance(body["name"], str) or not body["name"].strip() or not isinstance(body["description"], str):
                fail("Nom ou description du gabarit invalide.")
            calculate({"settings": DEFAULT_SETTINGS, "works": [{"id": "template-validation", "name": body["name"], "items": body["items"]}]})
            return
        required = {"id", "revision", "version", "parent_id", "status", "name", "client", "reference", "settings", "works", "commercial_title", "commercial_description", "updated_at"}
        optional = {"root_id", "frozen_at", "frozen_totals", "calculation_version"}
        if not required <= set(body) or set(body) - required - optional:
            fail("Champs de l'affaire invalides.")
        if base and base["status"] == "frozen":
            fail("Cette version est figée. Créez une révision.", "LOCKED_VERSION")
        for field in ("name", "client", "reference", "commercial_title", "commercial_description", "updated_at"):
            if not isinstance(body[field], str):
                fail("Texte de l'affaire invalide.")
        if not body["name"].strip() or body["status"] not in ("draft", "frozen"):
            fail("Nom ou état de l'affaire invalide.")
        datetime.fromisoformat(body["updated_at"])
        if type(body["version"]) is not int or body["version"] < 1:
            fail("Version de l'affaire invalide.")
        if base and any(body.get(k) != base.get(k) for k in ("version", "parent_id", "root_id")):
            fail("La filiation d'une affaire ne peut pas être modifiée.")
        if body["parent_id"] is None:
            if body["version"] != 1 or "root_id" in body:
                fail("Filiation d'affaire invalide.")
        else:
            parent = combined.get(("estimate", body["parent_id"]))
            if not parent or parent["status"] != "frozen" or body.get("root_id") != parent.get("root_id", parent["id"]) or body["version"] <= parent["version"]:
                fail("La révision doit provenir d'une version figée.")
            for (other_kind, other_id), other in combined.items():
                if other_kind == "estimate" and other_id != body["id"] and other.get("root_id", other_id) == body["root_id"] and other["version"] == body["version"]:
                    fail("Une autre révision a été créée sur le serveur. Vos modifications restent conservées sur ce PC.", "SYNC_CONFLICT")
        checked_settings(body["settings"], allow_missing=True)
        totals = calculate(body)
        if body["status"] == "frozen":
            if totals["incomplete"] or body.get("frozen_totals") != totals or body.get("calculation_version") != 1 or body["revision"] < 2:
                fail("Le chiffrage figé est incomplet ou ses totaux sont incohérents.")
            datetime.fromisoformat(body["frozen_at"])
        elif any(k in body for k in ("frozen_at", "frozen_totals", "calculation_version")):
            fail("Un brouillon ne peut pas contenir des totaux figés.")

    def save_settings(self, data, expected_revision, actor="ui", operation_id=None):
        data = deepcopy(data)
        def save(db):
            old = self._get(db, "settings", "default")
            self._check_revision(old, expected_revision)
            new = checked_settings(data)
            new["revision"] = old["revision"] + 1
            self._put(db, "settings", "default", new)
            return old, new
        return self._mutation("save_settings", [data, expected_revision], actor, operation_id, save)

    def list_estimates(self):
        with self.connection() as db:
            objects = [json.loads(row[0]) for row in db.execute("SELECT body FROM objects WHERE kind='estimate'")]
        return sorted([{k: x.get(k) for k in ("id", "revision", "version", "reference", "name", "client", "status", "updated_at")} for x in objects], key=lambda x: x["updated_at"], reverse=True)

    def get_estimate(self, id):
        with self.connection() as db:
            return self._get(db, "estimate", id)

    def create_estimate(self, name, client="", reference="", actor="ui", operation_id=None):
        if not isinstance(name, str) or not name.strip():
            fail("Le nom de l'affaire est obligatoire.")
        def create(db):
            obj = {"id": str(uuid4()), "revision": 1, "version": 1, "parent_id": None, "status": "draft",
                   "name": name.strip(), "client": client, "reference": reference, "settings": self._get(db, "settings", "default"),
                   "works": [], "commercial_title": "", "commercial_description": "", "updated_at": now()}
            self._put(db, "estimate", obj["id"], obj)
            return None, obj
        return self._mutation("create_estimate", [name, client, reference], actor, operation_id, create)

    def save_estimate(self, data, expected_revision, actor="ui", operation_id=None):
        data = deepcopy(data)
        def save(db):
            old = self._get(db, "estimate", data.get("id"))
            self._check_revision(old, expected_revision)
            if old["status"] != "draft":
                fail("Cette version est figée. Créez une révision.", "LOCKED_VERSION")
            if "settings" in data and data["settings"] != old["settings"]:
                fail("Les paramètres de l'affaire se changent avec l'action explicite Appliquer les paramètres actuels.")
            new = deepcopy(old)
            for field in ("name", "client", "reference", "works", "commercial_title", "commercial_description"):
                if field in data:
                    new[field] = data[field]
            if not isinstance(new["name"], str) or not new["name"].strip():
                fail("Le nom de l'affaire est obligatoire.")
            calculate(new)
            new.update(revision=old["revision"] + 1, updated_at=now())
            self._put(db, "estimate", old["id"], new)
            return old, new
        return self._mutation("save_estimate", [data, expected_revision], actor, operation_id, save)

    def freeze_estimate(self, id, expected_revision, actor="ui", operation_id=None):
        def freeze(db):
            old = self._get(db, "estimate", id)
            self._check_revision(old, expected_revision)
            if old["status"] != "draft":
                fail("Version déjà figée.", "LOCKED_VERSION")
            totals = calculate(old)
            if totals["incomplete"]:
                fail("Chiffrage incomplet : " + "; ".join(totals["incomplete"]))
            new = deepcopy(old)
            new.update(status="frozen", revision=old["revision"] + 1, frozen_at=now(), updated_at=now(), frozen_totals=totals, calculation_version=1)
            self._put(db, "estimate", id, new)
            return old, new
        return self._mutation("freeze_estimate", [id, expected_revision], actor, operation_id, freeze)

    def revise_estimate(self, id, actor="ui", operation_id=None, expected_revision=None):
        def revise(db):
            old = self._get(db, "estimate", id)
            if expected_revision is not None:
                self._check_revision(old, expected_revision)
            if old["status"] != "frozen":
                fail("Figez cette version avant de créer une révision.")
            new = deepcopy(old)
            root = old.get("root_id", old["id"])
            siblings = [json.loads(r[0]) for r in db.execute("SELECT body FROM objects WHERE kind='estimate'")]
            version = max(x["version"] for x in siblings if x.get("root_id", x["id"]) == root) + 1
            new.update(id=str(uuid4()), revision=1, version=version, parent_id=id, root_id=root, status="draft", updated_at=now())
            for key in ("frozen_at", "frozen_totals", "calculation_version"):
                new.pop(key, None)
            for work in new["works"]:
                work["id"] = str(uuid4())
                work["items"] = clone_items(work["items"])
            self._put(db, "estimate", new["id"], new)
            return old, new
        return self._mutation("revise_estimate", [id, expected_revision], actor, operation_id, revise)

    def refresh_estimate_settings(self, id, expected_revision, actor="ui", operation_id=None):
        def refresh(db):
            old = self._get(db, "estimate", id)
            self._check_revision(old, expected_revision)
            if old["status"] != "draft":
                fail("Cette version est figée.", "LOCKED_VERSION")
            settings = self._get(db, "settings", "default")
            checked_settings(settings)
            new = deepcopy(old)
            new.update(settings=settings, revision=old["revision"] + 1, updated_at=now())
            calculate(new)
            self._put(db, "estimate", id, new)
            return old, new
        return self._mutation("refresh_estimate_settings", [id, expected_revision], actor, operation_id, refresh)

    def list_templates(self):
        with self.connection() as db:
            return sorted([json.loads(x[0]) for x in db.execute("SELECT body FROM objects WHERE kind='template'")], key=lambda x: x["name"])

    def save_template(self, data, expected_revision=None, actor="ui", operation_id=None):
        data = deepcopy(data)
        def save(db):
            old = self._get(db, "template", data["id"]) if data.get("id") else None
            if old:
                self._check_revision(old, expected_revision)
            new = {"id": old["id"] if old else str(uuid4()), "revision": old["revision"] + 1 if old else 1,
                   "name": str(data.get("name", "")).strip(), "description": str(data.get("description", "")), "items": data.get("items", [])}
            if not new["name"]:
                fail("Le nom du gabarit est obligatoire.")
            calculate({"settings": DEFAULT_SETTINGS, "works": [{"id": "template-validation", "name": new["name"], "items": new["items"]}]})
            self._put(db, "template", new["id"], new)
            return old, new
        return self._mutation("save_template", [data, expected_revision], actor, operation_id, save)

    def apply_template(self, estimate_id, template_id, work_id, expected_revision, actor="ui", operation_id=None):
        def apply(db):
            old = self._get(db, "estimate", estimate_id)
            self._check_revision(old, expected_revision)
            if old["status"] != "draft":
                fail("Cette version est figée.", "LOCKED_VERSION")
            template = self._get(db, "template", template_id)
            new = deepcopy(old)
            work = next((x for x in new["works"] if x["id"] == work_id), None)
            if work is None:
                fail("Ouvrage introuvable.", "NOT_FOUND")
            for copy in clone_items(template["items"]):
                copy.update(template_id=template_id, template_revision=template["revision"])
                work["items"].append(copy)
            calculate(new)
            new.update(revision=old["revision"] + 1, updated_at=now())
            self._put(db, "estimate", estimate_id, new)
            return old, new
        return self._mutation("apply_template", [estimate_id, template_id, work_id, expected_revision], actor, operation_id, apply)

    def list_changes(self, limit=50):
        with self.connection() as db:
            return [{"seq": r[0], "at": r[1], "actor": r[2], "operation": r[3], "before": json.loads(r[4]), "after": json.loads(r[5])}
                    for r in db.execute("SELECT seq,at,actor,operation,before_json,after_json FROM events ORDER BY seq DESC LIMIT ?", (max(1, min(int(limit), 200)),))]

    def backup(self, destination=None):
        if destination:
            target = Path(destination).resolve()
        else:
            target = self.path.parent / "backups" / f"chiffrage-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}.sqlite3"
        if target == self.path or target.exists():
            fail("La destination existe déjà. Choisissez un autre nom.")
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as source:
            with closing(sqlite3.connect(target)) as backup_db:
                source.backup(backup_db)
                if backup_db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    fail("La vérification de sauvegarde a échoué.")
        return str(target)

    def export_estimate(self, id, kind="commercial"):
        if kind not in ("commercial", "internal"):
            fail("Type d'export inconnu.")
        obj = self.get_estimate(id)
        totals = obj.get("frozen_totals") or calculate(obj)
        if totals["incomplete"]:
            fail("Complétez le chiffrage avant de l'exporter.")
        if kind == "internal":
            payload = {"estimate": obj, "totals": totals, "notice": "Prélèvements estimés sur le HT prévisionnel."}
        else:
            payload = {k: obj[k] for k in ("name", "reference", "client", "version", "commercial_title", "commercial_description")}
            payload.update(currency="EUR", status=obj["status"], company=obj["settings"]["company"],
                           vat_enabled=obj["settings"]["vat_enabled"], vat_rate=obj["settings"]["vat_rate"],
                           totals={k: totals[k] for k in ("ht_cents", "vat_cents", "ttc_cents")},
                           works=[{"name": w["name"], "ht_cents": next(x["ht_cents"] for x in totals["works"] if x["id"] == w["id"])} for w in obj["works"]])
        folder = self.path.parent / "exports"
        folder.mkdir(exist_ok=True)
        target = folder / f"chiffrage-{uuid4().hex}-{kind}.json"
        with target.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, ensure_ascii=False))
        return str(target)
