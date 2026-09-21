"""Offline UI lifecycle and preservation of unsaved edits."""
import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from jhr_chiffrage.core import DomainError, Store
from jhr_chiffrage.ui import MainWindow


class OfflineStub(Store):
    status_text = "Hors ligne — modifications conservées sur ce PC"
    has_conflict = False
    pending_count = 1

    def __init__(self, path):
        super().__init__(path)
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.calls = 0
        self.resolved = False

    def synchronize(self):
        self.calls += 1
        self.started.set()
        self.release.wait(3)
        if self.has_conflict:
            raise DomainError("SYNC_CONFLICT", "Versions différentes")
        self.status_text = "À jour"
        return True

    def resolve_conflict_keep_both(self):
        self.resolved = True
        self.has_conflict = False
        return "sauvegarde.sqlite3"


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = OfflineStub(tmp_path / "offline-ui.sqlite")
    widget = MainWindow(store)
    widget.timer.stop()
    widget.sync_timer.stop()
    yield widget
    store.release.set()
    for _ in range(100):
        app.processEvents()
        if widget.sync_worker is None:
            break
        QTest.qWait(10)
    widget.dirty = widget.settings_dirty = False
    widget.close()


def finish(window):
    for _ in range(100):
        QApplication.processEvents()
        if window.sync_worker is None:
            return
        QTest.qWait(10)
    pytest.fail("Synchronization did not finish")


def test_sync_is_background_and_does_not_overlap(window):
    window.store.release.clear()
    window.start_sync()
    assert window.store.started.wait(1)
    tick = []
    QTimer.singleShot(0, lambda: tick.append(True))
    QApplication.processEvents()
    assert tick
    assert not window.sync_button.isEnabled()
    assert window.tabs.isEnabled()
    window.start_sync()
    assert window.store.calls == 1
    window.store.release.set()
    finish(window)
    assert window.sync_status.text() == "À jour"
    assert window.tabs.isEnabled()


def test_sync_preserves_unsaved_fields(window):
    window.current = window.store.create_estimate("Affaire")
    window.render()
    window.metadata["client"].setText("Saisie en cours")
    window.dirty = True
    window.setting_fields["company"].setText("Entreprise en cours")
    window.settings_dirty = True
    window.start_sync()
    finish(window)
    assert window.store.calls == 0
    assert window.metadata["client"].text() == "Saisie en cours"
    assert window.setting_fields["company"].text() == "Entreprise en cours"
    assert window.dirty and window.settings_dirty


def test_editing_during_network_keeps_fields_and_defers_snapshot(window):
    window.current = window.store.create_estimate("Affaire")
    window.render()
    window.store.release.clear()
    applied = []
    window.store.apply_pending_snapshot = lambda: applied.append(True)
    window.start_sync()
    assert window.store.started.wait(1)
    assert window.metadata['client'].isEnabled()
    window.metadata['client'].setText('Saisie pendant le réseau')
    window.metadata['client'].setCursorPosition(5)
    window.mark_dirty()
    window.store.release.set()
    finish(window)
    assert window.dirty
    assert window.metadata['client'].text() == 'Saisie pendant le réseau'
    assert window.metadata['client'].cursorPosition() == 5
    assert not applied
    assert window.save_current()
    window.poll()
    assert applied


def test_sync_refreshes_changed_object_with_same_revision(window):
    window.current = window.store.create_estimate("Ancien titre")
    changed = dict(window.current, name="Titre du serveur")
    original_get = window.store.get_estimate
    window.store.get_estimate = lambda ident: changed if ident == changed["id"] else original_get(ident)
    window.start_sync()
    finish(window)
    assert window.current["name"] == "Titre du serveur"


def test_conflict_action_is_visible_and_resolves(window, monkeypatch):
    window.store.has_conflict = True
    window.store.status_text = "Conflit — vos modifications sont conservées"
    window.start_sync()
    finish(window)
    assert not window.resolve_button.isHidden()
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    window.resolve_sync_conflict()
    finish(window)
    assert window.store.resolved
    assert window.resolve_button.isHidden()


def test_close_waits_without_destroying_running_worker(window):
    window.show()
    window.store.release.clear()
    window.start_sync()
    assert window.store.started.wait(1)
    assert not window.close()
    assert window.close_after_sync
    assert window.isVisible()
    window.store.release.set()
    finish(window)
    assert not window.isVisible()
