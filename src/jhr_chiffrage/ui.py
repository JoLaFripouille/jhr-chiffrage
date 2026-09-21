"""Interface bureau française, locale ou synchronisée avec un serveur."""
from __future__ import annotations

import copy
import sys
import uuid
from pathlib import Path
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from PySide6.QtCore import Qt, QTimer, QThread, QPointF, Signal
from PySide6.QtGui import QFont, QKeySequence, QShortcut, QPainter, QPen, QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QSplitter, QTabWidget,
    QTextEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QTabBar,
    QFrame, QSizePolicy, QHeaderView, QToolButton, QMenu, QSpinBox, QAbstractSpinBox,
)
from . import __version__
from .core import Store, calculate, DomainError, clone_items
from .theme import LIGHT_THEME
from .connection import load_connection, save_connection, open_store, RemoteStore


def uid():
    return str(uuid.uuid4())


def branch_icon():
    # Vector-like painted icon avoids missing branch glyphs across OS fonts.
    pixmap = QPixmap(32, 32)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#2764a5"), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawPolyline([QPointF(3, 2), QPointF(3, 10), QPointF(13, 10)])
    painter.drawPolyline([QPointF(10, 7), QPointF(13, 10), QPointF(10, 13)])
    painter.end()
    return QIcon(pixmap)


def descendant_ids(items, item_id):
    result = {item_id}
    while True:
        more = {item["id"] for item in items if item.get("parent_id") in result} - result
        if not more:
            return result
        result.update(more)


class PostTree(QTreeWidget):
    addChildRequested = Signal(str)

    def __init__(self):
        super().__init__()
        self.editable = False
        self.hover_id = None
        self.setMouseTracking(True)
        self.child_button = QPushButton("+ Sous-poste", self.viewport())
        self.child_button.setStyleSheet("QPushButton { padding: 3px 6px; font-size: 12px; }")
        self.child_button.hide()
        self.child_button.clicked.connect(self.request_child)
        self.verticalScrollBar().valueChanged.connect(self.hide_action)
        self.horizontalScrollBar().valueChanged.connect(self.hide_action)
        self.header().sectionResized.connect(self.hide_action)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.context_menu)

    def hide_action(self, *_):
        self.child_button.hide()
        self.hover_id = None

    def request_child(self):
        if self.editable and self.hover_id:
            item_id = self.hover_id
            self.hide_action()
            self.addChildRequested.emit(item_id)

    def show_action(self, item):
        if not self.editable or item is None:
            self.hide_action()
            return
        rect = self.visualItemRect(item)
        self.hover_id = item.data(0, Qt.UserRole)
        self.child_button.setGeometry(self.columnViewportPosition(6) + 3, rect.y() + 3,
                                      self.columnWidth(6) - 6, max(24, rect.height() - 6))
        self.child_button.show()
        self.child_button.raise_()

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        self.show_action(self.itemAt(event.position().toPoint()))

    def leaveEvent(self, event):
        if not self.child_button.underMouse():
            self.hide_action()
        super().leaveEvent(event)

    def context_menu(self, point):
        item = self.itemAt(point) or self.currentItem()
        if not self.editable or item is None:
            return
        item_id = item.data(0, Qt.UserRole)
        menu = QMenu(self)
        menu.addAction("Ajouter un sous-poste", lambda: self.addChildRequested.emit(item_id))
        menu.exec(self.viewport().mapToGlobal(point))


def money(cents):
    return f"{cents / 100:,.2f} €".replace(",", " ").replace(".", ",")


