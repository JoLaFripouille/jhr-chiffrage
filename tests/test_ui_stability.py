"""Keep the desktop viewport stable while background data is refreshed."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from jhr_chiffrage.core import Store, new_item
from jhr_chiffrage.ui import MainWindow, uid


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    store = Store(tmp_path / "stability.sqlite")
    for number in range(30):
        store.create_estimate(f"Affaire de test {number:02}")
    widget = MainWindow(store)
    widget.timer.stop()
    widget.resize(1180, 768)
    widget.show()
    app.processEvents()
    yield widget
    widget.dirty = False
    widget.settings_dirty = False
    widget.close()


def populate_works(window, app):
    window.current = window.store.create_estimate("Stabilité des ouvrages")
    works = []
    for name in ("Premier ouvrage", "Ouvrage observé"):
        items = []
        for number in range(40):
            parent = new_item(f"Poste {number:02}")
            parent["duration_minutes"] = 15
            child = new_item(f"Détail {number:02}")
            child.update(parent_id=parent["id"], duration_minutes=5)
            items.extend((parent, child))
        works.append({"id": uid(), "name": name, "items": items})
    window.current["works"] = works
    window.current = window.store.save_estimate(window.current, window.current["revision"])
    window.render()
    window.refresh_lists()
    window.work_tabs.setCurrentIndex(1)
    app.processEvents()


def test_unchanged_poll_preserves_list_items_and_sidebar_scroll(window, app):
    estimates = [window.estimates.item(i) for i in range(window.estimates.count())]
    templates = [window.templates.item(i) for i in range(window.templates.count())]
    history = [window.history.topLevelItem(i) for i in range(window.history.topLevelItemCount())]
    assert estimates and templates and history
    scrollbar = window.estimates.verticalScrollBar()
    assert scrollbar.maximum() > 0
    scrollbar.setValue(scrollbar.maximum())
    position = scrollbar.value()

    for _ in range(3):
        window.poll()
        app.processEvents()

    assert all(window.estimates.item(i) is item for i, item in enumerate(estimates))
    assert all(window.templates.item(i) is item for i, item in enumerate(templates))
    assert all(window.history.topLevelItem(i) is item for i, item in enumerate(history))
    assert scrollbar.value() == position


def test_remote_revision_preserves_tab_selection_collapsed_branch_and_scroll(window, app):
    populate_works(window, app)
    tree = window.tree
    tree.topLevelItem(0).setExpanded(False)
    selected = tree.topLevelItem(25).child(0)
    tree.setCurrentItem(selected)
    tree.scrollToItem(selected)
    app.processEvents()
    selected_id = selected.data(0, Qt.UserRole)
    collapsed_id = tree.topLevelItem(0).data(0, Qt.UserRole)
    active_work_id = window.active_work_id
    position = tree.verticalScrollBar().value()
    assert position > 0

    external = window.store.get_estimate(window.current["id"])
    external["works"][1]["items"][0]["label"] = "Poste modifié à distance"
    window.store.save_estimate(external, external["revision"])
    window.poll()
    app.processEvents()

    assert window.active_work_id == active_work_id
    assert window.work_tabs.currentIndex() == 1
    assert tree.currentItem().data(0, Qt.UserRole) == selected_id
    assert tree.topLevelItem(0).data(0, Qt.UserRole) == collapsed_id
    assert not tree.topLevelItem(0).isExpanded()
    assert tree.verticalScrollBar().value() == position
    assert tree.topLevelItem(0).text(0) == "Poste modifié à distance"


def test_nested_rows_have_pastel_background_and_roots_stay_bold(window, app):
    populate_works(window, app)
    root = window.tree.topLevelItem(0)
    child = root.child(0)
    assert root.font(0).bold()
    for column in range(window.tree.columnCount()):
        assert child.background(column).color().name() == "#fff5cc"
        assert child.foreground(column).color().name() == "#72551c"
    # Check the painted result too: Qt stylesheets can override BackgroundRole.
    rect = window.tree.visualItemRect(child)
    pixels = window.tree.viewport().grab().toImage()
    x = window.tree.columnViewportPosition(6) + 20
    assert pixels.pixelColor(x, rect.center().y()).name() == '#fff5cc'
    # A root without children remains visually distinct too.
    leaf = new_item("Poste autonome")
    leaf["duration_minutes"] = 10
    window.current["works"][1]["items"].append(leaf)
    window.render_tree()
    assert window.tree.topLevelItem(40).font(0).bold()
