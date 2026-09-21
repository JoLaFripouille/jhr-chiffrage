"""Keep useful estimate rows visible on laptop-sized desktop windows."""
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QLabel

from jhr_chiffrage.core import Store, new_item
from jhr_chiffrage.ui import MainWindow, uid


@pytest.mark.parametrize("width,height", [(1366, 768), (1280, 720)])
def test_laptop_table_space(tmp_path, monkeypatch, width, height):
    app = QApplication.instance() or QApplication([])
    # Use the native Linux font when available; load Windows fonts explicitly
    # because headless Qt does not always discover the platform font directory.
    for filename in ("segoeui.ttf", "segoeuib.ttf"):
        font = Path("C:/Windows/Fonts") / filename
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    family = "DejaVu Sans" if "DejaVu Sans" in QFontDatabase.families() else "Segoe UI"
    monkeypatch.setattr(MainWindow, "start_sync", lambda *args, **kwargs: None)
    store = Store(tmp_path / "layout.sqlite")
    # The connection status bar consumes real space on the Ubuntu client.
    store.synchronize = lambda: None
    store.status_text = "À jour"
    store.has_conflict = False
    settings = store.get_settings()
    settings.update(hourly_rate="80", vat_rate="20", levy_rate="25")
    store.save_settings(settings, settings["revision"])
    obj = store.create_estimate("Affaire de démonstration", client="Atelier exemple")
    items = []
    for index in range(20):
        item = new_item(f"Poste {index + 1} — plans et détails")
        item["duration_minutes"] = 30
        if index % 3:
            item["parent_id"] = items[index - index % 3]["id"]
        items.append(item)
    obj["works"] = [{"id": uid(), "name": "Escalier métallique", "items": items}]
    obj = store.save_estimate(obj, obj["revision"])
    window = MainWindow(store)
    try:
        window.timer.stop()
        window.sync_timer.stop()
        window.setFont(QFont(family, 10))
        window.current = obj
        window.render()
        window.resize(width, height)
        window.show()
        app.processEvents()
        assert window.size().width() == width
        assert window.size().height() == height
        row = window.tree.topLevelItem(0)
        row_height = window.tree.visualItemRect(row).height()
        visible_rows = window.tree.viewport().height() // row_height
        assert visible_rows >= 10, (visible_rows, row_height, window.tree.viewport().height())
        footer_bottom = window.footer.mapTo(window, QPoint(0, window.footer.height())).y()
        assert footer_bottom <= window.statusBar().geometry().top()
        assert window.totals.height() < 85
        assert all(field.isVisible() for field in window.totals.values.values())
        assert not any(label.text() == "Chiffrage de l’affaire" for label in window.findChildren(QLabel))
        if os.environ.get("JHR_LAYOUT_PREVIEW"):
            output = Path(os.environ["JHR_LAYOUT_PREVIEW"])
            output.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(output / f"compact-{width}x{height}.png"))
    finally:
        window.dirty = window.settings_dirty = False
        window.close()
