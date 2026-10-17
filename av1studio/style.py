"""Farben und Qt-Stylesheet im dunklen Studio-Look (Violett → Blau → Cyan)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

# --------------------------------------------------------------------------- Palette
BG_TOP = "#0b0b14"
BG_BOTTOM = "#081416"
CARD = "#12121d"
CARD_BORDER = "#22223a"
INPUT = "#08080f"
INPUT_BORDER = "#1c1c2e"
TEXT = "#ececf5"
MUTED = "#9b9bb6"
DIM = "#6b6b88"
VIOLET = "#7c3aed"
BLUE = "#3b82f6"
CYAN = "#06b6d4"
GREEN = "#34d399"
RED = "#f87171"
ORANGE = "#fb923c"
YELLOW = "#fcd34d"
NVIDIA_GREEN = "#76b900"

GRAD_H = f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {VIOLET}, stop:0.55 #4f6cf0, stop:1 {CYAN})"
GRAD_H_HOVER = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #8b4cf6, stop:0.55 #6380f5, stop:1 #22c7e2)"
GRAD_D = f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {VIOLET}, stop:1 #2563eb)"

FONT = "Segoe UI"
MONO = "Cascadia Mono, Consolas"


def _make_arrow(path: Path, color: str) -> None:
    """Kleiner Dreieck-Pfeil für Auswahlfelder."""
    img = QImage(24, 24, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    tri = QPainterPath()
    tri.moveTo(QPointF(7, 10))
    tri.lineTo(QPointF(17, 10))
    tri.lineTo(QPointF(12, 15.5))
    tri.closeSubpath()
    p.setPen(QPen(Qt.NoPen))
    p.fillPath(tri, QColor(color))
    p.end()
    img.save(str(path))


def build_stylesheet() -> str:
    assets = Path(tempfile.gettempdir()) / "av1studio_assets"
    assets.mkdir(exist_ok=True)
    arrow, arrow_dis = assets / "arrow.png", assets / "arrow_disabled.png"
    _make_arrow(arrow, TEXT)
    _make_arrow(arrow_dis, DIM)
    a, ad = arrow.as_posix(), arrow_dis.as_posix()

    return f"""
* {{ font-family: "{FONT}"; color: {TEXT}; }}
QWidget {{ font-size: 14px; }}
#root {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {BG_TOP}, stop:0.7 #0a0c16, stop:1 {BG_BOTTOM}); }}
QScrollArea, #scrollContent {{ background: transparent; border: none; }}

/* ---------- Karten ---------- */
#card {{ background: {CARD}; border: 1px solid {CARD_BORDER}; border-radius: 22px; }}
#headerCard {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1e1436, stop:0.55 #15172a, stop:1 #0e2a2d);
    border: 1px solid #2a2342; border-radius: 24px;
}}
#appSubtitle {{ color: #b9b9cf; font-size: 15px; }}
#sectionNum {{ background: {GRAD_D}; border-radius: 11px; color: white; font-family: {MONO};
               font-size: 14px; font-weight: 700; }}
#sectionTitle {{ font-size: 18px; font-weight: 700; }}
#sectionSub {{ color: {MUTED}; font-size: 13px; }}
#fieldLabel {{ color: #a7a7c8; font-size: 11px; font-weight: 700; }}
#hint {{ color: {DIM}; font-size: 12px; }}
#smallMuted {{ color: {MUTED}; font-size: 12px; }}

/* ---------- Status-Pillen ---------- */
#badge {{ background: #1b1b2a; border: 1px solid #33334c; border-radius: 15px; }}
#badge[variant="accent"] {{ background: #241e47; border: 1px solid #4a3c8a; }}
#badgeText {{ font-size: 13px; font-weight: 600; color: #e8e8f3; background: transparent; }}

/* ---------- Knöpfe ---------- */
QPushButton {{ border: none; }}
QPushButton#primary {{
    background: {GRAD_H}; color: white; border-radius: 15px;
    font-size: 18px; font-weight: 700; min-height: 58px;
}}
QPushButton#primary:hover {{ background: {GRAD_H_HOVER}; }}
QPushButton#primary:pressed {{ background: {GRAD_H}; padding-top: 2px; }}
QPushButton#primary:disabled {{ background: #23233a; color: {DIM}; }}
QPushButton#ghost {{
    background: #1b1b2a; border: 1px solid #2c2c44; border-radius: 11px;
    padding: 9px 16px; font-weight: 600; font-size: 13px;
}}
QPushButton#ghost:hover {{ background: #25253c; border-color: #3c3c5e; }}
QPushButton#ghost:disabled {{ color: {DIM}; background: #15151f; border-color: #1f1f30; }}
QPushButton#danger {{
    background: #34141f; color: {RED}; border: 1px solid #4a1d2b; border-radius: 11px;
    padding: 9px 16px; font-weight: 600; font-size: 13px;
}}
QPushButton#danger:hover {{ background: #45182a; }}
QPushButton#danger:disabled {{ background: #1a1217; color: #6a4a55; border-color: #261820; }}
QPushButton#iconButton {{
    background: #1b1b2a; border: 1px solid #2c2c44; border-radius: 12px; font-size: 18px;
}}
QPushButton#iconButton:hover {{ background: #25253c; }}
QPushButton#collapseHeader {{
    background: transparent; text-align: left; font-size: 15px; font-weight: 600; padding: 8px 4px;
}}
QPushButton#collapseHeader:hover {{ color: white; }}

