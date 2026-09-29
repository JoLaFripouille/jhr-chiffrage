"""Reusable labels suggest text only, without modifying an item's pricing."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from jhr_chiffrage.core import Store
from jhr_chiffrage.ui import ItemDialog, DesignationsDialog, MainWindow, TemplateDialog


@pytest.fixture
def store(tmp_path):
    app = QApplication.instance() or QApplication([])
    # Keep the application wrapper alive through the test.
    store = Store(tmp_path / 'labels.sqlite3')
    store._test_app = app
    return store


def test_remember_and_suggest_after_reopening(store):
    dialog = ItemDialog(store=store)
    dialog.label.setText('cotation + label')
    assert dialog.remember_button.isEnabled()
    dialog.remember_designation()
    assert not dialog.remember_button.isEnabled()
    dialog.reject()  # The explicit memorize action is independent of the item.
    reopened = ItemDialog({'quantity': '2', 'duration_minutes': 45, 'rate': '80'}, store=Store(store.path))
    reopened.show()
    reopened.activateWindow()
    reopened.label.setFocus()
    QApplication.processEvents()
    QTest.keyClicks(reopened.label, 'COT')
    QApplication.processEvents()
    completer = reopened.label.completer()
    assert completer.completionCount() == 1
    assert completer.currentCompletion() == 'cotation + label'
    popup = completer.popup()
    popup.setCurrentIndex(completer.completionModel().index(0, 0))
    QTest.keyClick(popup, Qt.Key_Return)
    QApplication.processEvents()
    assert reopened.label.text() == 'cotation + label'
    assert reopened.value()['duration_minutes'] == 45
    assert reopened.value()['quantity'] == '2'
    assert reopened.value()['rate'] == '80'
    reopened.close()


def test_library_removal_leaves_estimates_unchanged(store):
    estimate = store.create_estimate('Intacte')
    dialog = DesignationsDialog(store)
    dialog.name.setText('cotation + label')
    dialog.add_name()
    assert dialog.names.count() == 1
    dialog.names.setCurrentRow(0)
    dialog.remove_name()
    assert dialog.names.count() == 0
    assert ItemDialog(store=store).label.completer().model().rowCount() == 0
    assert store.get_estimate(estimate['id']) == estimate
    dialog.close()


def test_templates_and_subposts_inherit_the_same_library(store):
    store.save_designation({'name': 'cotation + label'})
    main = MainWindow(store)
    main.timer.stop()
    template = TemplateDialog(parent=main)
    nested = ItemDialog(parent=template)
    subpost = ItemDialog({'parent_id': 'parent'}, parent=main)
    assert nested.saved_names == ['cotation + label']
    assert subpost.saved_names == nested.saved_names
    assert subpost.value()['parent_id'] == 'parent'
    nested.close()
    subpost.close()
    template.close()
    main.close()
