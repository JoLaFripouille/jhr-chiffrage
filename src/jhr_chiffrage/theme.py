"""Shared light desktop theme; no web engine or remote assets."""
LIGHT_THEME = """
QMainWindow, QDialog { background: #f3f5f9; }
QWidget { color: #22334b; font-size: 13px; }
QWidget#appHeader { background: #ffffff; border-bottom: 1px solid #e2e8f0; }
QLabel#brand { color: #1760ce; font-size: 23px; font-weight: 700; }
QLabel#brandSubtitle, QLabel#subtle, QLabel#state { color: #708098; font-size: 12px; }
QTabWidget::pane { border: none; }
QTabWidget#mainNavigation > QTabBar::tab { background: transparent; color: #66768e; padding: 12px 22px; margin-right: 4px; border-bottom: 3px solid transparent; }
QTabWidget#mainNavigation > QTabBar::tab:selected { color: #185cbd; border-bottom: 3px solid #2870d9; font-weight: 600; }
QWidget#sidebar { background: #ffffff; border: 1px solid #e0e6ef; border-radius: 12px; }
QLabel#sectionTitle { color: #273c59; font-size: 16px; font-weight: 600; }
QLabel#pageTitle { font-size: 23px; font-weight: 600; color: #1e3352; }
QLabel#eyebrow { color: #7c8aa0; font-size: 11px; font-weight: 600; }
QFrame#workPanel { background: #ffffff; border: 1px solid #dce4f0; border-top-left-radius: 0px; border-top-right-radius: 10px; border-bottom-left-radius: 10px; border-bottom-right-radius: 10px; }
QWidget#workToolbar { background: #ffffff; }
QLabel#workSummary { color: #57708d; font-size: 12px; padding: 3px 0px; }
QTabBar#workTabs::tab { background: #e7ecf4; color: #60728c; border: 1px solid #dce4f0; border-bottom: 0; border-top-left-radius: 9px; border-top-right-radius: 9px; padding: 7px 16px; margin-right: 5px; }
QTabBar#workTabs::tab:selected { background: #ffffff; color: #175ac0; font-weight: 600; border-top: 3px solid #3174d9; padding-top: 5px; }
QTabBar#workTabs::tab:hover:!selected { background: #dce6f4; }
QPushButton, QToolButton { background: #ffffff; color: #3e5574; border: 1px solid #d8e1ed; border-radius: 7px; padding: 8px 13px; font-weight: 500; }
QPushButton:hover, QToolButton:hover { background: #edf3fd; border-color: #a4c0e7; color: #175ac0; }
QPushButton:pressed, QToolButton:pressed { background: #dfeafb; }
QPushButton[role="primary"] { background: #2563cf; color: #ffffff; border: 1px solid #2563cf; font-weight: 600; }
QPushButton[role="primary"]:hover { background: #1c54b8; }
QPushButton[role="quiet"] { background: transparent; border: 1px solid transparent; color: #627692; }
QPushButton[role="danger"] { background: transparent; border: 1px solid transparent; color: #96616b; }
QPushButton[role="danger"]:hover { background: #fff0f0; color: #b3434a; }
QPushButton:disabled, QToolButton:disabled { background: #eef1f5; color: #a1adbf; border-color: #e5eaf1; }
QLineEdit, QTextEdit, QComboBox, QSpinBox { background: #ffffff; color: #253851; border: 1px solid #dbe3ee; border-radius: 6px; padding: 7px 10px; selection-background-color: #c9ddff; selection-color: #163864; }
QLineEdit:focus, QTextEdit:focus, QComboBox:focus { border-color: #6b9cde; }
QComboBox { padding-right: 30px; }
QComboBox::down-arrow { image: none; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 28px; border: none; background: transparent; }
QComboBox QAbstractItemView { background: #ffffff; color: #253851; border: 1px solid #dbe3ee; selection-background-color: #e9f1ff; selection-color: #175ac0; padding: 5px; outline: none; }
QToolTip { background: #ffffff; color: #3e5574; border: 1px solid #ced9e8; padding: 6px 8px; }
QLineEdit:disabled, QSpinBox:disabled { background: #f0f3f7; color: #a1adbd; border-color: #e3e9f1; }
QLineEdit#estimateName { background: transparent; border: 1px solid transparent; color: #1d3354; font-size: 23px; font-weight: 600; padding: 2px 0px; }
QLineEdit#estimateName:focus { background: #ffffff; border-color: #92b6e6; }
QListWidget { background: #ffffff; border: 0; outline: 0; padding: 3px; }
QListWidget::item { padding: 12px 9px; border-radius: 7px; margin: 3px 0px; color: #536984; }
QListWidget::item:selected { background: #e9f1ff; color: #1859b7; }
QListWidget::item:hover:!selected { background: #f4f7fb; }
QTreeWidget { background: #ffffff; alternate-background-color: #f8fafd; border: none; outline: none; selection-background-color: #e8f1ff; selection-color: #204d91; }
QTreeWidget::item { border-bottom: 1px solid #edf1f6; padding: 10px 7px; }
QTreeWidget::item:selected { background: #e8f1ff; color: #204d91; }
QHeaderView::section { background: #f5f8fc; color: #71829a; border: none; border-bottom: 1px solid #e4ebf4; padding: 10px 7px; font-size: 11px; font-weight: 600; }
QFrame#metricCard { background: #ffffff; border: 1px solid #e1e7f0; border-radius: 9px; }
QFrame#metricPrimary { background: #e9f1ff; border: 1px solid #cdddf6; border-radius: 9px; }
QFrame#metricBalance { background: #eef7f5; border: 1px solid #d5e9e3; border-radius: 9px; }
QLabel#metricCaption { color: #78879c; font-size: 11px; }
QLabel#metricValue { color: #243d60; font-size: 21px; font-weight: 600; }
QLabel#summaryNote { color: #77869c; font-size: 11px; }
QLabel#warning { color: #996329; background: #fff6e8; border-radius: 6px; padding: 7px; font-size: 12px; }
QLabel#emptyTitle { color: #294c7d; font-size: 25px; font-weight: 600; }
QStatusBar { background: #f3f5f9; color: #8390a4; font-size: 11px; border-top: 1px solid #e1e7ef; }
QScrollBar:vertical { background: #f4f6fa; width: 9px; margin: 0; }
QScrollBar::handle:vertical { background: #c8d3e2; border-radius: 4px; min-height: 25px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QMenu { background: #ffffff; border: 1px solid #dce4ef; padding: 6px; }
QMenu::item { padding: 8px 20px; }
QMenu::item:selected { background: #eaf2ff; color: #175ac0; }
"""
