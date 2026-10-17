"""Eigene Bausteine im Studio-Look."""
from __future__ import annotations

from PySide6.QtCore import (Property, QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt, Signal)
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen)
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QFrame, QHBoxLayout, QLabel, QListWidget,
                               QPushButton, QSizePolicy, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
                               QVBoxLayout, QWidget)

from . import core, style

ROLE_PATH = Qt.UserRole + 1      # str: voller Pfad
ROLE_STATUS = Qt.UserRole + 2    # dict | None: Ergebnis
ROLE_CURRENT = Qt.UserRole + 3   # bool: wird gerade encodiert


def gradient(rect: QRectF, diagonal: bool = False) -> QLinearGradient:
    g = QLinearGradient(rect.topLeft(), rect.bottomRight() if diagonal else rect.topRight())
    g.setColorAt(0.0, QColor(style.VIOLET))
    g.setColorAt(0.55, QColor("#4f6cf0") if not diagonal else QColor("#4f5ff0"))
    g.setColorAt(1.0, QColor(style.CYAN) if not diagonal else QColor("#2563eb"))
    return g


def field_label(text: str) -> QLabel:
    """Kleine Großbuchstaben-Überschrift wie „ZIEL-LÄNGE (SEKUNDEN)“."""
    lbl = QLabel(text.upper())
    lbl.setObjectName("fieldLabel")
    f = lbl.font()
    f.setLetterSpacing(QFont.AbsoluteSpacing, 1.2)
    lbl.setFont(f)
    return lbl


def label(text: str, object_name: str = "", wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    if object_name:
        lbl.setObjectName(object_name)
    lbl.setWordWrap(wrap)
    return lbl


class ElidedLabel(QLabel):
    """Einzeiliges Label, das zu langen Text mit „…“ kürzt."""

    def __init__(self, text: str = "", object_name: str = ""):
        super().__init__()
        if object_name:
            self.setObjectName(object_name)
        self._full = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self.setToolTip(text if text else "")
        self._elide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def _elide(self) -> None:
        self.setText(self.fontMetrics().elidedText(self._full, Qt.ElideMiddle, max(10, self.width())))


def card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(26, 24, 26, 24)
    lay.setSpacing(16)
    return frame, lay


# --------------------------------------------------------------------------- Kopfbereich

class AppIcon(QWidget):
    """Abgerundetes Verlaufsquadrat mit Balken (wie ein Pegel)."""

    def __init__(self, size: int = 66):
        super().__init__()
        self.setFixedSize(size, size)

    def paintEvent(self, _):
        p = QPainter(self)
        self.draw(p, QRectF(self.rect()))

    @staticmethod
    def draw(p: QPainter, r: QRectF) -> None:
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(r, r.width() * 0.27, r.height() * 0.27)
        p.fillPath(path, QBrush(gradient(r, diagonal=True)))
        # Balken
        heights = [0.22, 0.30, 0.42, 0.56, 0.36]
        bar_w = r.width() * 0.085
        gap = r.width() * 0.05
        total = len(heights) * bar_w + (len(heights) - 1) * gap
        x = (r.width() - total) / 2
        base = r.height() * 0.70
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("white"))
        for h in heights:
            bh = r.height() * h
            p.drawRoundedRect(QRectF(x, base - bh, bar_w, bh), bar_w / 2, bar_w / 2)
            x += bar_w + gap


class GradientTitle(QWidget):
    """Titel aus mehreren Teilen, je mit eigener Farbe; Farbe None = Violett-Cyan-Verlauf.

    Beispiel: [("NVIDIA ", style.NVIDIA_GREEN), ("Encoder ", "white"), ("Studio", None)]
    """

    def __init__(self, parts: list[tuple[str, str | None]], point_size: int = 28):
        super().__init__()
        self.parts = parts
        self.font_ = QFont(style.FONT, point_size, QFont.Bold)
        fm = QFontMetrics(self.font_)
        width = sum(fm.horizontalAdvance(text) for text, _ in parts)
        self.setFixedSize(width + 6, fm.height() + 4)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        fm = QFontMetrics(self.font_)
        x, y = 0, fm.ascent() + 1
        for text, color in self.parts:
            path = QPainterPath()
            path.addText(x, y, self.font_, text)
            if color is None:
                r = path.boundingRect()
                g = QLinearGradient(r.topLeft(), r.topRight())
                g.setColorAt(0, QColor("#b39cff"))
                g.setColorAt(1, QColor("#67e8f9"))
                p.fillPath(path, QBrush(g))
            else:
                p.fillPath(path, QColor(color))
            x += fm.horizontalAdvance(text)