/* ---------- Segment-Umschalter ---------- */
#segment {{ background: #0c0c16; border: 1px solid {CARD_BORDER}; border-radius: 16px; }}
#segment QPushButton {{
    background: transparent; border-radius: 12px; padding: 11px 20px;
    color: #d3d3e6; font-size: 15px; font-weight: 600;
}}
#segment QPushButton:hover:!checked {{ background: #17172a; }}
#segment QPushButton:checked {{ background: {GRAD_H}; color: white; }}
#segment QPushButton:disabled:!checked {{ color: {DIM}; }}
#segment QPushButton:checked:disabled {{ background: #2b2b44; color: #a0a0bf; }}

/* ---------- Eingaben ---------- */
QComboBox {{
    background: {INPUT}; border: 1px solid {INPUT_BORDER}; border-radius: 11px;
    padding: 9px 14px; font-size: 15px; min-height: 22px;
}}
QComboBox:hover {{ border-color: #36365a; }}
QComboBox:focus {{ border-color: {VIOLET}; }}
QComboBox:disabled {{ color: {DIM}; background: #0d0d16; }}
QComboBox::drop-down {{ border: none; width: 34px; }}
QComboBox::down-arrow {{ image: url("{a}"); width: 22px; height: 22px; }}
QComboBox::down-arrow:disabled {{ image: url("{ad}"); }}
QComboBox QAbstractItemView {{
    background: #14141f; border: 1px solid #2c2c44; border-radius: 10px;
    padding: 4px; outline: 0; selection-background-color: #2b2452; selection-color: white;
}}
QComboBox QAbstractItemView::item {{ min-height: 30px; padding: 2px 8px; border-radius: 6px; }}
QLineEdit, QSpinBox {{
    background: {INPUT}; border: 1px solid #262640; border-radius: 9px;
    padding: 6px 10px; font-size: 15px; selection-background-color: #4c3d8f;
}}
QLineEdit:focus, QSpinBox:focus {{ border-color: {VIOLET}; }}
QLineEdit:disabled, QSpinBox:disabled {{ color: {DIM}; background: #0d0d16; border-color: #1a1a2a; }}

/* ---------- Slider ---------- */
QSlider::groove:horizontal {{ height: 6px; background: #e3e3ee; border-radius: 3px; }}
QSlider::sub-page:horizontal {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {VIOLET}, stop:1 #8b5cf6); border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: white; width: 20px; height: 20px; margin: -7px 0; border-radius: 10px;
}}
QSlider::groove:horizontal:disabled {{ background: #2a2a3c; }}
QSlider::sub-page:horizontal:disabled {{ background: #4a4a66; }}
QSlider::handle:horizontal:disabled {{ background: #8a8aa0; }}

/* ---------- Fortschritt ---------- */
QProgressBar {{ background: #1c1c2c; border: none; border-radius: 4px; max-height: 8px; min-height: 8px; }}
QProgressBar::chunk {{ background: {GRAD_H}; border-radius: 4px; }}
QProgressBar#totalBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #10b981, stop:1 {GREEN}); border-radius: 4px;
}}

/* ---------- Liste, Status, Log ---------- */
QListView#fileList {{
    background: {INPUT}; border: 1px solid {INPUT_BORDER}; border-radius: 16px; padding: 6px; outline: 0;
}}
QListView#fileList[dropActive="true"] {{ border: 2px dashed {VIOLET}; background: #0e0b1c; }}
#statusBox {{ background: #0d0d17; border: 1px solid {CARD_BORDER}; border-radius: 18px; }}
#statusIcon {{ background: #1d1d30; border-radius: 12px; font-size: 22px; }}
#statusText {{ font-size: 14px; }}
#statTile {{ background: #0d0d17; border: 1px solid {CARD_BORDER}; border-radius: 14px; }}
#statValue {{ font-size: 20px; font-weight: 700; }}
#statLabel {{ color: {MUTED}; font-size: 11px; font-weight: 700; }}
#resultHeader {{ color: #a99cf0; font-size: 13px; }}
#fpsLabel {{ color: {YELLOW}; font-size: 12px; font-weight: 600; }}
#pctLabel {{ color: #8fb4ff; font-size: 12px; font-weight: 600; }}
#totalPct {{ color: {GREEN}; font-size: 12px; font-weight: 600; }}
QPlainTextEdit#log {{
    background: {INPUT}; border: 1px solid {INPUT_BORDER}; border-radius: 14px; padding: 8px;
    font-family: {MONO}; font-size: 12px; color: #9fe3bb; selection-background-color: #4c3d8f;
}}

/* ---------- Scrollbars & Tooltips ---------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 6px 2px; }}
QScrollBar::handle:vertical {{ background: #2c2c44; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3c3c5c; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 6px; }}
QScrollBar::handle:horizontal {{ background: #2c2c44; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolTip {{ background: #1b1b2a; color: {TEXT}; border: 1px solid #33334c; padding: 6px 8px; }}
QMessageBox {{ background: {CARD}; }}
"""
