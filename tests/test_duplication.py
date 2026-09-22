"""Duplication keeps independent identities and nested parent relationships."""
import copy
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from jhr_chiffrage.core import Store, calculate, new_item
from jhr_chiffrage.ui import MainWindow, uid


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'duplication.sqlite3')
    settings = store.get_settings()
    settings.update(hourly_rate='60', levy_rate='20', vat_rate='0', vat_enabled=False)
    store.save_settings(settings, settings['revision'])
    estimate = store.create_estimate('Duplication')
    root, child, grandchild = [new_item(name) for name in ('Plan', 'Coupe', 'Détail')]
    for item in (root, child, grandchild):
        item['duration_minutes'] = 30
    child['parent_id'] = root['id']
    grandchild['parent_id'] = child['id']
    estimate['works'] = [{'id': uid(), 'name': 'Escalier', 'items': [root, child, grandchild]}]
    estimate = store.save_estimate(estimate, estimate['revision'])
    widget = MainWindow(store)
    widget.timer.stop()
    widget.current = estimate
    widget.render()
    yield widget
    widget.dirty = widget.settings_dirty = False
    widget.close()


def test_duplicate_work_independent_and_saved(window):
    original = copy.deepcopy(window.current['works'][0])
    amount = calculate(window.current)['ht_cents']
    window.duplicate_work(original['id'])
    duplicate = window.current['works'][1]
    assert window.dirty and window.active_work_id == duplicate['id']
    assert window.work_tabs.currentIndex() == 1
    assert duplicate['name'] == 'Escalier — copie'
    assert duplicate['id'] != original['id']
    assert not {i['id'] for i in original['items']} & {i['id'] for i in duplicate['items']}
    assert duplicate['items'][1]['parent_id'] == duplicate['items'][0]['id']
    assert duplicate['items'][2]['parent_id'] == duplicate['items'][1]['id']
    assert calculate(window.current)['ht_cents'] == 2 * amount
    duplicate['items'][0]['duration_minutes'] = 45
    assert window.current['works'][0] == original
    assert window.save_current()
    assert window.store.get_estimate(window.current['id'])['works'][1] == duplicate
    window.duplicate_work(original['id'])
    assert window.current['works'][1]['name'] == 'Escalier — copie 2'


def test_duplicate_subpost_retains_external_parent(window):
    items = window.current['works'][0]['items']
    before = copy.deepcopy(items)
    window.duplicate_item(items[1]['id'])
    assert len(items) == 5
    root, child = items[3:]
    assert root['label'] == 'Coupe — copie'
    assert root['parent_id'] == before[0]['id']
    assert child['parent_id'] == root['id']
    assert root['id'] != before[1]['id'] and child['id'] != before[2]['id']
    assert items[:3] == before
    assert window.tree.currentItem().data(0, Qt.UserRole) == root['id']
    assert calculate(window.current)['ht_cents'] == 15000
    assert window.save_current()


def test_duplicate_selected_parent_copies_all_descendants(window):
    window.tree.setCurrentItem(window.tree.topLevelItem(0))
    window.duplicate_item()
    items = window.current['works'][0]['items']
    assert len(items) == 6
    assert items[3].get('parent_id') is None
    assert items[4]['parent_id'] == items[3]['id']
    assert items[5]['parent_id'] == items[4]['id']
    assert calculate(window.current)['ht_cents'] == 18000


def test_frozen_estimate_cannot_duplicate(window):
    window.current = window.store.freeze_estimate(window.current['id'], window.current['revision'])
    window.render()
    original = copy.deepcopy(window.current)
    window.duplicate_work()
    window.duplicate_item(original['works'][0]['items'][0]['id'])
    assert window.current == original
    assert not window.dirty