class Badge(QFrame):
    """Status-Pille mit farbigem Punkt."""

    def __init__(self, text: str, accent: bool = False):
        super().__init__()
        self.setObjectName("badge")
        self.setProperty("variant", "accent" if accent else "")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 14, 6)
        lay.setSpacing(8)
        self.dot = QLabel()
        self.dot.setFixedSize(9, 9)
        self.text = label(text, "badgeText")
        lay.addWidget(self.dot)
        lay.addWidget(self.text)
        self.set_state(text, None)

    def set_state(self, text: str, ok: bool | None) -> None:
        """ok=True grün, False rot, None grau (wird geprüft)."""
        self.text.setText(text)
        color = {True: style.GREEN, False: style.RED, None: style.DIM}[ok]
        self.dot.setStyleSheet(f"background: {color}; border-radius: 4px;")


class SectionHeader(QWidget):
    """Nummeriertes Kästchen + Titel + Untertitel."""

    def __init__(self, number: str, title: str, subtitle: str):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 4)
        lay.setSpacing(16)
        num = label(number, "sectionNum")
        num.setAlignment(Qt.AlignCenter)
        num.setFixedSize(42, 42)
        lay.addWidget(num, 0, Qt.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        texts.addWidget(label(title, "sectionTitle"))
        texts.addWidget(label(subtitle, "sectionSub", wrap=True))
        lay.addLayout(texts, 1)


# --------------------------------------------------------------------------- Bedienelemente

class SegmentedControl(QFrame):
    """Zwei (oder mehr) Knöpfe, von denen genau einer aktiv ist."""
    changed = Signal(int)

    def __init__(self, options: list[str]):
        super().__init__()
        self.setObjectName("segment")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(5, 5, 5, 5)
        lay.setSpacing(4)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for i, text in enumerate(options):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.group.addButton(b, i)
            lay.addWidget(b)
        self.group.button(0).setChecked(True)
        self.group.idToggled.connect(lambda i, on: on and self.changed.emit(i))

    def index(self) -> int:
        return self.group.checkedId()

    def set_index(self, i: int) -> None:
        self.group.button(i).setChecked(True)


class ToggleSwitch(QAbstractButton):
    """Animierter Ein/Aus-Schalter mit Farbverlauf."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(48, 26)
        self._pos = 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def _get_knob(self) -> float:
        return self._pos

    def _set_knob(self, v: float) -> None:
        self._pos = v
        self.update()

    knob = Property(float, _get_knob, _set_knob)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def setChecked(self, on: bool) -> None:  # ohne Animation beim Laden der Einstellungen
        super().setChecked(on)
        self._anim.stop()
        self._set_knob(1.0 if on else 0.0)

    def sizeHint(self) -> QSize:
        return QSize(48, 26)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.4)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        track = QPainterPath()
        track.addRoundedRect(r, r.height() / 2, r.height() / 2)
        p.fillPath(track, QColor("#2a2a40"))
        if self._pos > 0:
            p.save()
            p.setOpacity(p.opacity() * self._pos)
            p.fillPath(track, QBrush(gradient(r)))
            p.restore()
        d = r.height() - 6
        x = r.left() + 3 + self._pos * (r.width() - d - 6)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))


class ToggleRow(QWidget):
    """Schalter + Titel + Erklärung; ein Klick auf die Zeile schaltet um."""

    def __init__(self, title: str, hint: str, extra: QWidget | None = None):
        super().__init__()
        self.switch = ToggleSwitch()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(14)
        lay.addWidget(self.switch, 0, Qt.AlignVCenter)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        self.title = label(title)
        self.title.setStyleSheet("font-size: 15px; font-weight: 600;")
        texts.addWidget(self.title)
        texts.addWidget(label(hint, "hint", wrap=extra is None))
        if extra is None:
            lay.addLayout(texts, 1)
        else:  # Zusatzfeld (z. B. Startzeit) direkt hinter dem Text
            lay.addLayout(texts)
            lay.addWidget(extra, 0, Qt.AlignVCenter)
            lay.addStretch(1)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e):
        if self.switch.isEnabled() and e.button() == Qt.LeftButton:
            self.switch.toggle()
        super().mousePressEvent(e)

    def isChecked(self) -> bool:
        return self.switch.isChecked()


class Collapsible(QWidget):
    """Aufklappbarer Bereich mit Kopfzeile „⚙ Titel ◀ / ▼“."""

    def __init__(self, title: str, content: QWidget, expanded: bool = False):
        super().__init__()
        self.content = content
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.header = QPushButton()
        self.header.setObjectName("collapseHeader")
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.setMinimumHeight(40)
        self.header.clicked.connect(lambda: self.set_expanded(not self.content.isVisible()))
        row = QHBoxLayout(self.header)
        row.setContentsMargins(4, 0, 8, 0)
        title_lbl = label(f"⚙   {title}")
        title_lbl.setStyleSheet("font-size: 15px; font-weight: 600; background: transparent;")
        self.arrow = label("")
        self.arrow.setStyleSheet("font-size: 13px; background: transparent;")
        for w in (title_lbl, self.arrow):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(title_lbl)
        row.addStretch(1)
        row.addWidget(self.arrow)
        lay.addWidget(self.header)
        lay.addWidget(content)
        self.set_expanded(expanded)

    def set_expanded(self, on: bool) -> None:
        self.content.setVisible(on)
        self.arrow.setText("▼" if on else "◀")


# --------------------------------------------------------------------------- Dateiliste

class FileListDelegate(QStyledItemDelegate):
    """Zeile: Status | Dateiname + Ordner (gedimmt) | Ergebnis rechts."""
    ROW_H = 38

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), self.ROW_H)

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index) -> None:
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect).adjusted(2, 2, -2, -2)
        status: dict | None = index.data(ROLE_STATUS)
        current: bool = bool(index.data(ROLE_CURRENT))

        # Hintergrund
        if option.state & QStyle.State_Selected:
            p.setPen(QPen(QColor("#3a3070"), 1))
            p.setBrush(QColor("#1e1b36"))
            p.drawRoundedRect(r, 10, 10)
        elif option.state & QStyle.State_MouseOver:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#151524"))
            p.drawRoundedRect(r, 10, 10)

        base = QFont(style.FONT, 10)
        bold = QFont(base)
        bold.setBold(True)
        x = r.left() + 12

        # Status-Symbol
        icon, icon_color = "", None
        if current:
            icon, icon_color = "▶", style.BLUE
        elif status and status.get("status") == "ok":
            icon, icon_color = "✓", style.GREEN
        elif status and status.get("status") == "failed":
            icon, icon_color = "✗", style.RED
        if icon:
            p.setFont(bold)
            p.setPen(QColor(icon_color))
            p.drawText(QRectF(x, r.top(), 20, r.height()), Qt.AlignVCenter | Qt.AlignLeft, icon)
        x += 26

        # Ergebnis rechts (Teile: Text, Farbe, fett)
        parts: list[tuple[str, str, bool]] = []
        if status and not current:
            if status.get("status") == "ok":
                if status.get("is_sample"):
                    parts.append(("Sample  ", style.DIM, False))
                    size = status.get("full_est") or status.get("out_size", 0)
                    parts.append((("≈ " if status.get("full_est") else "") + core.format_size(size), style.TEXT, False))
                else:
                    parts.append((core.format_size(status.get("out_size", 0)), style.TEXT, False))
                pct = core.saving_percent(status.get("src_size", 0), status.get("out_size", 0))
                if pct is not None:
                    text = f"   −{pct:.0f} %" if pct >= 0 else f"   +{-pct:.0f} %"
                    parts.append((text, style.GREEN if pct >= 0 else style.ORANGE, True))
            else:
                parts.append(("fehlgeschlagen", style.RED, False))
        right_w = 0
        for text, _, is_bold in parts:
            right_w += QFontMetrics(bold if is_bold else base).horizontalAdvance(text)
        rx = r.right() - 12 - right_w
        for text, color, is_bold in parts:
            f = bold if is_bold else base
            p.setFont(f)
            p.setPen(QColor(color))
            w = QFontMetrics(f).horizontalAdvance(text)
            p.drawText(QRectF(rx, r.top(), w + 2, r.height()), Qt.AlignVCenter | Qt.AlignLeft, text)
            rx += w

        # Dateiname + Ordner, gekürzt auf den freien Platz
        avail = (r.right() - 12 - right_w - (16 if parts else 0)) - x
        path = index.data(ROLE_PATH) or ""
        name = index.data(Qt.DisplayRole) or ""
        folder = path[: max(0, len(path) - len(name) - 1)]
        fm = QFontMetrics(base)
        name_text = fm.elidedText(name, Qt.ElideMiddle, int(avail))
        p.setFont(base)
        p.setPen(QColor("#a9b8ff") if option.state & QStyle.State_Selected else QColor(style.TEXT))
        p.drawText(QRectF(x, r.top(), avail, r.height()), Qt.AlignVCenter | Qt.AlignLeft, name_text)
        used = fm.horizontalAdvance(name_text) + 14
        if avail - used > 40:
            small = QFont(style.FONT, 9)
            p.setFont(small)
            p.setPen(QColor(style.DIM))
            folder_text = QFontMetrics(small).elidedText(folder, Qt.ElideMiddle, int(avail - used))
            p.drawText(QRectF(x + used, r.top(), avail - used, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                       folder_text)
        p.restore()


class FileList(QListWidget):
    """Dateiliste mit Leer-Hinweis; Drops nimmt das Hauptfenster entgegen."""

    def __init__(self):
        super().__init__()
        self.setObjectName("fileList")
        self.setItemDelegate(FileListDelegate(self))
        self.setSelectionMode(QListWidget.ExtendedSelection)
        self.setMouseTracking(True)
        self.setUniformItemSizes(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumHeight(140)

    def set_drop_active(self, on: bool) -> None:
        if self.property("dropActive") != on:
            self.setProperty("dropActive", on)
            self.style().unpolish(self)
            self.style().polish(self)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.count():
            return
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.viewport().rect())
        # Symbol: Pfeil in einem Kreis
        c = r.center()
        circle = QRectF(c.x() - 30, c.y() - 62, 60, 60)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#1b1830"))
        p.drawEllipse(circle)
        pen = QPen(QColor("#a99cf0"), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        cx, cy = circle.center().x(), circle.center().y()
        p.drawLine(QPointF(cx, cy - 12), QPointF(cx, cy + 10))
        p.drawLine(QPointF(cx - 9, cy - 3), QPointF(cx, cy - 12))
        p.drawLine(QPointF(cx + 9, cy - 3), QPointF(cx, cy - 12))
        p.setPen(QColor(style.TEXT))
        p.setFont(QFont(style.FONT, 12, QFont.DemiBold))
        p.drawText(QRectF(r.left(), c.y() + 6, r.width(), 26), Qt.AlignCenter,
                   "MKV/MP4-Dateien oder Ordner hierher ziehen")
        p.setPen(QColor(style.DIM))
        p.setFont(QFont(style.FONT, 10))
        p.drawText(QRectF(r.left(), c.y() + 32, r.width(), 22), Qt.AlignCenter,
                   "auch aus verschiedenen Verzeichnissen gleichzeitig")
