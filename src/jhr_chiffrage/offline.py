"""Durable per-server working copy, atomic synchronization and conflict copies.

The baseline and working objects share one SQLite database. A sync batch has a
content-addressed receipt ID: an interrupted reply can safely be retried after
restart. Never synchronize the independent local-mode database.
"""
from copy import deepcopy
import hashlib
import json
import logging
import os
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .connection import RemoteStore
from .core import DomainError, Store, encode, now

LOGGER = logging.getLogger(__name__)


class OfflineStore(Store):
    def __init__(self, config, *, remote=None, cache_dir=None):
        self.config = config
        self.remote = remote if remote is not None else RemoteStore(config)
        # Credentials are never saved in the cache or exposed in its filename.
        identity = hashlib.sha256(encode([config['url'].rstrip('/'), config['token']]).encode()).hexdigest()
        root = Path(cache_dir) if cache_dir else Path.home() / '.jhr-chiffrage' / 'offline'
        root.mkdir(parents=True, exist_ok=True)
        if os.name != 'nt':
            root.chmod(0o700)
        self._sync_lock = RLock()
        self._pending_lock = RLock()
        self._pending_snapshot = None
        self.has_conflict = False
        self.online = False
        self.last_error = ''
        super().__init__(root / (identity + '.sqlite3'))
        if os.name != 'nt':
            self.path.chmod(0o600)
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS sync_baseline (kind TEXT, id TEXT, body TEXT, PRIMARY KEY(kind,id))')
            db.execute('CREATE TABLE IF NOT EXISTS sync_meta (key TEXT PRIMARY KEY, value TEXT)')
            ready = db.execute("SELECT 1 FROM sync_meta WHERE key='ready'").fetchone()
            db.commit()
        if not ready:
            # No empty/default database may masquerade as a server copy.
            try:
                self.synchronize()
            except DomainError:
                self.remote.close()
                raise
            with self.connection() as db:
                ready = db.execute("SELECT 1 FROM sync_meta WHERE key='ready'").fetchone()
            if not ready:
                self.remote.close()
                raise DomainError('OFFLINE_NOT_READY', 'Connectez ce PC au serveur une première fois pour préparer les affaires hors ligne.')

    def _changes(self, db):
        ready = db.execute("SELECT 1 FROM sync_meta WHERE key='ready'").fetchone()
        if not ready:
            return []
        return [dict(kind=k, id=i, body=json.loads(body), base=json.loads(base) if base else None)
                for k, i, body, base in db.execute(
                    'SELECT o.kind,o.id,o.body,b.body FROM objects o LEFT JOIN sync_baseline b '
                    'ON o.kind=b.kind AND o.id=b.id WHERE b.body IS NULL OR o.body != b.body ORDER BY o.kind,o.id')]

    @property
    def pending_count(self):
        with self.connection() as db:
            return len(self._changes(db))

    @property
    def status_text(self):
        count = self.pending_count
        if self.has_conflict:
            return f'Conflit de synchronisation · {count} élément(s) conservé(s) sur ce PC'
        state = 'Serveur connecté' if self.online else 'Hors ligne'
        if self.last_error and not self.online:
            state = self.last_error
        if count:
            return f'{state} · {count} élément(s) à synchroniser'
        return 'À jour · copie hors ligne disponible' if self.online else f'{state} · copie locale disponible'

    def _snapshot(self):
        snapshot = self.remote.sync_snapshot()
        if not isinstance(snapshot, dict) or snapshot.get('offline_sync_version') != 1:
            raise DomainError('SERVER_VERSION', 'Mettez à jour le serveur pour utiliser la synchronisation hors ligne.')
        objects = snapshot.get('objects')
        if not isinstance(objects, list) or not any(isinstance(x, dict) and x.get('kind') == 'settings' and x.get('id') == 'default' for x in objects):
            raise DomainError('CONNECTION_ERROR', 'Copie du serveur incomplète.')
        keys = set()
        for obj in objects:
            if not isinstance(obj, dict) or obj.get('kind') not in ('estimate', 'template', 'settings') or not isinstance(obj.get('id'), str) or not isinstance(obj.get('body'), dict):
                raise DomainError('CONNECTION_ERROR', 'Copie du serveur invalide.')
            key = (obj['kind'], obj['id'])
            if key in keys or type(obj['body'].get('revision')) is not int:
                raise DomainError('CONNECTION_ERROR', 'Copie du serveur invalide.')
            keys.add(key)
        return objects

    def _install_snapshot(self, db, objects):
        db.execute('DELETE FROM objects')
        db.execute('DELETE FROM sync_baseline')
        for obj in objects:
            self._put(db, obj['kind'], obj['id'], obj['body'])
            db.execute('INSERT INTO sync_baseline VALUES(?,?,?)', (obj['kind'], obj['id'], encode(obj['body'])))
        db.execute("INSERT OR REPLACE INTO sync_meta VALUES('ready','1')")
        db.execute("INSERT OR REPLACE INTO sync_meta VALUES('last_sync',?)", (now(),))

    def _cache_token(self, db):
        # Include working data and a durable generation: another process may
        # finish a sync while this process is waiting for the server.
        state = [list(db.execute('SELECT kind,id,body FROM objects ORDER BY kind,id')),
                 list(db.execute('SELECT kind,id,body FROM sync_baseline ORDER BY kind,id')),
                 list(db.execute("SELECT key,value FROM sync_meta WHERE key IN ('outbox','ready','generation') ORDER BY key"))]
        return hashlib.sha256(encode(state).encode()).hexdigest()

    def apply_pending_snapshot(self):
        """Apply on the UI thread only when its editor is clean and idle.

        No network lock is taken here. Persisted changes made during the fetch
        invalidate the snapshot, including edits from a separate MCP process.
        """
        with self._pending_lock:
            pending, self._pending_snapshot = self._pending_snapshot, None
        if pending is None:
            return False
        token, objects = pending
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if (self._changes(db) or
                    db.execute("SELECT 1 FROM sync_meta WHERE key='outbox'").fetchone() or
                    self._cache_token(db) != token):
                return False
            self._install_snapshot(db, objects)
            db.execute("INSERT OR REPLACE INTO sync_meta VALUES('generation',?)", (str(uuid4()),))
            db.commit()
        return True

    def synchronize(self):
        return self._synchronize(deferred=False)

    def synchronize_deferred(self):
        """Fetch in a worker; the GUI installs later without replacing edits."""
        return self._synchronize(deferred=True)

    def _synchronize(self, *, deferred):
        with self._sync_lock:
            try:
                with self._pending_lock:
                    self._pending_snapshot = None
                # Freeze the exact durable request, then release SQLite before
                # any network wait so editors and MCP can continue saving.
                with self.connection() as db:
                    db.execute('BEGIN IMMEDIATE')
                    changes = self._changes(db)
                    queued = db.execute("SELECT value FROM sync_meta WHERE key='outbox'").fetchone()
                    if changes and not queued:
                        payload = encode(changes)
                        db.execute("INSERT INTO sync_meta VALUES('outbox',?)", (payload,))
                        queued = (payload,)
                    db.commit()
                if queued:
                    batch = json.loads(queued[0])
                    operation_id = 'offline-' + hashlib.sha256(encode(batch).encode()).hexdigest()
                    self.remote.sync_push(batch, operation_id)
                    with self.connection() as db:
                        db.execute('BEGIN IMMEDIATE')
                        current = db.execute("SELECT value FROM sync_meta WHERE key='outbox'").fetchone()
                        # A second process may already have acknowledged this
                        # batch and queued another. Never erase its newer work.
                        if current and current[0] == queued[0]:
                            for change in batch:
                                db.execute('INSERT OR REPLACE INTO sync_baseline VALUES(?,?,?)',
                                           (change['kind'], change['id'], encode(change['body'])))
                            db.execute("DELETE FROM sync_meta WHERE key='outbox'")
                            db.execute("INSERT OR REPLACE INTO sync_meta VALUES('generation',?)", (str(uuid4()),))
                        db.commit()
                with self.connection() as db:
                    db.execute('BEGIN')
                    clean = not self._changes(db) and not db.execute("SELECT 1 FROM sync_meta WHERE key='outbox'").fetchone()
                    token = self._cache_token(db) if clean else None
                if clean:
                    objects = self._snapshot()
                    with self._pending_lock:
                        self._pending_snapshot = (token, objects)
                    if not deferred:
                        self.apply_pending_snapshot()
                self.online = True
                self.has_conflict = False
                self.last_error = ''
                LOGGER.info('Offline synchronization completed')
                return True
            except DomainError as exc:
                self.online = False
                self.has_conflict = exc.code == 'SYNC_CONFLICT'
                LOGGER.warning('Offline synchronization stopped (%s)', exc.code)
                self.last_error = {'UNAUTHORIZED': 'Authentification refusée',
                                   'UNKNOWN_METHOD': 'Serveur à mettre à jour',
                                   'SERVER_VERSION': 'Serveur à mettre à jour'}.get(exc.code, 'Hors ligne')
                if exc.code == 'CONNECTION_ERROR':
                    return False
                raise

    def resolve_conflict_keep_both(self):
        """Explicit user action: keep server objects plus copies, never overwrite."""
        with self._sync_lock:
            with self.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                backup = self.backup()
                changes = self._changes(db)
                objects = self._snapshot()
                self._install_snapshot(db, objects)
                db.execute("DELETE FROM sync_meta WHERE key='outbox'")
                for change in changes:
                    if change['kind'] == 'settings':
                        continue  # Preserved in full backup and estimate settings snapshots.
                    obj = deepcopy(change['body'])
                    obj.update(id=str(uuid4()), revision=1, name=obj['name'] + ' (copie hors ligne)')
                    if change['kind'] == 'estimate':
                        obj.update(status='draft', version=1, parent_id=None, updated_at=now())
                        for key in ('root_id', 'frozen_at', 'frozen_totals', 'calculation_version'):
                            obj.pop(key, None)
                    self._put(db, change['kind'], obj['id'], obj)
                db.execute('INSERT INTO events(at,actor,operation,before_json,after_json) VALUES(?,?,?,?,?)',
                           (now(), 'ui', 'offline_conflict_copies', 'null', encode({'backup': str(backup)})))
                db.commit()
            self.has_conflict = False
            self.online = True
            return str(backup)

    def close(self):
        self.remote.close()
