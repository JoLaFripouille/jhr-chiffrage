"""Parcours bureau exécutables sans ouvrir de fenêtre native."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox
from jhr_chiffrage.core import Store
from jhr_chiffrage.ui import MainWindow, ItemDialog, uid, duration_text, POST_AMOUNT_COLUMN, TOTAL_COLUMN, TOTAL_HOURS_COLUMN
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from jhr_chiffrage import ui


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    store = Store(tmp_path / "ui.sqlite")
    settings = store.get_settings()
    settings.update(hourly_rate="50", levy_rate="20", vat_enabled=False, vat_rate="0")
    store.save_settings(settings, settings["revision"])
    widget = MainWindow(store)
    widget.timer.stop()
    yield widget
    widget.dirty = False
    widget.settings_dirty = False
    widget.close()


def test_create_edit_save_and_totals(window, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Escalier", True))
    window.new_estimate()
    window.add_work()
    window.current["works"][0]["items"].append({
        "id": uid(), "label": "Plans EXE", "mode": "hourly", "quantity": "2",
        "hours": "3", "rate": None, "price": None, "estimated_hours": None,
    })
    window.changed_tree()
    assert window.dirty
    assert "300,00" in window.totals.text()
    window.metadata["client"].setText("Atelier test")
    assert window.save_current()
    saved = window.store.get_estimate(window.current["id"])
    assert saved["client"] == "Atelier test"
    assert len(saved["works"][0]["items"]) == 1
    assert not window.dirty
    assert window.tree.topLevelItem(0).text(POST_AMOUNT_COLUMN) == "300,00 €"
    assert "300,00 €" in window.work_summary.text()
    assert not window.revise_button.isEnabled()
    assert "50 €/h" in window.totals.text()
    assert window.history.topLevelItemCount() >= 3


def test_hover_add_subpost_persist_and_remove_branch(window, monkeypatch, app):
    from jhr_chiffrage.core import new_item, calculate
    window.current = window.store.create_estimate("Sous-postes")
    parent = new_item("Plan")
    parent["duration_minutes"] = 60
    other = new_item("Autre poste")
    other["duration_minutes"] = 30
    window.current["works"] = [{"id": uid(), "name": "Ouvrage", "items": [parent, other]}]
    window.render()
    window.show()
    app.processEvents()
    row = window.tree.topLevelItem(0)
    QTest.mouseMove(window.tree.viewport(), window.tree.visualItemRect(row).center())
    app.processEvents()
    assert window.tree.child_button.isVisible()
    assert window.tree.hover_id == parent["id"]
    child = new_item("Détail")
    child["duration_minutes"] = 15
    monkeypatch.setattr(ItemDialog, "exec", lambda self: 1)
    monkeypatch.setattr(ItemDialog, "value", lambda self: child.copy())
    QTest.mouseClick(window.tree.child_button, Qt.LeftButton)
    assert window.tree.topLevelItemCount() == 2
    assert window.tree.topLevelItem(0).child(0).text(0) == "Détail"
    assert window.save_current()
    saved = window.store.get_estimate(window.current["id"])
    assert saved["works"][0]["items"][-1]["parent_id"] == parent["id"]
    assert calculate(saved)["ht_cents"] == 8750
    window.tree.setCurrentItem(window.tree.topLevelItem(0))
    messages = []
    def confirm(*args):
        messages.append(args[2])
        return QMessageBox.Yes
    monkeypatch.setattr(QMessageBox, "question", confirm)
    window.remove_selected()
    assert "1 sous-poste" in messages[0]
    assert [i["id"] for i in window.current["works"][0]["items"]] == [other["id"]]


def test_frozen_subposts_cannot_be_added(window, app):
    from jhr_chiffrage.core import new_item
    window.current = window.store.create_estimate("Lecture seule")
    item = new_item("Plan")
    item["duration_minutes"] = 60
    window.current["works"] = [{"id": uid(), "name": "Ouvrage", "items": [item]}]
    window.current["status"] = "frozen"
    window.render()
    window.show()
    app.processEvents()
    window.tree.show_action(window.tree.topLevelItem(0))
    assert not window.tree.child_button.isVisible()
    window.add_sub_item(item["id"])
    assert len(window.current["works"][0]["items"]) == 1


@pytest.mark.parametrize('target_child,accept', [(False, True), (True, True), (True, False)])
def test_context_delete_targets_clicked_branch(window, app, monkeypatch, target_child, accept):
    from jhr_chiffrage.core import new_item
    from PySide6.QtWidgets import QMenu
    root, child, other = [new_item(name) for name in ('Parent', 'Enfant', 'Autre')]
    child['parent_id'] = root['id']
    window.current = window.store.create_estimate('Suppression')
    window.current['works'] = [{'id': uid(), 'name': 'Ouvrage', 'items': [root, child, other]}]
    window.render()
    window.show()
    app.processEvents()
    tree = window.tree
    tree.setCurrentItem(tree.topLevelItem(1))  # Deliberately select another row.
    target = tree.topLevelItem(0).child(0) if target_child else tree.topLevelItem(0)
    messages = []
    def confirm(*args):
        messages.append(args[2])
        return QMessageBox.Yes if accept else QMessageBox.No
    monkeypatch.setattr(QMessageBox, 'question', confirm)
    class TestMenu(QMenu):
        def exec(self, point):
            next(action for action in self.actions() if action.text() == 'Supprimer le poste').trigger()
    monkeypatch.setattr(ui, 'QMenu', TestMenu)
    tree.context_menu(tree.visualItemRect(target).center())
    ids = [item['id'] for item in window.current['works'][0]['items']]
    expected = [root['id'], other['id']] if target_child else [other['id']]
    assert ids == (expected if accept else [root['id'], child['id'], other['id']])
    assert window.dirty == accept
    assert bool('1 sous-poste' in messages[0]) == (not target_child)


def test_edit_subpost_dialog_preserves_parent(app):
    from jhr_chiffrage.core import new_item
    item = new_item("Détail")
    item.update(parent_id="parent", duration_minutes=20)
    dialog = ItemDialog(item)
    dialog.label.setText("Détail modifié")
    dialog.validate()
    assert dialog.result() == 1
    assert dialog.value()["parent_id"] == "parent"


def test_connection_failure_does_not_save_profile(app, monkeypatch):
    from jhr_chiffrage.core import DomainError
    monkeypatch.setattr(ui, "load_connection", lambda: {"mode": "local"})
    saved, closed = [], []
    class Unavailable:
        def __init__(self, config):
            pass
        def health(self):
            raise DomainError("CONNECTION_ERROR", "Serveur indisponible")
        def close(self):
            closed.append(True)
    monkeypatch.setattr(ui, "RemoteStore", Unavailable)
    monkeypatch.setattr(ui, "save_connection", saved.append)
    dialog = ui.ConnectionDialog()
    dialog.mode.setCurrentIndex(1)
    dialog.validate()
    assert saved == []
    assert closed == [True]
    assert dialog.result() == 0
    assert "indisponible" in dialog.result_label.text()


def test_external_revision_refresh_preserves_dirty(window):
    window.current = window.store.create_estimate("Affaire partagée")
    window.render()
    external = window.store.get_estimate(window.current["id"])
    external["name"] = "Modifié ailleurs"
    window.store.save_estimate(external, external["revision"])
    window.poll()
    assert window.metadata["name"].text() == "Modifié ailleurs"
    window.metadata["name"].setText("Modification locale")
    window.mark_dirty()
    external = window.store.get_estimate(window.current["id"])
    external["name"] = "Nouvelle modification distante"
    window.store.save_estimate(external, external["revision"])
    window.poll()
    assert window.metadata["name"].text() == "Modification locale"
    assert window.dirty


def test_conflict_keeps_user_edits(window, monkeypatch):
    window.current = window.store.create_estimate("Affaire")
    window.render()
    window.metadata["name"].setText("Mon changement")
    window.mark_dirty()
    external = window.store.get_estimate(window.current["id"])
    external["client"] = "Changement externe"
    window.store.save_estimate(external, external["revision"])
    errors = []
    monkeypatch.setattr(window, "error", errors.append)
    assert not window.save_current()
    assert errors[0].code == "REVISION_CONFLICT"
    assert window.dirty
    assert window.metadata["name"].text() == "Mon changement"


def test_settings_refresh_is_explicit(window):
    window.current = window.store.create_estimate("Affaire")
    window.render()
    settings = window.store.get_settings()
    settings["hourly_rate"] = "80"
    window.store.save_settings(settings, settings["revision"])
    window.poll()
    assert window.current["settings"]["hourly_rate"] == "50"
    window.refresh_rates()
    assert window.current["settings"]["hourly_rate"] == "80"


def test_item_modes_and_decimal_input(app):
    dialog = ItemDialog({"template_id": "origin", "template_revision": 7})
    dialog.label.setText("Forfait étude")
    dialog.mode.setCurrentIndex(1)
    dialog.fields["price"].setText("250,50")
    assert not dialog.fields["hours"].isEnabled()
    assert dialog.fields["price"].isEnabled()
    assert dialog.value()["price"] == "250.50"
    assert dialog.value()["quantity"] == "1"
    assert dialog.value()["template_id"] == "origin"
    assert dialog.value()["template_revision"] == 7


def test_incomplete_messages_are_bounded(window):
    window.current = window.store.create_estimate("Incomplète")
    window.current["works"] = [{"id": uid(), "name": f"Ouvrage {i}", "items": []} for i in range(8)]
    window.render()
    assert "+ 5 autre(s)" in window.totals.text()
    assert "Ouvrage 7 : aucun poste" not in window.totals.text()


def test_frozen_editor_is_read_only(window):
    window.current = window.store.create_estimate("Version figée")
    window.current["status"] = "frozen"
    window.render()
    assert window.metadata["name"].isReadOnly()
    assert not window.add_work_button.isEnabled()
    assert not window.save_button.isEnabled()
    assert window.revise_button.isEnabled()


def two_works(window):
    window.current = window.store.create_estimate("Affaire à onglets")
    window.current["works"] = [
        {"id": uid(), "name": name, "items": [
            {"id": uid(), "label": label, "mode": "hourly", "quantity": "1", "hours": hours}
        ]} for name, label, hours in [("Escalier", "Plans FAB", "2"), ("Garde-corps", "Plans EXE", "3")]
    ]
    window.current = window.store.save_estimate(window.current, window.current["revision"])
    window.render()


def test_work_footer_hours_amount_and_fixed_scroll_position(window, app):
    from jhr_chiffrage.core import new_item
    from PySide6.QtCore import QPoint
    two_works(window)
    assert '2 h 00 min' in window.work_summary.text()
    assert '100,00 € HT' in window.work_summary.text()
    window.work_tabs.setCurrentIndex(1)
    assert '3 h 00 min' in window.work_summary.text()
    assert '150,00 € HT' in window.work_summary.text()
    window.work_tabs.setCurrentIndex(0)
    items = window.current['works'][0]['items']
    child = new_item('Détail')
    child.update(parent_id=items[0]['id'], duration_minutes=25, quantity='2')
    items.append(child)
    for index in range(40):
        item = new_item(f'Ligne {index}')
        item.update(mode='fixed', price='0', estimated_minutes=0)
        items.append(item)
    window.changed_tree()
    window.resize(1280, 720)
    window.show()
    app.processEvents()
    assert '2 h 50 min' in window.work_summary.text()
    assert '141,67 € HT' in window.work_summary.text()
    position = window.work_summary.mapTo(window, QPoint(0, 0))
    window.tree.verticalScrollBar().setValue(window.tree.verticalScrollBar().maximum())
    app.processEvents()
    assert window.work_summary.mapTo(window, QPoint(0, 0)) == position
    assert window.work_summary.isVisible()
    assert position.y() >= window.tree.mapTo(window, QPoint(0, window.tree.height())).y()
    for width in (1280, 1460):
        window.resize(width, 720)
        window.tree.setColumnWidth(3, 130)
        app.processEvents()
        for field, column in ((window.work_summary.hours, TOTAL_HOURS_COLUMN), (window.work_summary.amount, TOTAL_COLUMN)):
            assert field.mapTo(window, QPoint(0, 0)).x() == window.tree.viewport().mapTo(window, QPoint(window.tree.header().sectionViewportPosition(column), 0)).x()
            assert field.width() == window.tree.columnWidth(column)
            assert field.font().bold()



def test_principal_hours_include_quantities_and_all_generations(window, app):
    from copy import deepcopy
    from decimal import Decimal
    from jhr_chiffrage.core import new_item, calculate
    from jhr_chiffrage.ui import tree_nodes
    from jhr_chiffrage.agent_workflow import outline

    window.current = window.store.create_estimate("Hours hierarchy")
    root, child, grandchild, leaf, other = [new_item(str(i)) for i in range(5)]
    root.update(duration_minutes=30, quantity='2')
    child.update(parent_id=root['id'], duration_minutes=20, quantity='3')
    grandchild.update(parent_id=child['id'], mode='fixed', price='50', quantity='4', estimated_minutes=15)
    leaf.update(parent_id=grandchild['id'], hours='0.25')
    other.update(duration_minutes=10)
    window.current['works'] = [{'id': uid(), 'name': 'Lot', 'items': [leaf, child, other, grandchild, root]}]
    original = deepcopy(window.current)
    window.changed_tree()
    nodes = {node.data(0, Qt.UserRole): node for node in tree_nodes(window.tree)}
    assert nodes[root['id']].text(3) == '0 h 30 min'
    assert nodes[child['id']].text(3) == '0 h 20 min'
    assert nodes[root['id']].text(TOTAL_HOURS_COLUMN) == '2 h 30 min'
    assert nodes[root['id']].font(TOTAL_HOURS_COLUMN).bold()
    assert nodes[other['id']].text(TOTAL_HOURS_COLUMN) == '0 h 10 min'
    assert all(nodes[item['id']].text(TOTAL_HOURS_COLUMN) == '' for item in (child, grandchild, leaf))
    assert window.work_summary.hours.text() == '2 h 40 min'
    assert window.current == original
    branch = next(node for node in outline(window.current)['lots'][0]['ouvrages'] if node['id'] == root['id'])
    assert Decimal(branch['subtree_hours']) == Decimal('2.5')
    assert Decimal(branch['children'][0]['subtree_hours']) == Decimal('1.5')
    nodes[root['id']].setExpanded(False)
    window.render_tree()
    assert window.work_summary.hours.text() == '2 h 40 min'
    leaf['hours'] = '0.5'
    window.changed_tree()
    nodes = {node.data(0, Qt.UserRole): node for node in tree_nodes(window.tree)}
    assert nodes[root['id']].text(TOTAL_HOURS_COLUMN) == '2 h 45 min'
    assert window.work_summary.hours.text() == '2 h 55 min'
    assert duration_text(calculate(window.current)['hours']) == '2 h 55 min'


def test_tabs_keep_unsaved_changes_and_target_active_work(window, monkeypatch):
    two_works(window)
    assert window.work_tabs.count() == 2
    assert window.tree.topLevelItem(0).text(0) == "Plans FAB"
    window.metadata["client"].setText("Client modifié")
    window.mark_dirty()
    window.work_tabs.setCurrentIndex(1)
    assert window.tree.topLevelItem(0).text(0) == "Plans EXE"
    assert window.selection() == (1, None)
    monkeypatch.setattr(ItemDialog, "exec", lambda self: True)
    monkeypatch.setattr(ItemDialog, "value", lambda self: {
        "id": uid(), "label": "Contrôle", "mode": "fixed", "quantity": "1", "price": "40"})
    window.add_item()
    assert len(window.current["works"][0]["items"]) == 1
    assert len(window.current["works"][1]["items"]) == 2
    assert window.metadata["client"].text() == "Client modifié"
    assert window.save_current()
    assert window.work_tabs.currentIndex() == 1
    assert "290,00" in window.totals.text()
    assert window.store.get_estimate(window.current["id"])["client"] == "Client modifié"


def test_tab_reordering_rename_and_remove_are_persisted(window, monkeypatch):
    two_works(window)
    active_id = window.active_work_id
    window.work_tabs.moveTab(0, 1)
    assert window.current["works"][0]["name"] == "Garde-corps"
    assert window.active_work_id == active_id
    assert window.tree.topLevelItem(0).text(0) == "Plans FAB"
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Escalier principal", True))
    window.rename_work(1)
    assert window.work_tabs.tabText(1) == "Escalier principal"
    assert window.save_current()
    assert window.store.get_estimate(window.current["id"])["works"][1]["name"] == "Escalier principal"
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    window.remove_work()
    assert window.work_tabs.count() == 2
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    window.remove_work()
    assert window.work_tabs.count() == 1
    assert window.tree.topLevelItem(0).text(0) == "Plans EXE"
    window.remove_work()
    assert window.work_tabs.count() == 0 and window.selection() == (None, None)
    assert window.tree.topLevelItemCount() == 0


def test_frozen_tabs_remain_navigable_without_writes(window):
    two_works(window)
    window.current = window.store.freeze_estimate(window.current["id"], window.current["revision"])
    window.render()
    window.work_tabs.setCurrentIndex(1)
    assert window.tree.topLevelItem(0).text(0) == "Plans EXE"
    assert not window.work_tabs.isMovable()
    assert all(not action.isEnabled() for action in window.work_actions)
    window.remove_work()
    window.add_item()
    assert window.work_tabs.count() == 2
    assert not window.dirty


def test_template_targets_active_tab_and_external_refresh_keeps_selection(window, monkeypatch):
    two_works(window)
    window.work_tabs.setCurrentIndex(1)
    active_id = window.active_work_id
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: (a[3][0], True))
    window.apply_template()
    assert window.active_work_id == active_id
    assert len(window.current["works"][0]["items"]) == 1
    assert len(window.current["works"][1]["items"]) > 1
    changed = window.store.get_estimate(window.current["id"])
    changed["works"][1]["name"] = "Nom changé par MCP"
    window.store.save_estimate(changed, changed["revision"])
    window.poll()
    assert window.active_work_id == active_id
    assert window.work_tabs.tabText(1) == "Nom changé par MCP"


def test_hours_minutes_entry_and_legacy_precision(app):
    dialog = ItemDialog()
    dialog.label.setText("Dessin")
    duration = dialog.fields["hours"]
    duration.hours.setValue(2)
    duration.minutes.setValue(30)
    assert dialog.value()["duration_minutes"] == 150
    assert dialog.value()["hours"] is None
    assert duration_text(minutes=150) == "2 h 30 min"
    old = ItemDialog({"id": uid(), "label": "Ancien poste", "hours": "1.111111", "mode": "hourly"})
    old.label.setText("Libellé modifié")
    assert old.value()["hours"] == "1.111111"
    assert old.value()["duration_minutes"] is None
    old.fields["hours"].minutes.setValue(8)
    assert old.value()["duration_minutes"] == 68
    assert old.value()["hours"] is None


def test_new_work_plus_sits_after_tabs_and_keyboard_cycles(window, app):
    two_works(window)
    window.show()
    app.processEvents()
    last_tab = window.work_tabs.tabRect(window.work_tabs.count() - 1)
    gap = window.add_work_button.x() - (window.work_tabs.x() + last_tab.right() + 1)
    assert 0 <= gap <= 12
    window.cycle_work(1)
    assert window.work_tabs.currentIndex() == 1
    window.cycle_work(1)
    assert window.work_tabs.currentIndex() == 0
    assert not window.commercial_panel.isVisible()
    window.details_toggle.setChecked(True)
    assert window.commercial_panel.isVisible()


def test_item_dialog_shows_only_fields_for_selected_mode(app):
    dialog = ItemDialog()
    assert dialog.form.isRowVisible(dialog.fields["hours"])
    assert not dialog.form.isRowVisible(dialog.fields["price"])
    assert not dialog.form.isRowVisible(dialog.fields["estimated_hours"])
    assert dialog.fields["hours"].hours.text() == "Heures"
    assert dialog.value()["duration_minutes"] is None
    dialog.fields["hours"].hours.setValue(1)
    dialog.fields["hours"].minutes.setValue(20)
    dialog.mode.setCurrentIndex(1)
    assert not dialog.form.isRowVisible(dialog.fields["hours"])
    assert dialog.form.isRowVisible(dialog.fields["price"])
    dialog.mode.setCurrentIndex(0)
    assert dialog.value()["duration_minutes"] == 80


def test_long_reference_starts_at_beginning_with_full_tooltip(window):
    two_works(window)
    reference = "Longue référence — bâtiment communal — plans d’exécution et détails"
    window.current["reference"] = reference
    window.render()
    assert window.metadata["reference"].cursorPosition() == 0
    assert window.metadata["reference"].toolTip() == reference


def test_principal_totals_include_all_descendants_once(window, app):
    from jhr_chiffrage.core import new_item, calculate
    from jhr_chiffrage.ui import tree_nodes, POST_AMOUNT_COLUMN, TOTAL_COLUMN, ACTION_COLUMN
    window.current = window.store.create_estimate("Hierarchy totals")
    root, child, grandchild, greatgrandchild, other = [new_item(name) for name in ("Root", "Child", "Grandchild", "Great-grandchild", "Other")]
    for item, price in zip((root, child, grandchild, greatgrandchild, other), ('80','20','20','10','15')):
        item.update(mode='fixed', price=price, quantity='1')
    child.update(parent_id=root['id'], quantity='2')
    grandchild['parent_id']=child['id']
    greatgrandchild['parent_id']=grandchild['id']
    # Descendants need not appear after their parents in storage.
    window.current['works']=[{'id':uid(),'name':'Work','items':[greatgrandchild,grandchild,child,root,other]}]
    window.changed_tree()
    nodes={node.data(0,Qt.UserRole):node for node in tree_nodes(window.tree)}
    assert nodes[root['id']].text(POST_AMOUNT_COLUMN)=='80,00 €'
    assert nodes[root['id']].text(TOTAL_COLUMN)=='150,00 €'
    assert nodes[child['id']].text(POST_AMOUNT_COLUMN)=='40,00 €'
    assert nodes[grandchild['id']].text(POST_AMOUNT_COLUMN)=='20,00 €'
    assert nodes[greatgrandchild['id']].text(POST_AMOUNT_COLUMN)=='10,00 €'
    assert all(nodes[item['id']].text(TOTAL_COLUMN)=='' for item in (child,grandchild,greatgrandchild))
    assert nodes[other['id']].text(TOTAL_COLUMN)=='15,00 €'
    assert nodes[root['id']].font(TOTAL_COLUMN).bold()
    assert calculate(window.current)['ht_cents']==16500
    assert window.work_summary.amount.text()=='165,00 €'
    nodes[root['id']].setExpanded(False)
    window.render_tree()
    assert window.tree.topLevelItem(0).text(TOTAL_COLUMN)=='150,00 €'
    assert not window.tree.topLevelItem(0).isExpanded()
    window.tree.show_action(window.tree.topLevelItem(0))
    assert window.tree.child_button.x()==window.tree.columnViewportPosition(ACTION_COLUMN)+3
    greatgrandchild['price']='30'
    window.changed_tree()
    assert window.tree.topLevelItem(0).text(TOTAL_COLUMN)=='170,00 €'
    assert window.work_summary.amount.text()=='185,00 €'
