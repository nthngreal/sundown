"""Dark theme: Fusion style, a dark palette and one style sheet for the whole app."""
import os

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QStyleFactory

ASSETS = os.path.join(os.path.dirname(__file__), "assets")

BG = "#111218"
SURFACE = "#1a1b24"
SURFACE2 = "#23252f"
RAISED = "#2d303d"
BORDER = "#2e3140"
TEXT = "#ecedf3"
MUTED = "#8d91a5"
ACCENT = "#ff7a45"
ACCENT2 = "#e2457f"
OK = "#4ade80"
ERR = "#f87171"

GRADIENT = f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT2})"
GRADIENT_HOVER = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff8d5e, stop:1 #ea5b8f)"


def asset(name):
    return os.path.join(ASSETS, name).replace("\\", "/")


QSS = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-size: 10pt; }}
QDialog, QMessageBox {{ background: {BG}; }}
QLabel {{ background: transparent; }}
QLabel#title {{ font-size: 17pt; font-weight: 700; }}
QLabel#muted, QLabel#detail {{ color: {MUTED}; }}
QLabel#small {{ color: {MUTED}; font-size: 8.5pt; }}
QLabel#section {{ color: {MUTED}; font-size: 8.5pt; font-weight: 700; letter-spacing: 1px; }}
QLabel#big {{ font-size: 30pt; font-weight: 700; }}
QLabel#bigaction {{ font-size: 18pt; font-weight: 700; }}

QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame#card QWidget {{ background: transparent; }}
QFrame#hero {{ border: 1px solid {ACCENT}; border-radius: 14px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 rgba(255,122,69,40), stop:1 rgba(226,69,127,40)); }}
QFrame#hero QWidget {{ background: transparent; }}
QFrame#divider {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: none; }}
QFrame#segbar {{ background: {SURFACE2}; border-radius: 12px; }}
QFrame#segbar QWidget {{ background: transparent; }}

QPushButton {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 9px;
    padding: 7px 14px; }}
QPushButton:hover {{ background: {RAISED}; }}
QPushButton:disabled {{ color: {MUTED}; }}
QPushButton#seg {{ background: transparent; border: none; border-radius: 9px;
    padding: 8px 10px; color: {MUTED}; font-weight: 600; }}
QPushButton#seg:hover {{ color: {TEXT}; }}
QPushButton#seg:checked {{ background: {RAISED}; color: {TEXT}; }}
QPushButton#seg:disabled {{ color: #5a5e70; }}
QPushButton#seg:checked:disabled {{ color: {MUTED}; }}
QFrame#card QPushButton {{ background: {SURFACE2}; border: 1px solid {BORDER};
    border-radius: 9px; padding: 5px 12px; }}
QFrame#card QPushButton:hover {{ background: {RAISED}; }}
QFrame#card QPushButton#preset {{ border-radius: 14px; }}

QToolButton#action {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px;
    padding: 12px 6px 10px 6px; font-weight: 600; }}
QToolButton#action:hover {{ background: {SURFACE2}; }}
QToolButton#action:checked {{ border: 1.5px solid {ACCENT}; background: rgba(255,122,69,30); }}
QToolButton#action:disabled {{ color: {MUTED}; }}

QPushButton#primary {{ background: {GRADIENT}; color: white; border: none; border-radius: 12px;
    padding: 13px; font-size: 11pt; font-weight: 700; }}
QPushButton#primary:hover {{ background: {GRADIENT_HOVER}; }}
QPushButton#primary:disabled {{ background: {SURFACE2}; color: {MUTED}; }}
QPushButton#danger {{ background: {SURFACE2}; color: {ERR}; border: 1.5px solid {ERR};
    border-radius: 12px; padding: 12px; font-size: 11pt; font-weight: 700; }}
QPushButton#danger:hover {{ background: rgba(248,113,113,30); }}

QComboBox, QSpinBox, QTimeEdit, QLineEdit {{ background: {SURFACE2}; border: 1px solid {BORDER};
    border-radius: 8px; padding: 6px 10px; selection-background-color: {ACCENT}; }}
QComboBox:hover, QSpinBox:hover, QTimeEdit:hover, QLineEdit:hover {{ border-color: #444859; }}
QComboBox:focus, QSpinBox:focus, QTimeEdit:focus, QLineEdit:focus {{ border-color: {ACCENT}; }}
QComboBox:disabled, QSpinBox:disabled, QTimeEdit:disabled {{ color: {MUTED}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url({asset("chevron-down.svg")}); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {SURFACE2}; border: 1px solid {BORDER};
    selection-background-color: {RAISED}; outline: none; padding: 4px; }}
QSpinBox::up-button, QTimeEdit::up-button {{ subcontrol-origin: border;
    subcontrol-position: top right; width: 22px; border: none; background: transparent; }}
QSpinBox::down-button, QTimeEdit::down-button {{ subcontrol-origin: border;
    subcontrol-position: bottom right; width: 22px; border: none; background: transparent; }}
QSpinBox::up-arrow, QTimeEdit::up-arrow {{ image: url({asset("chevron-up.svg")});
    width: 10px; height: 10px; }}
QSpinBox::down-arrow, QTimeEdit::down-arrow {{ image: url({asset("chevron-down.svg")});
    width: 10px; height: 10px; }}
QSpinBox#bigspin, QTimeEdit#bigspin {{ font-size: 20pt; font-weight: 700; padding: 6px 12px;
    min-width: 150px; }}
QSpinBox#bigspin::up-button, QTimeEdit#bigspin::up-button,
QSpinBox#bigspin::down-button, QTimeEdit#bigspin::down-button {{ width: 30px; }}

QCheckBox {{ spacing: 10px; background: transparent; }}
QCheckBox#source {{ font-size: 11pt; font-weight: 700; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 6px;
    border: 1.5px solid #4a4e62; background: {SURFACE2}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT};
    image: url({asset("check.svg")}); }}
QCheckBox::indicator:checked:disabled {{ background: #7a4a38; border-color: #7a4a38; }}

QProgressBar {{ background: {SURFACE2}; border: none; border-radius: 3px;
    max-height: 6px; min-height: 6px; }}
QProgressBar::chunk {{ background: {GRADIENT}; border-radius: 3px; }}
QProgressBar#countdown {{ max-height: 8px; min-height: 8px; border-radius: 4px; }}
QProgressBar#countdown::chunk {{ border-radius: 4px; }}

QToolTip {{ background: {SURFACE2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px; }}
QMenu {{ background: {SURFACE2}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {RAISED}; }}
"""


def apply(app):
    app.setStyle(QStyleFactory.create("Fusion"))
    pal = QPalette()
    for role, color in ((QPalette.Window, BG), (QPalette.Base, SURFACE2),
                        (QPalette.AlternateBase, SURFACE), (QPalette.Button, SURFACE2),
                        (QPalette.WindowText, TEXT), (QPalette.Text, TEXT),
                        (QPalette.ButtonText, TEXT), (QPalette.ToolTipBase, SURFACE2),
                        (QPalette.ToolTipText, TEXT), (QPalette.Highlight, ACCENT),
                        (QPalette.HighlightedText, "#ffffff"), (QPalette.PlaceholderText, MUTED),
                        (QPalette.Link, ACCENT)):
        pal.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(MUTED))
    app.setPalette(pal)
    app.setStyleSheet(QSS)