def duration_text(hours=None, minutes=None):
    if minutes is None and hours in (None, ""):
        return "—"
    total = minutes if minutes is not None else int((Decimal(str(hours).replace(",", ".")) * 60).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    h, m = divmod(total, 60)
    return f"{h} h {m:02d} min"


class DurationInput(QWidget):
    """Hours and minutes control; retain legacy precision until time is edited."""
    def __init__(self, hours=None, minutes=None):
        super().__init__()
        self.edited = False
        self.legacy_hours = hours
        self.initial_minutes = minutes
        self.hours = QSpinBox()
        self.hours.setRange(-1, 999999999)
        self.hours.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.hours.setSpecialValueText("Heures")
        self.hours.setSuffix(" h")
        self.hours.setAccessibleName("Heures")
        self.minutes = QSpinBox()
        self.minutes.setRange(0, 59)
        self.minutes.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.minutes.setSuffix(" min")
        self.minutes.setAccessibleName("Minutes")
        self.hours.setToolTip("Heures entières. Saisir 0 pour une durée inférieure à une heure.")
        self.minutes.setToolTip("Minutes : de 0 à 59.")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.hours, 1)
        layout.addWidget(self.minutes, 1)
        total = minutes
        if total is None and hours not in (None, ""):
            total = int((Decimal(str(hours).replace(",", ".")) * 60).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        h, m = divmod(total, 60) if total is not None else (-1, 0)
        self.hours.setValue(h)
        self.minutes.setValue(m)
        self.hours.valueChanged.connect(self.changed)
        self.minutes.valueChanged.connect(self.changed)

    def changed(self, *_):
        self.edited = True
        if self.minutes.value() > 0 and self.hours.value() < 0:
            self.hours.setValue(0)

    def values(self, hours_key, minutes_key):
        if not self.edited and self.initial_minutes is None and self.legacy_hours not in (None, ""):
            return {hours_key: self.legacy_hours, minutes_key: None}
        total = None if self.hours.value() < 0 else self.hours.value() * 60 + self.minutes.value()
        return {hours_key: None, minutes_key: total}


def button(label, callback, layout):
    widget = QPushButton(label)
    widget.clicked.connect(callback)
    layout.addWidget(widget)
    return widget


def label(text, name):
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setTextFormat(Qt.PlainText)
    return widget


class SummaryPanel(QWidget):
    """Financial summary with a plain-text equivalent for accessibility."""
    def __init__(self):
        super().__init__()
        self._text = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        heading = QHBoxLayout()
        heading.addWidget(label("SYNTHÈSE DE L’AFFAIRE", "eyebrow"))
        self.hours = label("", "subtle")
        heading.addStretch()
        heading.addWidget(self.hours)
        layout.addLayout(heading)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.values = {}
        for title, key, kind in [
            ("Total HT", "ht_cents", "metricPrimary"),
            ("TVA", "vat_cents", "metricCard"),
            ("Total TTC", "ttc_cents", "metricCard"),
            ("Prélèvements estimés", "levy_cents", "metricCard"),
            ("Après prélèvements", "balance_cents", "metricBalance"),
        ]:
            card = QFrame()
            card.setObjectName(kind)
            body = QVBoxLayout(card)
            body.setContentsMargins(14, 7, 14, 7)
            body.addWidget(label(title, "metricCaption"))
            value = label("—", "metricValue")
            body.addWidget(value)
            self.values[key] = value
            row.addWidget(card, 1)
        layout.addLayout(row)
        self.note = label("", "summaryNote")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.warning = label("", "warning")
        self.warning.setWordWrap(True)
        self.warning.hide()
        layout.addWidget(self.warning)

    def setText(self, text):
        self._text = text
        self.setAccessibleDescription(text)

    def text(self):
        return self._text

    def display(self, result, settings):
        for key, field in self.values.items():
            field.setText(money(result[key]))
        self.hours.setText(f"Charge estimée · {duration_text(result['hours'])}")
        vat = f"{settings.get('vat_rate') or '—'} %" if settings.get("vat_enabled") else "non appliquée"
        self.note.setText(f"Taux de cette version : {settings.get('hourly_rate') or '—'} €/h HT   ·   TVA {vat}   ·   Prélèvements {settings.get('levy_rate') or '—'} %")
        issues = result["incomplete"]
        self.warning.setVisible(bool(issues))
        message = "Totaux partiels · " + " • ".join(issues[:3]) if issues else ""
        if len(issues) > 3:
            message += f" • + {len(issues) - 3} autre(s) point(s)"
        self.warning.setText(message)


class AddWorkButton(QPushButton):
    """Draw the plus geometrically, independently of font baseline metrics."""
    def __init__(self):
        super().__init__()
        self.setAccessibleName("Nouvel ouvrage")

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = "#a1adbf" if not self.isEnabled() else "#175ac0" if self.underMouse() else "#3e5574"
        painter.setPen(QPen(QColor(color), 1.5, Qt.SolidLine, Qt.RoundCap))
        x, y = (self.width() - 1) / 2, (self.height() - 1) / 2
        painter.drawLine(QPointF(x - 5, y), QPointF(x + 5, y))
        painter.drawLine(QPointF(x, y - 5), QPointF(x, y + 5))
        painter.end()


class CalculationComboBox(QComboBox):
    """Keep a visible dropdown affordance with the borderless Qt stylesheet."""
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#708098"), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        x, y = self.width() - 16, self.height() / 2
        painter.drawPolyline([QPointF(x - 4, y - 2), QPointF(x, y + 2), QPointF(x + 4, y - 2)])
        painter.end()


class ItemDialog(QDialog):
    def __init__(self, item=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Poste de chiffrage")
        self.setMinimumWidth(560)
        self.original = copy.deepcopy(item or {})
        form = QFormLayout(self)
        self.form = form
        form.setContentsMargins(20, 20, 20, 18)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(12)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.label = QLineEdit(self.original.get("label", ""))
        self.mode = CalculationComboBox()
        self.mode.addItem("Temps × quantité × taux horaire", "hourly")
        self.mode.addItem("Prix forfaitaire × quantité", "fixed")
        self.mode.setCurrentIndex(1 if self.original.get("mode") == "fixed" else 0)
        form.addRow("Désignation", self.label)
        form.addRow("Calcul", self.mode)
        self.fields = {}
        for key, title, placeholder in [
            ("quantity", "Quantité", "1"),
            ("rate", "Taux horaire HT (€)", "Vide : taux de l’affaire"),
            ("price", "Prix unitaire HT (€)", "À renseigner"),
        ]:
            field = QLineEdit(str(self.original.get(key) or ("1" if key == "quantity" else "")))
            field.setPlaceholderText(placeholder)
            form.addRow(title, field)
            self.fields[key] = field
        self.fields["hours"] = DurationInput(self.original.get("hours"), self.original.get("duration_minutes"))
        self.fields["estimated_hours"] = DurationInput(self.original.get("estimated_hours"), self.original.get("estimated_minutes"))
        form.insertRow(3, "Durée par unité", self.fields["hours"])
        form.addRow("Charge totale du forfait", self.fields["estimated_hours"])
        self.hint = label("", "subtle")
        self.hint.setWordWrap(True)
        form.addRow(self.hint)
        self.mode.currentIndexChanged.connect(self.update_mode)
        self.update_mode()
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Valider le poste")
        buttons.button(QDialogButtonBox.Save).setProperty("role", "primary")
        buttons.button(QDialogButtonBox.Cancel).setText("Annuler")
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.adjustSize()

    def update_mode(self):
        hourly = self.mode.currentData() == "hourly"
        for key in ("hours", "rate"):
            self.fields[key].setEnabled(hourly)
            self.form.setRowVisible(self.fields[key], hourly)
        for key in ("price", "estimated_hours"):
            self.fields[key].setEnabled(not hourly)
            self.form.setRowVisible(self.fields[key], not hourly)
        self.hint.setText("La durée est saisie en heures et minutes, pour chaque unité.\nLaisser le taux vide pour utiliser celui de l’affaire." if hourly else
                          "Le prix s’applique à chaque unité.\nLa charge totale est facultative et ne modifie pas le prix.")
        if self.isVisible():
            self.adjustSize()

    def value(self):
        data = copy.deepcopy(self.original)
        data.update({"id": self.original.get("id", uid()), "label": self.label.text().strip(),
                     "mode": self.mode.currentData()})
        data.update({key: self.fields[key].text().strip().replace(",", ".") or None
                     for key in ("quantity", "rate", "price")})
        data.update(self.fields["hours"].values("hours", "duration_minutes"))
        data.update(self.fields["estimated_hours"].values("estimated_hours", "estimated_minutes"))
        return data

    def validate(self):
        data = self.value()
        if not data["label"]:
            QMessageBox.warning(self, "Désignation manquante", "Indiquez une désignation pour ce poste.")
            return
        try:
            calculate({"settings": {"hourly_rate": "1", "levy_rate": "0", "vat_enabled": False,
                                    "vat_rate": "20"},
                       "works": [{"id": uid(), "name": "Contrôle", "items": [dict(data, parent_id=None)]}]})
        except DomainError as exc:
            QMessageBox.warning(self, "Valeur à corriger", str(exc))
            return
        self.accept()


class TemplateDialog(QDialog):
    def __init__(self, template=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Gabarit de postes")
        self.resize(620, 470)
        self.data = copy.deepcopy(template or {"items": []})
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(self.data.get("name", ""))
        self.description = QLineEdit(self.data.get("description", ""))
        form.addRow("Nom", self.name)
        form.addRow("Description", self.description)
        layout.addLayout(form)
        self.items = QListWidget()
        layout.addWidget(self.items)
        row = QHBoxLayout()
        button("Ajouter un poste", self.add_item, row)
        button("Modifier", self.edit_item, row)
        button("Retirer", self.remove_item, row)
        layout.addLayout(row)
        self.items.itemDoubleClicked.connect(lambda _: self.edit_item())
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Enregistrer le gabarit")
        buttons.button(QDialogButtonBox.Cancel).setText("Annuler")
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self):
        self.items.clear()
        by_id = {item["id"]: item for item in self.data["items"]}
        for item in self.data["items"]:
            depth, parent = 0, item.get("parent_id")
            seen = set()
            while parent in by_id and parent not in seen:
                seen.add(parent)
                depth += 1
                parent = by_id[parent].get("parent_id")
            prefix = "    " * depth + ("↳ " if depth else "")
            self.items.addItem(f"{prefix}{item['label']} — {'Horaire' if item['mode'] == 'hourly' else 'Forfait'}")

    def add_item(self):
        dialog = ItemDialog(parent=self)
        if dialog.exec():
            self.data["items"].append(dialog.value())
            self.refresh()

    def edit_item(self):
        index = self.items.currentRow()
        if index < 0:
            return
        dialog = ItemDialog(self.data["items"][index], self)
        if dialog.exec():
            self.data["items"][index] = dialog.value()
            self.refresh()

    def remove_item(self):
        index = self.items.currentRow()
        if index >= 0:
            removed = descendant_ids(self.data["items"], self.data["items"][index]["id"])
            if len(removed) > 1 and QMessageBox.question(self, "Retirer", f"Retirer ce poste et ses {len(removed) - 1} sous-poste(s) ?") != QMessageBox.Yes:
                return
            self.data["items"] = [item for item in self.data["items"] if item["id"] not in removed]
            self.refresh()

    def validate(self):
        if not self.name.text().strip():
            QMessageBox.warning(self, "Nom manquant", "Indiquez un nom pour ce gabarit.")
            return
        self.data.update(name=self.name.text().strip(), description=self.description.text().strip())
        self.accept()


class ConnectionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connexion aux données")
        self.setMinimumWidth(560)
        self.setStyleSheet(LIGHT_THEME)
        try:
            config = load_connection()
        except DomainError:
            config = {"mode": "server"}
        form = QFormLayout(self)
        self.mode = CalculationComboBox()
        self.mode.addItem("Sur cet ordinateur", "local")
        self.mode.addItem("Serveur partagé", "server")
        self.mode.setCurrentIndex(1 if config["mode"] == "server" else 0)
        form.addRow("Données", self.mode)
        self.url = QLineEdit(config.get("url", ""))
        self.url.setPlaceholderText("https://adresse-du-serveur:8765")
        self.token = QLineEdit(config.get("token", ""))
        self.token.setEchoMode(QLineEdit.Password)
        self.ca_file = QLineEdit(config.get("ca_file", ""))
        form.addRow("Adresse HTTPS", self.url)
        form.addRow("Clé d’accès", self.token)
        certificate = QHBoxLayout()
        certificate.addWidget(self.ca_file)
        button("Choisir…", self.choose_certificate, certificate)
        form.addRow("Certificat du serveur", certificate)
        self.result_label = QLabel("Le changement s’appliquera au prochain lancement.\nAucune affaire n’est copiée entre la base locale et le serveur.")
        self.result_label.setWordWrap(True)
        form.addRow(self.result_label)
        test = QPushButton("Tester la connexion")
        test.clicked.connect(self.test_connection)
        form.addRow(test)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Enregistrer la connexion")
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def choose_certificate(self):
        path, _ = QFileDialog.getOpenFileName(self, "Certificat public du serveur", "", "Certificat (*.pem *.crt)")
        if path:
            self.ca_file.setText(path)

    def value(self):
        return dict(mode=self.mode.currentData(), url=self.url.text().strip(),
                    token=self.token.text().strip(), ca_file=self.ca_file.text().strip())

    def test_connection(self):
        try:
            if self.mode.currentData() == "server":
                remote = RemoteStore(self.value())
                try:
                    remote.health()
                finally:
                    remote.close()
            self.result_label.setText("Connexion réussie." if self.mode.currentData() == "server" else "Mode local sélectionné.")
            return True
        except (DomainError, OSError, ValueError) as exc:
            self.result_label.setText(str(exc))
            return False

    def validate(self):
        if not self.test_connection():
            return
        try:
            save_connection(self.value())
            self.accept()
        except (DomainError, OSError, ValueError) as exc:
            self.result_label.setText(str(exc))


class SyncWorker(QThread):
    """Run network operations without blocking the Qt event loop."""

    def __init__(self, store, resolve=False, parent=None):
        super().__init__(parent)
        self.store = store
        self.resolve = resolve
        self.error = None
        self.backup_path = None

    def run(self):
        try:
            if self.resolve:
                self.backup_path = self.store.resolve_conflict_keep_both()
            self.store.synchronize()
        except Exception as exc:
            self.error = exc


class MainWindow(QMainWindow):
    def __init__(self, store=None):
        super().__init__()
        self.store = store if store is not None else open_store()
        self.sync_enabled = callable(getattr(self.store, "synchronize", None))
        self.sync_worker = None
        self.close_after_sync = False
        self.closing = False
        self.current = None
        self.dirty = False
        self.settings_dirty = False
        self.loading = False
        self.active_work_id = None
        self.setWindowTitle("JHR Chiffrage")
        self.setWindowIcon(QIcon(str(Path(__file__).parent / "assets/chiffrage.png")))
        self.resize(1360, 900)
        self.setStyleSheet(LIGHT_THEME)
        self.setFont(QFont("Segoe UI", 10))
        shell = QWidget()
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        header = QWidget()
        header.setObjectName("appHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(25, 8, 25, 8)
        app_icon = QLabel()
        app_icon.setPixmap(self.windowIcon().pixmap(32, 32))
        app_icon.setAccessibleName("Icône Ch — JHR Chiffrage")
        header_layout.addWidget(app_icon)
        header_layout.addWidget(label("JHR", "brand"))
        header_layout.addWidget(label("CHIFFRAGE  /  ÉTUDES & DESSIN", "brandSubtitle"))
        header_layout.addStretch()
        header_layout.addWidget(label("Votre espace de chiffrage", "subtle"))
        self.version_label = label(f"v{__version__}", "subtle")
        self.version_label.setToolTip("Version du logiciel JHR Chiffrage")
        self.version_label.setAccessibleName(f"Version du logiciel {__version__}")
        header_layout.addSpacing(12)
        header_layout.addWidget(self.version_label)
        shell_layout.addWidget(header)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("mainNavigation")
        shell_layout.addWidget(self.tabs)
        self.setCentralWidget(shell)
        self.build_estimates()
        self.build_templates()
        self.build_settings()
        self.build_history()
        self.refresh_lists()
        self.load_settings()
        self.render()
        self.timer = QTimer(self)
        self.timer.setInterval(3000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        if self.sync_enabled:
            self.sync_status = QLabel()
            self.sync_status.setWordWrap(True)
            self.sync_status.setToolTip("Les modifications enregistrées sont conservées sur ce PC puis synchronisées lorsque le serveur est accessible.")
            self.statusBar().addPermanentWidget(self.sync_status, 1)
            self.resolve_button = QPushButton("Conserver les deux versions")
            self.resolve_button.clicked.connect(self.resolve_sync_conflict)
            self.statusBar().addPermanentWidget(self.resolve_button)
            self.sync_button = QPushButton("Synchroniser")
            self.sync_button.setToolTip("Synchroniser les modifications déjà enregistrées.")
            self.sync_button.clicked.connect(lambda: self.start_sync(manual=True))
            self.statusBar().addPermanentWidget(self.sync_button)
            self.sync_timer = QTimer(self)
            self.sync_timer.setInterval(30000)
            self.sync_timer.timeout.connect(self.start_sync)
            self.sync_timer.start()
            self.update_sync_status()
            QTimer.singleShot(0, self.start_sync)
        else:
            self.statusBar().showMessage("  Données sur le serveur partagé" if isinstance(self.store, RemoteStore) else "  Données enregistrées sur cet ordinateur  ·  JHR Chiffrage")
        self.new_work_shortcut = QShortcut(QKeySequence("Ctrl+T"), self)
        self.new_work_shortcut.activated.connect(lambda: self.add_work() if self.tabs.currentIndex() == 0 else None)
        self.next_work_shortcut = QShortcut(QKeySequence("Ctrl+Tab"), self)
        self.next_work_shortcut.activated.connect(lambda: self.cycle_work(1))
        self.previous_work_shortcut = QShortcut(QKeySequence("Ctrl+Shift+Tab"), self)
        self.previous_work_shortcut.activated.connect(lambda: self.cycle_work(-1))

    def error(self, exc):
        QMessageBox.warning(self, "Action non effectuée", str(exc))

    def update_sync_status(self):
        if not self.sync_enabled:
            return
        busy = self.sync_worker is not None
        text = "Synchronisation en cours…" if busy else self.store.status_text
        self.sync_status.setText(text)
        self.sync_status.setStyleSheet("color: #a65a00;" if self.store.has_conflict else "")
        self.sync_button.setEnabled(not busy)
        self.resolve_button.setVisible(self.store.has_conflict)
        self.resolve_button.setEnabled(not busy)

    def start_sync(self, manual=False, resolve=False):
        if not self.sync_enabled or self.sync_worker is not None or self.close_after_sync or self.closing:
            return
        if self.dirty or self.settings_dirty:
            if manual:
                QMessageBox.information(self, "Modifications non enregistrées", "Enregistrez vos modifications avant de synchroniser.")
            return
        if QApplication.activeModalWidget() and not manual:
            return
        self.sync_worker = SyncWorker(self.store, resolve=resolve, parent=self)
        self.sync_worker.finished.connect(lambda: self.finish_sync(manual))
        # The remote snapshot may replace objects at an equal revision. Prevent
        # editing that snapshot until the brief network operation is complete.
        self.tabs.setEnabled(False)
        self.update_sync_status()
        self.sync_worker.start()

    def finish_sync(self, manual=False):
        worker = self.sync_worker
        self.sync_worker = None
        self.tabs.setEnabled(True)
        # Avoid interrupting train journeys with frequent unreachable requests.
        self.sync_timer.setInterval(30000 if getattr(self.store, "online", True) else 120000)
        self.update_sync_status()
        # poll deliberately preserves unsaved estimate and settings fields.
        self.poll()
        if worker.backup_path:
            self.statusBar().showMessage(f"Versions conservées. Sauvegarde locale : {worker.backup_path}", 15000)
        if worker.error and getattr(worker.error, "code", None) != "SYNC_CONFLICT":
            if manual and not self.close_after_sync:
                self.error(worker.error)
            else:
                self.sync_status.setText(f"Synchronisation interrompue : {worker.error}")
        worker.deleteLater()
        if self.close_after_sync:
            self.close()

    def resolve_sync_conflict(self):
        if self.dirty or self.settings_dirty:
            QMessageBox.information(self, "Modifications non enregistrées", "Enregistrez vos modifications avant de conserver les deux versions.")
            return
        answer = QMessageBox.question(
            self, "Conserver les deux versions",
            "Les affaires et gabarits modifiés sur ce PC seront conservés en copies. "
            "Les paramètres du serveur seront repris ; une sauvegarde locale complète sera conservée.\n\nContinuer ?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.start_sync(manual=True, resolve=True)

    def build_estimates(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(18, 12, 18, 14)
        layout.setSpacing(18)
        left = QWidget()
        left.setObjectName("sidebar")
        left.setFixedWidth(235)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(14, 20, 14, 16)
        left_layout.setSpacing(13)
        left_layout.addWidget(label("Mes affaires", "sectionTitle"))
        create = button("+  Nouvelle affaire", self.new_estimate, left_layout)
        create.setProperty("role", "primary")
        self.estimates = QListWidget()
        self.estimates.setWordWrap(True)
        self.estimates.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.estimates.currentItemChanged.connect(self.select_estimate)
        left_layout.addWidget(self.estimates, 1)
        left_layout.addWidget(label("ÉTUDES  ·  PLANS EXE  ·  FAB", "eyebrow"))
        layout.addWidget(left)
        right = QWidget()
        content = QVBoxLayout(right)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(8)
        heading = QHBoxLayout()
        heading.addWidget(label("Chiffrage de l’affaire", "pageTitle"))
        heading.addStretch()
        self.save_button = button("Enregistrer", self.save_current, heading)
        self.save_button.setProperty("role", "primary")
        self.export_button = QToolButton()
        self.export_button.setText("Exporter")
        self.export_button.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.export_button)
        menu.addAction("Synthèse commerciale (JSON)", lambda: self.export("commercial"))
        menu.addAction("Détail interne (JSON)", lambda: self.export("internal"))
        self.export_button.setMenu(menu)
        heading.addWidget(self.export_button)
        content.addLayout(heading)
        self.state_label = label("Sélectionnez ou créez une affaire.", "state")
        self.state_label.setWordWrap(True)
        content.addWidget(self.state_label)
        self.empty_panel = QWidget()
        empty = QVBoxLayout(self.empty_panel)
        empty.addStretch()
        welcome = label("Un nouvel ouvrage, un chiffrage clair.", "emptyTitle")
        welcome.setAlignment(Qt.AlignCenter)
        empty.addWidget(welcome)
        hint = label("Créez votre affaire, puis ajoutez vos ouvrages dans les onglets.", "subtle")
        hint.setAlignment(Qt.AlignCenter)
        empty.addWidget(hint)
        empty_actions = QHBoxLayout()
        empty_actions.addStretch()
        welcome_button = button("+  Créer ma première affaire", self.new_estimate, empty_actions)
        welcome_button.setProperty("role", "primary")
        empty_actions.addStretch()
        empty.addLayout(empty_actions)
        empty.addStretch()
        content.addWidget(self.empty_panel, 1)
        self.editor = QWidget()
        edit_layout = QVBoxLayout(self.editor)
        edit_layout.setContentsMargins(0, 0, 0, 0)
        edit_layout.setSpacing(6)
        self.metadata = {}
        for key in ("name", "client", "reference", "commercial_title", "commercial_description"):
            field = QLineEdit()
            field.textEdited.connect(self.mark_dirty)
            field.textChanged.connect(field.setToolTip)
            self.metadata[key] = field
        self.metadata["name"].setObjectName("estimateName")
        self.metadata["name"].setPlaceholderText("Nom de l’affaire")
        edit_layout.addWidget(self.metadata["name"])
        identity = QHBoxLayout()
        identity.addWidget(label("Client", "subtle"))
        self.metadata["client"].setPlaceholderText("Nom du client")
        identity.addWidget(self.metadata["client"], 1)
        identity.addWidget(label("Référence", "subtle"))
        self.metadata["reference"].setPlaceholderText("Référence de l’affaire")
        identity.addWidget(self.metadata["reference"], 1)
        self.details_toggle = QPushButton("Informations du devis")
        self.details_toggle.setCheckable(True)
        self.details_toggle.setProperty("role", "quiet")
        identity.addWidget(self.details_toggle)
        edit_layout.addLayout(identity)
        self.commercial_panel = QWidget()
        details = QFormLayout(self.commercial_panel)
        details.setContentsMargins(0, 2, 0, 2)
        details.addRow("Titre commercial", self.metadata["commercial_title"])
        details.addRow("Désignation", self.metadata["commercial_description"])
        self.commercial_panel.hide()
        self.details_toggle.toggled.connect(self.commercial_panel.setVisible)
        self.details_toggle.toggled.connect(lambda checked: self.details_toggle.setText("Masquer les informations" if checked else "Informations du devis"))
        edit_layout.addWidget(self.commercial_panel)
        browser = QVBoxLayout()
        browser.setSpacing(0)
        work_tabs_row = QHBoxLayout()
        work_tabs_row.setSpacing(4)
        self.work_tabs = QTabBar()
        self.work_tabs.setObjectName("workTabs")
        self.work_tabs.setDocumentMode(True)
        self.work_tabs.setDrawBase(False)
        self.work_tabs.setExpanding(False)
        self.work_tabs.setUsesScrollButtons(True)
        self.work_tabs.setMovable(True)
        self.work_tabs.setElideMode(Qt.ElideRight)
        self.work_tabs.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.work_tabs.currentChanged.connect(self.change_work_tab)
        self.work_tabs.tabBarDoubleClicked.connect(self.rename_work)
        self.work_tabs.tabMoved.connect(self.move_work_tab)
        work_tabs_row.addWidget(self.work_tabs)
        self.add_work_button = AddWorkButton()
        self.add_work_button.clicked.connect(self.add_work)
        work_tabs_row.addWidget(self.add_work_button)
        self.add_work_button.setFixedSize(32, 32)
        self.add_work_button.setStyleSheet("QPushButton { border: none; border-radius: 16px; background: transparent; font-size: 23px; padding: 0; } QPushButton:hover { background: #dce6f4; }")
        self.add_work_button.setToolTip("Nouvel ouvrage (Ctrl+T)")
        work_tabs_row.addStretch(1)
        browser.addLayout(work_tabs_row)
        work_panel = QFrame()
        work_panel.setObjectName("workPanel")
        panel = QVBoxLayout(work_panel)
        panel.setContentsMargins(14, 8, 14, 6)
        panel.setSpacing(6)
        toolbar = QHBoxLayout()
        self.work_summary = label("Ajoutez un ouvrage avec le bouton +.", "workSummary")
        self.work_summary.setWordWrap(True)
        toolbar.addWidget(self.work_summary, 1)
        post_button = button("+  Poste", self.add_item, toolbar)
        post_button.setProperty("role", "primary")
        template_button = button("Depuis un gabarit", self.apply_template, toolbar)
        panel.addLayout(toolbar)
        self.tree = PostTree()
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(38)
        self.tree.addChildRequested.connect(self.add_sub_item)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setHeaderLabels(["DÉSIGNATION", "CALCUL", "QTÉ", "DURÉE", "TAUX / PRIX HT", "TOTAL HT", ""])
        self.tree.setColumnWidth(6, 110)
        self.tree.setColumnWidth(0, 300)
        self.tree.setColumnWidth(1, 80)
        self.tree.setColumnWidth(2, 55)
        self.tree.setColumnWidth(3, 115)
        self.tree.setColumnWidth(4, 120)
        self.tree.setColumnWidth(5, 115)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (2, 3, 4, 5):
            self.tree.headerItem().setTextAlignment(column, Qt.AlignRight | Qt.AlignVCenter)
        self.tree.itemDoubleClicked.connect(lambda *_: self.edit_selected())
        panel.addWidget(self.tree, 1)
        row = QHBoxLayout()
        edit_button = button("Modifier le poste", self.edit_selected, row)
        edit_button.setProperty("role", "quiet")
        remove_button = button("Retirer le poste", self.remove_selected, row)
        remove_button.setProperty("role", "quiet")
        row.addStretch()
        remove_work = button("Retirer cet ouvrage", self.remove_work, row)
        remove_work.setProperty("role", "danger")
        self.work_actions = [post_button, template_button, edit_button, remove_button, remove_work]
        panel.addLayout(row)
        browser.addWidget(work_panel, 1)
        edit_layout.addLayout(browser, 1)
        content.addWidget(self.editor, 1)
        self.totals = SummaryPanel()
        content.addWidget(self.totals)
        self.footer = QWidget()
        actions = QHBoxLayout(self.footer)
        actions.setContentsMargins(0, 0, 0, 0)
        reload_button = button("Recharger", self.reload_current, actions)
        reload_button.setProperty("role", "quiet")
        self.rates_button = button("Actualiser les taux", self.refresh_rates, actions)
        self.rates_button.setToolTip("Appliquer explicitement les paramètres actuels à ce brouillon")
        self.rates_button.setProperty("role", "quiet")
        actions.addStretch()
        self.freeze_button = button("Figer la version", self.freeze, actions)
        self.revise_button = button("Créer une révision", self.revise, actions)
        content.addWidget(self.footer)
        layout.addWidget(right, 1)
        self.tabs.addTab(page, "Affaires")

    def build_templates(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Les gabarits sont copiés dans l’affaire. Leur modification ne change pas les affaires existantes."))
        self.templates = QListWidget()
        layout.addWidget(self.templates)
        self.templates.itemDoubleClicked.connect(lambda _: self.edit_template())
        row = QHBoxLayout()
        button("Créer un gabarit", lambda: self.edit_template(new=True), row)
        button("Modifier", self.edit_template, row)
        button("Dupliquer", lambda: self.edit_template(duplicate=True), row)
        layout.addLayout(row)
        self.tabs.addTab(page, "Gabarits")

    def build_settings(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel("Ces paramètres s’appliquent aux nouvelles affaires. Chaque affaire conserve ses paramètres de création.\n"
                      "Les prélèvements sont une estimation au taux global indiqué. Le solde n’est pas un bénéfice net.")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.setting_fields = {}
        for key, label in [("company", "Entreprise"), ("hourly_rate", "Taux horaire HT (€)"),
                           ("vat_rate", "TVA (%)"), ("levy_rate", "Prélèvements estimés (%)")]:
            field = QLineEdit()
            if key in ("hourly_rate", "levy_rate"):
                field.setPlaceholderText("À renseigner explicitement")
            field.textEdited.connect(self.settings_changed)
            form.addRow(label, field)
            self.setting_fields[key] = field
        self.vat_enabled = QCheckBox("Appliquer la TVA")
        self.vat_enabled.clicked.connect(self.settings_changed)
        form.addRow(self.vat_enabled)
        layout.addLayout(form)
        button("Enregistrer les paramètres", self.save_settings, layout)
        button("Créer une sauvegarde des données", self.backup, layout)
        connection_label = QLabel("Connexion actuelle : serveur avec copie hors ligne sur ce PC" if self.sync_enabled else "Connexion actuelle : serveur partagé" if isinstance(self.store, RemoteStore) else "Connexion actuelle : cet ordinateur")
        layout.addWidget(connection_label)
        button("Configurer la connexion…", self.configure_connection, layout)
        limits = QLabel("Premier jet : exports JSON, sans PDF, encaissements, TVA par poste ni détail des différents prélèvements.")
        limits.setWordWrap(True)
        layout.addWidget(limits)
        layout.addStretch()
        self.tabs.addTab(page, "Paramètres")

    def build_history(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Dernières opérations enregistrées, de la plus récente à la plus ancienne."))
        self.history = QTreeWidget()
        self.history.setHeaderLabels(["Date et heure", "Auteur", "Opération"])
        self.history.setColumnWidth(0, 220)
        self.history.setColumnWidth(1, 160)
        layout.addWidget(self.history)
        self.tabs.addTab(page, "Historique")

    def refresh_history(self):
        labels = {"save_settings": "Paramètres enregistrés", "create_estimate": "Affaire créée",
                  "save_estimate": "Affaire enregistrée", "freeze_estimate": "Version figée",
                  "revise_estimate": "Nouvelle version créée", "refresh_estimate_settings": "Paramètres appliqués au brouillon",
                  "save_template": "Gabarit enregistré", "apply_template": "Gabarit ajouté à une affaire"}
        self.history.clear()
        for event in self.store.list_changes():
            timestamp = event.get("at", "")
            try:
                timestamp = datetime.fromisoformat(timestamp).astimezone().strftime("%d/%m/%Y %H:%M:%S")
            except (ValueError, TypeError):
                pass
            self.history.addTopLevelItem(QTreeWidgetItem([
                timestamp, event.get("actor", ""), labels.get(event.get("operation"), event.get("operation", ""))]))

    def settings_changed(self, *_):
        self.settings_dirty = True

    def load_settings(self):
        self.settings = self.store.get_settings()
        for key, field in self.setting_fields.items():
            field.setText(str(self.settings.get(key) or ""))
        self.vat_enabled.setChecked(bool(self.settings.get("vat_enabled")))
        self.settings_dirty = False

    def save_settings(self):
        data = copy.deepcopy(self.settings)
        data.update({key: field.text().strip() for key, field in self.setting_fields.items()})
        data["vat_enabled"] = self.vat_enabled.isChecked()
        try:
            self.settings = self.store.save_settings(data, self.settings["revision"])
            self.load_settings()
            self.statusBar().showMessage("Paramètres enregistrés pour les nouvelles affaires.", 5000)
            return True
        except DomainError as exc:
            self.error(exc)
            return False

    def refresh_lists(self):
        self.refresh_history()
        estimates = self.store.list_estimates()
        templates = self.store.list_templates()
        selected = self.current["id"] if self.current else None
        self.estimates.blockSignals(True)
        self.estimates.clear()
        for estimate in estimates:
            frozen = " • Figée" if estimate["status"] == "frozen" else ""
            item = QListWidgetItem(f"{estimate.get('reference') or 'Sans référence'} — {estimate['name']}{frozen}")
            item.setData(Qt.UserRole, estimate["id"])
            self.estimates.addItem(item)
            if estimate["id"] == selected:
                self.estimates.setCurrentItem(item)
        self.estimates.blockSignals(False)
        selected_template = self.templates.currentItem()
        template_id = selected_template.data(Qt.UserRole)["id"] if selected_template else None
        self.templates.clear()
        for template in templates:
            item = QListWidgetItem(f"{template['name']} — {len(template['items'])} poste(s)")
            item.setData(Qt.UserRole, template)
            self.templates.addItem(item)
            if template["id"] == template_id:
                self.templates.setCurrentItem(item)

    def confirm_pending(self):
        if not self.dirty:
            return True
        box = QMessageBox(self)
        box.setWindowTitle("Modifications non enregistrées")
        box.setText("Enregistrer les modifications de cette affaire ?")
        save = box.addButton("Enregistrer", QMessageBox.AcceptRole)
        discard = box.addButton("Abandonner les modifications", QMessageBox.DestructiveRole)
        box.addButton("Annuler", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() == save:
            return self.save_current()
        return box.clickedButton() == discard

    def select_estimate(self, item, previous=None):
        if self.loading or not item:
            return
        target = item.data(Qt.UserRole)
        if self.current and target == self.current["id"]:
            return
        if not self.confirm_pending():
            self.estimates.blockSignals(True)
            self.estimates.setCurrentItem(previous)
            self.estimates.blockSignals(False)
            return
        try:
            self.current = self.store.get_estimate(target)
            self.dirty = False
            self.render()
        except DomainError as exc:
            self.error(exc)

    def new_estimate(self):
        if not self.confirm_pending():
            return
        settings = self.store.get_settings()
        if not settings.get("hourly_rate") or not settings.get("levy_rate"):
            self.statusBar().showMessage("Taux non renseignés : complétez Paramètres, puis appliquez-les au brouillon.", 12000)
        name, ok = QInputDialog.getText(self, "Nouvelle affaire", "Nom de l’affaire :")
        if ok and name.strip():
            try:
                self.current = self.store.create_estimate(name.strip())
                self.dirty = False
                self.refresh_lists()
                self.render()
            except DomainError as exc:
                self.error(exc)

    def mark_dirty(self, *_):
        if self.loading or not self.current or self.current["status"] == "frozen":
            return
        self.dirty = True
        self.update_totals()

    def collect(self):
        data = copy.deepcopy(self.current)
        if data:
            data.update({key: field.text().strip() for key, field in self.metadata.items()})
        return data

    def render(self):
        self.loading = True
        editable = bool(self.current and self.current["status"] == "draft")
        self.editor.setEnabled(bool(self.current))
        self.editor.setVisible(bool(self.current))
        self.empty_panel.setVisible(not bool(self.current))
        self.totals.setVisible(bool(self.current))
        self.footer.setVisible(bool(self.current))
        self.export_button.setEnabled(bool(self.current))
        for field in self.metadata.values():
            field.setReadOnly(not editable)
        self.add_work_button.setEnabled(editable)
        self.work_tabs.setMovable(editable)
        for action in self.work_actions:
            action.setEnabled(editable and bool(self.current["works"]))
        self.save_button.setEnabled(editable)
        self.freeze_button.setEnabled(editable)
        self.revise_button.setEnabled(bool(self.current and self.current["status"] == "frozen"))
        self.rates_button.setEnabled(editable)
        for key, field in self.metadata.items():
            field.setText(str(self.current.get(key) or "") if self.current else "")
            field.setCursorPosition(0)
        self.render_tree()
        self.update_totals()
        self.loading = False

    def render_tree(self, rebuild_tabs=True):
        works = (self.current or {}).get("works", [])
        selected = self.tree.currentItem()
        selected_id = selected.data(0, Qt.UserRole) if selected else None
        active_ids = [work["id"] for work in works]
        if self.active_work_id not in active_ids:
            self.active_work_id = active_ids[0] if active_ids else None
        if rebuild_tabs:
            self.work_tabs.blockSignals(True)
            while self.work_tabs.count():
                self.work_tabs.removeTab(0)
            for work in works:
                index = self.work_tabs.addTab(work["name"])
                self.work_tabs.setTabData(index, work["id"])
                self.work_tabs.setTabToolTip(index, work["name"] + " — double-clic pour renommer")
            if self.active_work_id:
                self.work_tabs.setCurrentIndex(active_ids.index(self.active_work_id))
            self.work_tabs.blockSignals(False)
        self.tree.hide_action()
        self.tree.clear()
        try:
            calculated = calculate(self.current) if self.current else {"lines": [], "works": []}
            amounts = {row["id"]: row["ht_cents"] for row in calculated["lines"] + calculated["works"]}
        except DomainError:
            amounts = {}
        work = next((work for work in works if work["id"] == self.active_work_id), None)
        self.work_summary.setText(
            f"{work['name']} • {len(work['items'])} poste(s) • Total ouvrage HT : {money(amounts.get(work['id'], 0))}"
            if work else "Ajoutez un ouvrage avec le bouton +.")
        if work:
            nodes = {}
            for item in work["items"]:
                hourly = item["mode"] == "hourly"
                child = QTreeWidgetItem([item["label"], "Horaire" if hourly else "Forfait",
                                        str(item.get("quantity") or ""),
                                        duration_text(item.get("hours" if hourly else "estimated_hours"), item.get("duration_minutes" if hourly else "estimated_minutes")),
                                        str(item.get("rate" if hourly else "price") or ("Taux affaire" if hourly else "—")),
                                        money(amounts.get(item["id"], 0))])
                child.setData(0, Qt.UserRole, item["id"])
                for column in (2, 3, 4, 5):
                    child.setTextAlignment(column, Qt.AlignRight | Qt.AlignVCenter)
                nodes[item["id"]] = child
            for item in work["items"]:
                child = nodes[item["id"]]
                parent = nodes.get(item.get("parent_id"))
                if parent is not None:
                    parent.addChild(child)
                    child.setIcon(0, branch_icon())
                    child.setForeground(0, QColor("#2764a5"))
                    heading_font = parent.font(0)
                    heading_font.setBold(True)
                    parent.setFont(0, heading_font)
                else:
                    self.tree.addTopLevelItem(child)
                    heading_font = child.font(0)
                    heading_font.setBold(True)
                    child.setFont(0, heading_font)
                if item["id"] == selected_id:
                    self.tree.setCurrentItem(child)
            self.tree.expandAll()
        editable = bool(self.current and self.current["status"] == "draft")
        self.tree.editable = editable
        for action in self.work_actions:
            action.setEnabled(editable and bool(work))

    def change_work_tab(self, index):
        if index < 0:
            return
        self.active_work_id = self.work_tabs.tabData(index)
        self.render_tree(rebuild_tabs=False)

    def cycle_work(self, direction):
        count = self.work_tabs.count()
        if self.tabs.currentIndex() == 0 and count:
            self.work_tabs.setCurrentIndex((self.work_tabs.currentIndex() + direction) % count)

    def move_work_tab(self, source, destination):
        if not self.current or self.current["status"] != "draft":
            return
        by_id = {work["id"]: work for work in self.current["works"]}
        self.current["works"] = [by_id[self.work_tabs.tabData(i)] for i in range(self.work_tabs.count())]
        self.mark_dirty()

    def rename_work(self, index):
        if index < 0 or not self.current or self.current["status"] != "draft":
            return
        work_id = self.work_tabs.tabData(index)
        work = next(work for work in self.current["works"] if work["id"] == work_id)
        name, ok = QInputDialog.getText(self, "Renommer l’ouvrage", "Nom :", text=work["name"])
        if ok and name.strip():
            work["name"] = name.strip()
            self.changed_tree()

    def remove_work(self):
        wi, _ = self.selection()
        if wi is None or self.current["status"] != "draft":
            return
        work = self.current["works"][wi]
        if QMessageBox.question(self, "Retirer l’ouvrage", f"Retirer l’ouvrage « {work['name']} » et ses {len(work['items'])} poste(s) de ce brouillon ?") != QMessageBox.Yes:
            return
        self.current["works"].pop(wi)
        remaining = self.current["works"]
        self.active_work_id = remaining[min(wi, len(remaining) - 1)]["id"] if remaining else None
        self.changed_tree()

    def update_totals(self):
        if not self.current:
            self.totals.setText("Créez une affaire pour commencer votre chiffrage.")
            self.state_label.setText("Sélectionnez ou créez une affaire.")
            return
        status = "FIGÉE — lecture seule" if self.current["status"] == "frozen" else "BROUILLON"
        self.state_label.setText(f"Version {self.current.get('version', 1)} • Révision {self.current['revision']} • {status}"
                                 + (" • MODIFICATIONS NON ENREGISTRÉES" if self.dirty else ""))
        try:
            result = calculate(self.collect())
            values = [("HT", "ht_cents"), ("TVA", "vat_cents"), ("TTC", "ttc_cents"),
                      ("Prélèvements estimés", "levy_cents"), ("Solde après prélèvements", "balance_cents")]
            text = "   |   ".join(f"{label} : {money(result[key])}" for label, key in values)
            text += f"\nCharge estimée : {duration_text(result['hours'])}"
            settings = self.current["settings"]
            vat = str(settings.get("vat_rate") or "à renseigner") + " %" if settings.get("vat_enabled") else "non appliquée"
            text += (f"\nParamètres de cette version : taux horaire {settings.get('hourly_rate') or 'à renseigner'} €/h"
                     f" • TVA {vat} • prélèvements {settings.get('levy_rate') or 'à renseigner'} %")
            if result["incomplete"]:
                messages = result["incomplete"]
                text += "\nINCOMPLET — totaux partiels : " + " • ".join(messages[:3])
                if len(messages) > 3:
                    text += f" • + {len(messages) - 3} autre(s) point(s) à compléter"
            self.totals.setText(text)
            self.totals.display(result, settings)
        except DomainError as exc:
            self.totals.setText(f"Chiffrage à corriger : {exc}")
            self.totals.warning.setText(f"Chiffrage à corriger : {exc}")
            self.totals.warning.show()

    def selection(self):
        works = (self.current or {}).get("works", [])
        wi = next((i for i, work in enumerate(works) if work["id"] == self.active_work_id), None)
        item = self.tree.currentItem()
        item_id = item.data(0, Qt.UserRole) if item else None
        ii = next((i for i, post in enumerate(works[wi]["items"]) if post["id"] == item_id), None) if wi is not None else None
        return wi, ii

    def changed_tree(self):
        self.render_tree()
        self.mark_dirty()

    def add_work(self):
        if not self.current or self.current["status"] != "draft":
            return
        name, ok = QInputDialog.getText(self, "Ajouter un ouvrage", "Nom de l’ouvrage :")
        if ok and name.strip():
            self.active_work_id = uid()
            self.current["works"].append({"id": self.active_work_id, "name": name.strip(), "items": []})
            self.changed_tree()

    def add_item(self):
        if not self.current or self.current["status"] != "draft":
            return
        wi, _ = self.selection()
        if wi is None:
            QMessageBox.information(self, "Choisir un ouvrage", "Sélectionnez d’abord l’ouvrage qui recevra le poste.")
            return
        dialog = ItemDialog(parent=self)
        if dialog.exec():
            self.current["works"][wi]["items"].append(dialog.value())
            self.changed_tree()

    def edit_selected(self):
        if not self.current or self.current["status"] != "draft":
            return
        wi, ii = self.selection()
        if wi is None:
            return
        work = self.current["works"][wi]
        if ii is None:
            return
        else:
            dialog = ItemDialog(work["items"][ii], self)
            if dialog.exec():
                work["items"][ii] = dialog.value()
                self.changed_tree()

    def add_sub_item(self, parent_id):
        if not self.current or self.current["status"] != "draft":
            return
        wi, _ = self.selection()
        if wi is None:
            return
        items = self.current["works"][wi]["items"]
        parent = next((item for item in items if item["id"] == parent_id), None)
        if parent is None:
            return
        dialog = ItemDialog(parent=self)
        dialog.setWindowTitle(f"Sous-poste — {parent['label']}")
        if dialog.exec():
            data = dialog.value()
            data["parent_id"] = parent_id
            items.append(data)
            self.changed_tree()

    def remove_selected(self):
        if not self.current or self.current["status"] != "draft":
            return
        wi, ii = self.selection()
        if wi is None or ii is None:
            return
        items = self.current["works"][wi]["items"]
        removed = descendant_ids(items, items[ii]["id"])
        message = f"Retirer ce poste et ses {len(removed) - 1} sous-poste(s) de l’affaire ?" if len(removed) > 1 else "Retirer ce poste de l’affaire ?"
        if QMessageBox.question(self, "Retirer", message) != QMessageBox.Yes:
            return
        self.current["works"][wi]["items"] = [item for item in items if item["id"] not in removed]
        self.changed_tree()

    def save_current(self):
        if not self.current:
            return True
        if self.current["status"] == "frozen":
            return not self.dirty
        try:
            self.current = self.store.save_estimate(self.collect(), self.current["revision"])
            self.dirty = False
            self.refresh_lists()
            self.render()
            self.statusBar().showMessage("Affaire enregistrée.", 4000)
            return True
        except DomainError as exc:
            self.error(exc)
            return False

    def reload_current(self):
        if not self.current or not self.confirm_pending():
            return
        try:
            self.current = self.store.get_estimate(self.current["id"])
            self.dirty = False
            self.render()
        except DomainError as exc:
            self.error(exc)

    def refresh_rates(self):
        if not self.current or self.current["status"] != "draft":
            return
        if self.dirty and not self.save_current():
            return
        try:
            self.current = self.store.refresh_estimate_settings(self.current["id"], self.current["revision"])
            self.render()
            self.refresh_lists()
            self.statusBar().showMessage("Paramètres actuels appliqués et affaire enregistrée.", 5000)
        except DomainError as exc:
            self.error(exc)

    def freeze(self):
        if not self.current or (self.dirty and not self.save_current()):
            return
        if QMessageBox.question(self, "Figer la version", "Figer cette version en lecture seule ? Vous pourrez créer une nouvelle version avec Réviser.") != QMessageBox.Yes:
            return
        try:
            self.current = self.store.freeze_estimate(self.current["id"], self.current["revision"])
            self.refresh_lists()
            self.render()
        except DomainError as exc:
            self.error(exc)

    def revise(self):
        if not self.current or self.current["status"] != "frozen" or (self.dirty and not self.save_current()):
            return
        try:
            self.current = self.store.revise_estimate(self.current["id"])
            self.dirty = False
            self.refresh_lists()
            self.render()
        except DomainError as exc:
            self.error(exc)

    def export(self, kind):
        if not self.current or (self.dirty and not self.save_current()):
            return
        try:
            path = self.store.export_estimate(self.current["id"], kind)
            QMessageBox.information(self, "Export JSON créé", f"Fichier enregistré :\n{path}")
        except (DomainError, OSError) as exc:
            self.error(exc)

    def apply_template(self):
        if not self.current or self.current["status"] != "draft":
            return
        wi, _ = self.selection()
        if wi is None:
            QMessageBox.information(self, "Choisir un ouvrage", "Sélectionnez d’abord un ouvrage.")
            return
        try:
            templates = self.store.list_templates()
        except DomainError as exc:
            self.error(exc)
            return
        if not templates:
            QMessageBox.information(self, "Aucun gabarit", "Créez un gabarit dans l’onglet Gabarits.")
            return
        names = [f"{i + 1}. {t['name']}" for i, t in enumerate(templates)]
        choice, ok = QInputDialog.getItem(self, "Ajouter un gabarit", "Gabarit :", names, 0, False)
        if not ok:
            return
        work_id = self.current["works"][wi]["id"]
        if self.dirty and not self.save_current():
            return
        try:
            self.current = self.store.apply_template(self.current["id"], templates[names.index(choice)]["id"],
                                                     work_id, self.current["revision"])
            self.render()
            self.refresh_lists()
            self.statusBar().showMessage("Gabarit ajouté et affaire enregistrée.", 5000)
        except DomainError as exc:
            self.error(exc)

    def edit_template(self, checked=False, new=False, duplicate=False):
        item = self.templates.currentItem()
        if not new and item is None:
            return
        data = None if new else copy.deepcopy(item.data(Qt.UserRole))
        if duplicate:
            data.pop("id", None)
            data.pop("revision", None)
            data["name"] += " — copie"
            data["items"] = clone_items(data["items"])
        dialog = TemplateDialog(data, self)
        if dialog.exec():
            try:
                self.store.save_template(dialog.data, (data or {}).get("revision"))
                self.refresh_lists()
            except DomainError as exc:
                self.error(exc)

    def backup(self):
        try:
            path = self.store.backup()
            QMessageBox.information(self, "Sauvegarde créée", str(path))
        except (DomainError, OSError) as exc:
            self.error(exc)

    def configure_connection(self):
        try:
            if ConnectionDialog(self).exec():
                QMessageBox.information(self, "Connexion enregistrée", "Enregistrez vos modifications, puis fermez et relancez l’application pour utiliser cette connexion.")
        except (DomainError, OSError, ValueError) as exc:
            self.error(exc)

    def poll(self):
        self.update_sync_status()
        if QApplication.activeModalWidget():
            return
        try:
            if self.current:
                latest = self.store.get_estimate(self.current["id"])
                if latest["revision"] != self.current["revision"] or (not self.dirty and latest != self.current):
                    if self.dirty:
                        self.statusBar().showMessage("L’affaire a changé ailleurs. Vos modifications sont conservées ; l’enregistrement vérifiera le conflit.")
                    else:
                        self.current = latest
                        self.render()
            if not self.settings_dirty:
                latest_settings = self.store.get_settings()
                if latest_settings["revision"] != self.settings["revision"]:
                    self.load_settings()
            self.refresh_lists()
        except (DomainError, OSError) as exc:
            self.statusBar().showMessage(f"Actualisation impossible : {exc}")

    def closeEvent(self, event):
        if not self.confirm_pending():
            event.ignore()
            return
        if self.settings_dirty:
            box = QMessageBox(self)
            box.setWindowTitle("Paramètres non enregistrés")
            box.setText("Enregistrer les paramètres avant de quitter ?")
            save = box.addButton("Enregistrer", QMessageBox.AcceptRole)
            discard = box.addButton("Abandonner", QMessageBox.DestructiveRole)
            box.addButton("Annuler", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() == save:
                if not self.save_settings():
                    event.ignore()
                    return
            elif box.clickedButton() != discard:
                event.ignore()
                return
        if self.sync_worker is not None:
            self.close_after_sync = True
            self.sync_timer.stop()
            self.setEnabled(False)
            self.statusBar().showMessage("Fin de la synchronisation avant fermeture…")
            event.ignore()
            return
        self.timer.stop()
        if self.sync_enabled:
            self.sync_timer.stop()
        self.closing = True
        event.accept()


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("JHR Chiffrage")
    app.setWindowIcon(QIcon(str(Path(__file__).parent / "assets/chiffrage.png")))
    app.setStyle("Fusion")
    while True:
        try:
            window = MainWindow()
            break
        except (DomainError, OSError, ValueError) as exc:
            QMessageBox.warning(None, "Connexion indisponible", f"Impossible d’ouvrir les données : {exc}\nVérifiez la connexion. Aucune base locale de remplacement n’a été ouverte.")
            if not ConnectionDialog().exec():
                return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
