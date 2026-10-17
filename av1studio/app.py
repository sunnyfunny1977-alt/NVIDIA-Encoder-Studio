"""Hauptfenster von NVIDIA Encoder Studio."""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, QRegularExpression, QSettings, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QRegularExpressionValidator,
                           QShortcut)
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QGraphicsDropShadowEffect,
                               QGridLayout, QHBoxLayout, QLineEdit, QListWidgetItem, QMainWindow,
                               QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSlider, QSpinBox,
                               QStackedWidget, QVBoxLayout, QWidget)

from . import core, style
from .widgets import (ROLE_CURRENT, ROLE_PATH, ROLE_STATUS, AppIcon, Badge, Collapsible, ElidedLabel, FileList,
                      GradientTitle, SectionHeader, SegmentedControl, ToggleRow, card, field_label, label)
from .worker import EncodeWorker

APP_NAME = "NVIDIA Encoder Studio"
SHUTDOWN_COUNTDOWN = 60   # Sekunden bis zum Herunterfahren – Zeit zum Abbrechen


def shutdown_command() -> list[str]:
    """%SystemRoot%\\System32\\Shutdown.exe -s -t 00 -f"""
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return [os.path.join(root, "System32", "Shutdown.exe"), "-s", "-t", "00", "-f"]


class SystemProbe(QObject):
    """Prüft ffmpeg, GPU und Tonemapping im Hintergrund (für die Status-Pillen)."""
    done = Signal(dict)

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        ffmpeg = core.ffmpeg_status()
        result = {"ffmpeg": ffmpeg, "gpu": core.gpu_status(),
                  "tonemap": core.detect_tonemapper() if ffmpeg[0] else None}
        self.done.emit(result)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1150, 760)
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1500, int(screen.width() * 0.92)), min(1200, int(screen.height() * 0.92)))
        self.setAcceptDrops(True)
        self.settings = QSettings("AV1EncoderStudio", "AV1EncoderStudio")
        self.enc_thread: QThread | None = None
        self.worker: EncodeWorker | None = None
        self.results: list[dict] = []
        self.current_row = -1
        self.ffmpeg_ok = True
        self.available_codecs: set[str] | None = None   # None = noch nicht geprüft
        self.run_tag = ""                                # Codec des letzten Laufs (für die Ergebnis-Zeile)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)
        content = QWidget()
        content.setObjectName("scrollContent")
        scroll.setWidget(content)

        page = QVBoxLayout(content)
        page.setContentsMargins(28, 24, 28, 28)
        page.setSpacing(20)
        page.addWidget(self._build_header())

        columns = QHBoxLayout()
        columns.setSpacing(20)
        left = QVBoxLayout()
        left.setSpacing(20)
        left.addWidget(self._build_files_card(), 1)
        left.addWidget(self._build_settings_card())
        right = QVBoxLayout()
        right.setSpacing(20)
        right.addWidget(self._build_encode_card())
        right.addStretch(1)
        columns.addLayout(left, 58)
        columns.addLayout(right, 42)
        page.addLayout(columns, 1)

        self._load_settings()
        self._update_file_buttons()
        self._set_status("🎛️", "Bereit. Füge links Dateien hinzu – dann hier auf <b>Encoding starten</b> klicken.")

        self.probe = SystemProbe()
        self.probe.done.connect(self._on_system_probe)
        self.probe.start()

    # ================================================================== Aufbau

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("headerCard")
        lay = QHBoxLayout(header)
        lay.setContentsMargins(30, 26, 30, 26)
        lay.setSpacing(22)
        lay.addWidget(AppIcon(68), 0, Qt.AlignVCenter)
        texts = QVBoxLayout()
        texts.setSpacing(6)
        texts.addWidget(GradientTitle([("NVIDIA ", style.NVIDIA_GREEN), ("Encoder ", "white"), ("Studio", None)]))
        texts.addWidget(label("MKV & MP4 nach AV1 oder HEVC – hardwarebeschleunigt mit NVENC, HDR → SDR, "
                              "Qualitätsprofile und Samples", "appSubtitle"))
        badges = QHBoxLayout()
        badges.setSpacing(10)
        badges.setContentsMargins(0, 8, 0, 0)
        self.badge_ffmpeg = Badge("ffmpeg wird geprüft …")
        self.badge_gpu = Badge("GPU wird geprüft …")
        self.badge_tonemap = Badge("HDR → SDR wird geprüft …", accent=True)
        for b in (self.badge_ffmpeg, self.badge_gpu, self.badge_tonemap):
            badges.addWidget(b)
        badges.addStretch(1)
        texts.addLayout(badges)
        lay.addLayout(texts, 1)
        return header

    def _build_files_card(self) -> QWidget:
        frame, lay = card()
        lay.addWidget(SectionHeader("01", "Dateien",
                                    "MKV & MP4 hinzufügen – per Drag & Drop, auch aus verschiedenen Ordnern"))
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.btn_add_files = self._ghost("＋  Dateien", self._choose_files)
        self.btn_add_folder = self._ghost("📁  Ordner", self._choose_folder)
        bar.addWidget(self.btn_add_files)
        bar.addWidget(self.btn_add_folder)
        bar.addStretch(1)
        self.lbl_count = label("", "smallMuted")
        bar.addWidget(self.lbl_count)
        bar.addSpacing(6)
        self.btn_remove = self._ghost("Entfernen", self._remove_selected)
        self.btn_clear = self._ghost("Liste leeren", self._clear_list)
        bar.addWidget(self.btn_remove)
        bar.addWidget(self.btn_clear)
        lay.addLayout(bar)

        self.file_list = FileList()
        self.file_list.itemSelectionChanged.connect(self._update_file_buttons)
        self.file_list.itemDoubleClicked.connect(self._open_result)
        self.file_list.setToolTip("Doppelklick auf eine fertige Datei spielt das Ergebnis ab")
        QShortcut(QKeySequence.Delete, self.file_list, self._remove_selected)
        lay.addWidget(self.file_list, 1)
        return frame

    def _build_settings_card(self) -> QWidget:
        frame, lay = card()
        lay.addWidget(SectionHeader("02", "Einstellungen", "Codec, Qualität, Auflösung und Optionen (NVENC)"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(22)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        # Links: Codec | Rechts: Encoding-Modus
        self.codec_keys = list(core.CODECS)
        grid.addWidget(field_label("Codec"), 0, 0)
        self.codec = SegmentedControl([core.CODECS[k].label for k in self.codec_keys])
        self.codec.setToolTip("AV1: kleinere Dateien bei gleicher Qualität\n"
                              "HEVC (H.265): läuft auch auf älteren Fernsehern, Handys und Playern")
        self.codec.changed.connect(self._on_codec_changed)
        grid.addWidget(self.codec, 1, 0)
        grid.addWidget(label("AV1: kleinere Dateien  ·  HEVC: läuft auf mehr (älteren) Geräten", "hint", wrap=True),
                       2, 0, Qt.AlignTop)
        grid.addWidget(field_label("Encoding-Modus"), 0, 1)
        self.mode = SegmentedControl(["◎  Smart CQ", "⇅  Bitrate"])
        self.mode.changed.connect(lambda i: self.rate_stack.setCurrentIndex(i))
        grid.addWidget(self.mode, 1, 1)
        grid.setRowMinimumHeight(3, 6)

        # Links: Zielauflösung + Qualität
        grid.addWidget(field_label("Zielauflösung"), 4, 0)
        self.cmb_res = QComboBox()
        for width, text in core.RESOLUTIONS:
            self.cmb_res.addItem(text, width)
        grid.addWidget(self.cmb_res, 5, 0)
        grid.setRowMinimumHeight(6, 4)
        grid.addWidget(field_label("Qualität"), 7, 0)
        self.cmb_quality = QComboBox()
        for key, (text, _, _) in core.QUALITY_PROFILES.items():
            self.cmb_quality.addItem(text, key)
        self.cmb_quality.currentIndexChanged.connect(self._update_quality_info)
        grid.addWidget(self.cmb_quality, 8, 0)
        self.lbl_quality_info = label("", "hint", wrap=True)
        grid.addWidget(self.lbl_quality_info, 9, 0, Qt.AlignTop)

        # Rechts: CQ-Info bzw. Bitrate-Regler (gehört zum Encoding-Modus)
        self.rate_stack = QStackedWidget()
        cq_page = QWidget()
        cq_lay = QVBoxLayout(cq_page)
        cq_lay.setContentsMargins(0, 0, 0, 0)
        cq_lay.setSpacing(8)
        self.lbl_cq = field_label("Konstante Qualität")
        cq_lay.addWidget(self.lbl_cq)
        cq_lay.addWidget(label("Die Bitrate passt sich dem Bildinhalt an – ruhige Szenen werden klein, "
                               "aufwendige bekommen mehr Daten.", "hint", wrap=True))
        cq_lay.addStretch(1)
        self.rate_stack.addWidget(cq_page)

        br_page = QWidget()
        br_lay = QVBoxLayout(br_page)
        br_lay.setContentsMargins(0, 0, 0, 0)
        br_lay.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(field_label("Bitrate (Mbit/s)"))
        head.addStretch(1)
        self.spin_bitrate = QSpinBox()
        self.spin_bitrate.setRange(core.BITRATE_MIN, core.BITRATE_MAX)
        self.spin_bitrate.setButtonSymbols(QSpinBox.NoButtons)
        self.spin_bitrate.setAlignment(Qt.AlignCenter)
        self.spin_bitrate.setFixedWidth(84)
        head.addWidget(self.spin_bitrate)
        reset = QPushButton("↺")
        reset.setObjectName("ghost")
        reset.setStyleSheet('font-family: "Segoe UI Symbol"; font-size: 17px; padding: 0;')
        reset.setFixedSize(38, 36)
        reset.setCursor(Qt.PointingHandCursor)
        reset.setToolTip(f"Zurück auf {core.BITRATE_DEFAULT} Mbit/s")
        reset.clicked.connect(lambda: self.spin_bitrate.setValue(core.BITRATE_DEFAULT))
        head.addWidget(reset)
        br_lay.addLayout(head)
        slider_row = QHBoxLayout()
        slider_row.setSpacing(12)
        slider_row.addWidget(label(str(core.BITRATE_MIN), "smallMuted"))
        self.slider_bitrate = QSlider(Qt.Horizontal)
        self.slider_bitrate.setRange(core.BITRATE_MIN, core.BITRATE_MAX)
        self.slider_bitrate.setCursor(Qt.PointingHandCursor)
        slider_row.addWidget(self.slider_bitrate, 1)
        slider_row.addWidget(label(str(core.BITRATE_MAX), "smallMuted"))
        br_lay.addLayout(slider_row)
        br_lay.addStretch(1)
        self.slider_bitrate.valueChanged.connect(self.spin_bitrate.setValue)
        self.spin_bitrate.valueChanged.connect(self.slider_bitrate.setValue)
        self.rate_stack.addWidget(br_page)
        grid.addWidget(self.rate_stack, 4, 1, 6, 1)
        lay.addLayout(grid)

        # Schalter
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {style.CARD_BORDER};")
        lay.addWidget(sep)
        self.tg_10bit = ToggleRow("10-Bit encodieren", "Weniger Banding, HDR behält die volle Farbtiefe")
        self.tg_hdr = ToggleRow("HDR → SDR (Tonemapping)", "Nur bei HDR10-, HLG- und Dolby-Vision-Dateien")
        self.tg_bars = ToggleRow("Schwarze Balken entfernen", "Automatisch erkennen, Auflösung passt sich an")
        self.tg_stereo = ToggleRow("Mehrkanal-Ton → Stereo", "5.1 / 7.1 wird zu Stereo heruntergemischt")
        self.txt_sample = QLineEdit("00:10:00")
        self.txt_sample.setFixedWidth(104)
        self.txt_sample.setAlignment(Qt.AlignCenter)
        self.txt_sample.setPlaceholderText("hh:mm:ss")
        self.txt_sample.setValidator(QRegularExpressionValidator(QRegularExpression(r"[0-9:.,]{0,12}")))
        self.txt_sample.setAcceptDrops(False)
        self.txt_sample.setToolTip("Startzeit des Samples: hh:mm:ss, mm:ss oder Sekunden")
        self.tg_sample = ToggleRow(f"Sample: nur {core.SAMPLE_SECONDS} Sek. encodieren, ab",
                                   "Qualität vorab prüfen – Startzeit rechts festlegen (hh:mm:ss)",
                                   extra=self.txt_sample)
        self.tg_sample.switch.toggled.connect(self._update_sample_field)
        toggles = QGridLayout()
        toggles.setHorizontalSpacing(22)
        toggles.setVerticalSpacing(10)
        toggles.setColumnStretch(0, 1)
        toggles.setColumnStretch(1, 1)
        toggles.addWidget(self.tg_10bit, 0, 0)
        toggles.addWidget(self.tg_hdr, 0, 1)
        toggles.addWidget(self.tg_bars, 1, 0)
        toggles.addWidget(self.tg_stereo, 1, 1)
        toggles.addWidget(self.tg_sample, 2, 0, 1, 2)
        lay.addLayout(toggles)
        return frame

    def _build_encode_card(self) -> QWidget:
        frame, lay = card()
        lay.addWidget(SectionHeader("03", "Encodieren", "Speicherort wählen und mit NVENC encodieren"))

        # Speicherort: Quellordner oder ein gemeinsamer Zielordner
        lay.addWidget(field_label("Speicherort"))
        self.out_mode = SegmentedControl(["📂  Quellordner", "📁  Eigener Ordner"])
        self.out_mode.changed.connect(self._on_out_mode_changed)
        lay.addWidget(self.out_mode)
        self.out_row = QWidget()
        orow = QHBoxLayout(self.out_row)
        orow.setContentsMargins(0, 0, 0, 0)
        orow.setSpacing(10)
        self.txt_out_dir = QLineEdit()
        self.txt_out_dir.setPlaceholderText("Zielordner für alle Encodings …")
        self.txt_out_dir.setAcceptDrops(False)
        self.txt_out_dir.setToolTip("Alle Ausgaben dieses Laufs landen hier. Fehlt der Ordner, wird er angelegt.")
        self.btn_out_dir = self._ghost("Wählen …", self._choose_out_dir)
        orow.addWidget(self.txt_out_dir, 1)
        orow.addWidget(self.btn_out_dir)
        lay.addWidget(self.out_row)
        self.lbl_out_hint = label("", "hint", wrap=True)
        lay.addWidget(self.lbl_out_hint)

        self.btn_start = QPushButton("🎬   Encoding starten")
        self.btn_start.setObjectName("primary")
        self.btn_start.setCursor(Qt.PointingHandCursor)
        self.btn_start.clicked.connect(self._start)
        glow = QGraphicsDropShadowEffect(self.btn_start)
        glow.setBlurRadius(38)
        glow.setOffset(0, 8)
        glow.setColor(QColor(124, 58, 237, 110))
        self.btn_start.setGraphicsEffect(glow)
        lay.addWidget(self.btn_start)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.btn_cancel = QPushButton("■  Abbrechen")
        self.btn_cancel.setObjectName("danger")
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_show = self._ghost("📂  Im Ordner zeigen", self._show_in_folder)
        self.btn_show.setToolTip("Zeigt das Ergebnis der markierten Datei (oder die Quelle) im Explorer")
        row.addWidget(self.btn_cancel, 1)
        row.addWidget(self.btn_show, 1)
        lay.addLayout(row)

        # Auto-Shutdown – bewusst NICHT gespeichert, damit er nie unbemerkt vom letzten Mal aktiv ist
        self.tg_shutdown = ToggleRow("Nach Ende herunterfahren",
                                     f"PC fährt {SHUTDOWN_COUNTDOWN} s nach dem letzten Encoding herunter "
                                     "– bis dahin abbrechbar")
        self.tg_shutdown.switch.toggled.connect(lambda on: on or self._abort_shutdown("Schalter ausgeschaltet"))
        lay.addWidget(self.tg_shutdown)
        self.shutdown_timer = QTimer(self)
        self.shutdown_timer.setInterval(1000)
        self.shutdown_timer.timeout.connect(self._shutdown_tick)
        self.shutdown_remaining = 0
        self.final_summary = ""

        # Status-Box
        box = QFrame()
        box.setObjectName("statusBox")
        bl = QHBoxLayout(box)
        bl.setContentsMargins(18, 16, 18, 16)
        bl.setSpacing(16)
        self.status_icon = label("", "statusIcon")
        self.status_icon.setAlignment(Qt.AlignCenter)
        self.status_icon.setFixedSize(48, 48)
        self.status_text = label("", "statusText", wrap=True)
        self.status_text.setTextFormat(Qt.RichText)
        bl.addWidget(self.status_icon, 0, Qt.AlignTop)
        bl.addWidget(self.status_text, 1)
        lay.addWidget(box)

        # Fortschritt
        prog = QVBoxLayout()
        prog.setSpacing(6)
        cur_head = QHBoxLayout()
        self.lbl_current = ElidedLabel("Aktuelle Datei", "smallMuted")
        self.lbl_fps = label("", "fpsLabel")
        self.lbl_file_pct = label("0 %", "pctLabel")
        cur_head.addWidget(self.lbl_current, 1)
        cur_head.addWidget(self.lbl_fps)
        cur_head.addSpacing(12)
        cur_head.addWidget(self.lbl_file_pct)
        prog.addLayout(cur_head)
        self.bar_file = QProgressBar()
        self.bar_file.setRange(0, 1000)
        self.bar_file.setTextVisible(False)
        prog.addWidget(self.bar_file)
        prog.addSpacing(6)
        tot_head = QHBoxLayout()
        tot_head.addWidget(label("Gesamt", "smallMuted"), 1)
        self.lbl_total_pct = label("0 %", "totalPct")
        tot_head.addWidget(self.lbl_total_pct)
        prog.addLayout(tot_head)
        self.bar_total = QProgressBar()
        self.bar_total.setObjectName("totalBar")
        self.bar_total.setRange(0, 1000)
        self.bar_total.setTextVisible(False)
        prog.addWidget(self.bar_total)
        lay.addLayout(prog)

        # Ergebnis
        lay.addWidget(label("♫  Ergebnis", "resultHeader"))
        self.result_stack = QStackedWidget()
        empty = label("♫\nNoch keine Ergebnisse", "hint")
        empty.setAlignment(Qt.AlignCenter)
        empty.setMinimumHeight(90)
        self.result_stack.addWidget(empty)
        stats = QWidget()
        sl = QVBoxLayout(stats)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(10)
        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        self.stat_ok = self._stat_tile(tiles, "Fertig")
        self.stat_failed = self._stat_tile(tiles, "Fehlgeschlagen")
        self.stat_saving = self._stat_tile(tiles, "Ersparnis")
        sl.addLayout(tiles)
        self.lbl_sizes = label("", "smallMuted", wrap=True)
        sl.addWidget(self.lbl_sizes)
        self.result_stack.addWidget(stats)
        lay.addWidget(self.result_stack)

        # Protokoll
        self.log = QPlainTextEdit()
        self.log.setObjectName("log")
        self.log.setReadOnly(True)
        self.log.setAcceptDrops(False)
        self.log.setMaximumBlockCount(5000)
        self.log.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.log.setMinimumHeight(220)
        self.log_section = Collapsible("Protokoll", self.log, expanded=False)
        lay.addWidget(self.log_section)
        return frame

    # ---------------------------------------------------------------- kleine Helfer

    def _ghost(self, text: str, slot) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("ghost")
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(slot)
        return b

    def _stat_tile(self, layout: QHBoxLayout, caption: str):
        tile = QFrame()
        tile.setObjectName("statTile")
        tl = QVBoxLayout(tile)
        tl.setContentsMargins(14, 10, 14, 12)
        tl.setSpacing(2)
        tl.addWidget(field_label(caption))
        value = label("–", "statValue")
        tl.addWidget(value)
        layout.addWidget(tile, 1)
        return value

    def _set_status(self, icon: str, html: str) -> None:
        self.status_icon.setText(icon)
        self.status_text.setText(html)

    def _append_log(self, text: str) -> None:
        self.log.appendPlainText(text)

    def _codec_key(self) -> str:
        return self.codec_keys[max(0, self.codec.index())]

    def _on_codec_changed(self, _index: int = 0) -> None:
        c = core.CODECS[self._codec_key()]
        self.lbl_cq.setText(f"KONSTANTE QUALITÄT (CQ {c.cq})")
        if self.available_codecs is not None and self._codec_key() not in self.available_codecs:
            self._set_status("⚠️", f"<b>{c.label} ist nicht verfügbar:</b> dein ffmpeg hat keinen {c.encoder}.")
        self._update_file_buttons()

    def _update_quality_info(self) -> None:
        key = self.cmb_quality.currentData()
        self.lbl_quality_info.setText(core.QUALITY_PROFILES.get(key, core.QUALITY_PROFILES["high"])[2])

    def _update_sample_field(self) -> None:
        self.txt_sample.setEnabled(self.tg_sample.isChecked() and self.worker is None)

    # ================================================================== Dateiliste

    def _rows(self) -> list[QListWidgetItem]:
        return [self.file_list.item(i) for i in range(self.file_list.count())]

    def add_paths(self, paths: list[str]) -> None:
        known = {item.data(ROLE_PATH).lower() for item in self._rows()}
        added = dupes = skipped_out = ignored = 0
        for raw in paths:
            p = Path(raw)
            if p.is_dir():
                candidates, from_folder = sorted((c for c in p.iterdir() if c.is_file()),
                                                 key=lambda c: c.name.lower()), True
            elif p.is_file():
                candidates, from_folder = [p], False
            else:
                continue
            for c in candidates:
                if not core.is_video_file(c):
                    ignored += 0 if from_folder else 1
                    continue
                if core.is_output_file(c):
                    skipped_out += 1
                    continue
                key = str(c).lower()
                if key in known:
                    dupes += 1
                    continue
                known.add(key)
                item = QListWidgetItem(c.name)
                item.setData(ROLE_PATH, str(c))
                item.setToolTip(str(c))
                self.file_list.addItem(item)
                added += 1
        msg = f"{added} Datei(en) hinzugefügt"
        if dupes:
            msg += f", {dupes} schon in der Liste"
        if skipped_out:
            msg += f", {skipped_out} bereits umgewandelte (_AV1/_HEVC) übersprungen"
        if ignored:
            msg += f", {ignored} keine MKV/MP4 ignoriert"
        self._append_log(f"{msg}. Liste: {self.file_list.count()} Datei(en).")
        if self.worker is None:
            self._set_status("📥", f"<b>{msg}.</b><br>Liste: {self.file_list.count()} Datei(en).")
        self._update_file_buttons()

    def _choose_files(self) -> None:
        start = self.settings.value("last_dir", "")
        files, _ = QFileDialog.getOpenFileNames(self, "Videodateien auswählen", start,
                                                "Videos (*.mkv *.mp4);;Alle Dateien (*.*)")
        if files:
            self.settings.setValue("last_dir", str(Path(files[0]).parent))
            self.add_paths(files)

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Ordner mit MKV/MP4-Dateien auswählen",
                                                  self.settings.value("last_dir", ""))
        if folder:
            self.settings.setValue("last_dir", folder)
            self.add_paths([folder])

    def _remove_selected(self) -> None:
        if self.worker is not None:
            return
        for item in self.file_list.selectedItems():
            self.file_list.takeItem(self.file_list.row(item))
        self._update_file_buttons()

    def _clear_list(self) -> None:
        if self.worker is None:
            self.file_list.clear()
            self._update_file_buttons()

    def _update_file_buttons(self) -> None:
        idle = self.worker is None
        n = self.file_list.count()
        self.lbl_count.setText(f"{n} Datei{'en' if n != 1 else ''}" if n else "")
        self.btn_remove.setEnabled(idle and bool(self.file_list.selectedItems()))
        self.btn_clear.setEnabled(idle and n > 0)
        codec_ok = self.available_codecs is None or self._codec_key() in self.available_codecs
        self.btn_start.setEnabled(idle and n > 0 and self.ffmpeg_ok and codec_ok)

    def _selected_or_current(self) -> QListWidgetItem | None:
        items = self.file_list.selectedItems()
        if items:
            return items[0]
        return self.file_list.item(self.current_row) if self.current_row >= 0 else None

    def _open_result(self, item: QListWidgetItem) -> None:
        status = item.data(ROLE_STATUS) or {}
        out = status.get("out_path")
        if status.get("status") == "ok" and out and Path(out).exists():
            os.startfile(out)

    def _show_in_folder(self) -> None:
        item = self._selected_or_current() or (self.file_list.item(0) if self.file_list.count() else None)
        if item is None:
            return
        status = item.data(ROLE_STATUS) or {}
        if status.get("status") == "ok":
            target = status.get("out_path")
        elif self._custom_out_dir() and Path(self._custom_out_dir()).is_dir():
            subprocess.Popen(f'explorer "{self._custom_out_dir()}"')   # noch nichts fertig: Zielordner öffnen
            return
        else:
            target = item.data(ROLE_PATH)
        if target and Path(target).exists():
            subprocess.Popen(f'explorer /select,"{target}"')

    # ---------------------------------------------------------------- Speicherort

    def _custom_out_dir(self) -> str:
        """Gewählter Zielordner oder "" im Modus Quellordner."""
        return self.txt_out_dir.text().strip() if self.out_mode.index() == 1 else ""

    def _on_out_mode_changed(self, index: int) -> None:
        if index == 1 and not self.txt_out_dir.text().strip():
            self._choose_out_dir()
            if not self.txt_out_dir.text().strip():   # Auswahl abgebrochen -> zurück zum Quellordner
                self.out_mode.set_index(0)
        self._update_out_row()

    def _choose_out_dir(self) -> None:
        start = self.txt_out_dir.text().strip() or self.settings.value("last_dir", "")
        folder = QFileDialog.getExistingDirectory(self, "Zielordner für alle Encodings wählen", start)
        if folder:
            self.txt_out_dir.setText(str(Path(folder)))

    def _update_out_row(self) -> None:
        custom = self.out_mode.index() == 1
        self.out_row.setVisible(custom)
        self.lbl_out_hint.setText(
            "Alle Encodings landen in diesem Ordner. Gleichnamige Dateien aus verschiedenen Quellordnern "
            "bekommen automatisch eine Nummer." if custom else
            "Jede Ausgabe landet neben ihrer Quelldatei – auch bei Dateien aus verschiedenen Ordnern.")

    # ---------------------------------------------------------------- Drag & Drop (ganzes Fenster)

    def dragEnterEvent(self, e):
        if self.worker is None and e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.file_list.set_drop_active(True)
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if self.worker is None and e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self.file_list.set_drop_active(False)

    def dropEvent(self, e):
        self.file_list.set_drop_active(False)
        if self.worker is not None:
            return
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            e.acceptProposedAction()
            self.add_paths(paths)

    # ================================================================== Encoding

    def _collect_settings(self) -> core.EncodeSettings | None:
        sample_start = None
        if self.tg_sample.isChecked():
            sample_start = core.parse_time(self.txt_sample.text())
            if sample_start is None:
                self._set_status("⚠️", f"<b>Ungültige Startzeit für das Sample:</b> „{self.txt_sample.text()}“<br>"
                                       "Bitte im Format hh:mm:ss angeben, z. B. 00:10:00.")
                self.txt_sample.setFocus()
                return None
        output_dir = None
        if self.out_mode.index() == 1:
            text = self.txt_out_dir.text().strip()
            target = Path(text) if text else None
            if target is None or not target.is_absolute() or (target.exists() and not target.is_dir()):
                self._set_status("⚠️", "<b>Bitte einen gültigen Zielordner wählen</b><br>"
                                       "oder als Speicherort „Quellordner“ einstellen.")
                self.txt_out_dir.setFocus()
                return None
            output_dir = str(target)
        return core.EncodeSettings(
            codec=self._codec_key(),
            output_dir=output_dir,
            width=self.cmb_res.currentData(),
            use_bitrate=self.mode.index() == 1,
            bitrate_mbit=self.spin_bitrate.value(),
            quality=self.cmb_quality.currentData(),
            ten_bit=self.tg_10bit.isChecked(),
            hdr_to_sdr=self.tg_hdr.isChecked(),
            sample_start=sample_start,
            remove_bars=self.tg_bars.isChecked(),
            stereo=self.tg_stereo.isChecked(),
        )

    def _start(self) -> None:
        rows = self._rows()
        if not rows or self.worker is not None:
            return
        self._abort_shutdown("abgebrochen (neues Encoding gestartet)")
        s = self._collect_settings()
        if s is None:
            return
        self._save_settings()

        for item in rows:  # alle Dateien werden neu encodiert -> alte Markierungen entfernen
            item.setData(ROLE_STATUS, None)
            item.setData(ROLE_CURRENT, False)
        self.results.clear()
        self.current_row = -1
        self._update_results()
        self.log.clear()
        mode = f"Bitrate {s.bitrate_mbit} Mbit" if s.use_bitrate else f"CQ {s.codec_info.cq}"
        self.run_tag = s.codec_info.tag
        sample = (f"{core.SAMPLE_SECONDS} Sek. ab {core.seconds_to_hms(s.sample_start)}"
                  if s.is_sample else "aus")
        self._append_log("=== Encoding gestartet ===")
        self._append_log(f"Codec: {s.codec_info.label} ({s.codec_info.encoder}) | "
                         f"Modus: {mode} | Qualität: {self.cmb_quality.currentText()} | "
                         f"10-Bit: {'an' if s.ten_bit else 'aus'} | Auflösung: {self.cmb_res.currentText()} | "
                         f"HDR → SDR: {'an' if s.hdr_to_sdr else 'aus'} | "
                         f"Balken entfernen: {'an' if s.remove_bars else 'aus'} | "
                         f"Stereo: {'an' if s.stereo else 'aus'} | Sample: {sample} | Dateien: {len(rows)}")
        self._append_log(f"Speicherort: {s.output_dir or 'jeweiliger Quellordner'}")
        self._append_log("")

        files = [Path(item.data(ROLE_PATH)) for item in rows]
        self.enc_thread = QThread(self)
        self.worker = EncodeWorker(files, s)
        self.worker.moveToThread(self.enc_thread)
        self.enc_thread.started.connect(self.worker.run)
        self.worker.log.connect(self._append_log)
        self.worker.file_started.connect(self._on_file_started)
        self.worker.progress.connect(self._on_progress)
        self.worker.fps.connect(self._on_fps)
        self.worker.file_finished.connect(self._on_file_finished)
        self.worker.finished.connect(self._on_finished)
        self.worker.finished.connect(self.enc_thread.quit)
        self.enc_thread.finished.connect(self.worker.deleteLater)
        self.enc_thread.finished.connect(self.enc_thread.deleteLater)
        self._set_running(True)
        self._on_progress(0, 0)
        self.enc_thread.start()

    def _cancel(self) -> None:
        if self.shutdown_timer.isActive():   # während des Countdowns: Herunterfahren abbrechen
            self._abort_shutdown("abgebrochen")
            return
        if self.worker is not None:
            self.worker.cancel()
            self.btn_cancel.setEnabled(False)
            self._append_log("» Abbruch angefordert …")
            self._set_status("⏹️", "<b>Wird abgebrochen …</b>")

    def _set_running(self, running: bool) -> None:
        idle = not running
        for w in (self.btn_add_files, self.btn_add_folder, self.codec, self.mode, self.cmb_res, self.cmb_quality,
                  self.slider_bitrate, self.spin_bitrate, self.tg_10bit.switch, self.tg_hdr.switch,
                  self.tg_bars.switch, self.tg_stereo.switch, self.tg_sample.switch,
                  self.out_mode, self.txt_out_dir, self.btn_out_dir):
            w.setEnabled(idle)
        self.btn_cancel.setEnabled(running)
        if idle:
            self.worker = None
            self.enc_thread = None
        self._update_sample_field()
        self._update_file_buttons()

    def _on_file_started(self, index: int, name: str) -> None:
        if 0 <= self.current_row < self.file_list.count():
            self.file_list.item(self.current_row).setData(ROLE_CURRENT, False)
        self.current_row = index
        item = self.file_list.item(index)
        item.setData(ROLE_CURRENT, True)
        self.file_list.scrollToItem(item)
        self.lbl_current.set_full_text(name)
        total = self.file_list.count()
        self._set_status("⚙️", f"<b>Encodiere {index + 1} / {total}</b><br>{name}")

    def _on_progress(self, file_pct: float, total_pct: float) -> None:
        self.bar_file.setValue(int(file_pct * 10))
        self.bar_total.setValue(int(total_pct * 10))
        self.lbl_file_pct.setText(f"{file_pct:.1f} %".replace(".", ","))
        self.lbl_total_pct.setText(f"{total_pct:.1f} %".replace(".", ","))

    def _on_fps(self, fps: float) -> None:
        self.lbl_fps.setText(f"{fps:.0f} fps" if fps > 0 else "")

    def _on_file_finished(self, index: int, result: dict) -> None:
        self.file_list.item(index).setData(ROLE_STATUS, result)
        self.results.append(result)
        self._update_results()
        if result.get("status") == "failed":
            self.log_section.set_expanded(True)  # Fehler sofort sichtbar machen

    def _on_finished(self, ok: int, failed: int, cancelled: bool) -> None:
        if 0 <= self.current_row < self.file_list.count():
            self.file_list.item(self.current_row).setData(ROLE_CURRENT, False)
        self._on_fps(0)
        self._set_running(False)
        if cancelled:
            self._set_status("⏹️", f"<b>Abgebrochen.</b> {ok} fertig, {failed} fehlgeschlagen.")
        elif failed:
            self._set_status("⚠️", f"<b>Fertig mit Fehlern:</b> {ok} erfolgreich, {failed} fehlgeschlagen.<br>"
                                   "Details stehen im Protokoll.")
        else:
            self._set_status("✅", f"<b>Fertig!</b> {ok} Datei(en) erfolgreich encodiert.<br>"
                                   "Doppelklick auf eine Datei spielt das Ergebnis ab.")
        QApplication.alert(self)  # Taskleiste blinkt, falls das Fenster im Hintergrund ist
        # Auto-Shutdown nur nach regulärem Ende, nicht nach einem Abbruch durch den Benutzer
        if not cancelled and self.tg_shutdown.isChecked():
            self.final_summary = f"{ok} erfolgreich, {failed} fehlgeschlagen"
            self._start_shutdown_countdown()

    # ================================================================== Auto-Shutdown

    def _start_shutdown_countdown(self) -> None:
        self.shutdown_remaining = SHUTDOWN_COUNTDOWN
        self._append_log(f"» Auto-Shutdown: PC fährt in {SHUTDOWN_COUNTDOWN} s herunter.")
        self.btn_cancel.setText("■  Herunterfahren abbrechen")
        self.btn_cancel.setEnabled(True)
        self._update_shutdown_status()
        self.shutdown_timer.start()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _shutdown_tick(self) -> None:
        self.shutdown_remaining -= 1
        if self.shutdown_remaining > 0:
            self._update_shutdown_status()
            return
        self.shutdown_timer.stop()
        self._execute_shutdown()

    def _update_shutdown_status(self) -> None:
        self._set_status("⏻", f"<b>PC wird in {self.shutdown_remaining} s heruntergefahren.</b><br>"
                              f"Encoding beendet ({self.final_summary}). Zum Verhindern auf "
                              "<b>Herunterfahren abbrechen</b> klicken.")

    def _abort_shutdown(self, reason: str) -> None:
        if not self.shutdown_timer.isActive():
            return
        self.shutdown_timer.stop()
        self.btn_cancel.setText("■  Abbrechen")
        self.btn_cancel.setEnabled(self.worker is not None)
        self._append_log(f"» Auto-Shutdown {reason}.")
        self._set_status("✋", f"<b>Herunterfahren {reason}.</b><br>Encoding beendet ({self.final_summary}).")

    def _execute_shutdown(self) -> None:
        self._save_settings()   # closeEvent läuft beim erzwungenen Herunterfahren nicht mehr
        cmd = shutdown_command()
        self._append_log("» Auto-Shutdown: " + subprocess.list2cmdline(cmd))
        try:
            subprocess.Popen(cmd, creationflags=core.CREATE_NO_WINDOW)
        except OSError as e:
            self.btn_cancel.setText("■  Abbrechen")
            self.btn_cancel.setEnabled(False)
            self._set_status("⚠️", f"<b>Herunterfahren fehlgeschlagen:</b> {e}")

    def _update_results(self) -> None:
        if not self.results:
            self.result_stack.setCurrentIndex(0)
            return
        self.result_stack.setCurrentIndex(1)
        done = [r for r in self.results if r.get("status") == "ok"]
        self.stat_ok.setText(str(len(done)))
        self.stat_failed.setText(str(len(self.results) - len(done)))
        src = sum(r["src_size"] for r in done)
        out = sum(r["out_size"] for r in done)
        pct = core.saving_percent(src, out)
        if pct is None:
            self.stat_saving.setText("–")
            self.stat_saving.setStyleSheet("")
            self.lbl_sizes.setText("")
            return
        self.stat_saving.setText(f"−{pct:.0f} %" if pct >= 0 else f"+{-pct:.0f} %")
        self.stat_saving.setStyleSheet(f"color: {style.GREEN if pct >= 0 else style.ORANGE};")
        text = f"Quelle {core.format_size(src)}  →  {self.run_tag or 'Ausgabe'} {core.format_size(out)}"
        if any(r.get("is_sample") for r in done):
            text += "  ·  bei Samples verglichen mit dem gleich langen Abschnitt der Quelle"
        self.lbl_sizes.setText(text)

    # ================================================================== Systemstatus

    def _on_system_probe(self, info: dict) -> None:
        ok, text, available = info["ffmpeg"]
        self.badge_ffmpeg.set_state(text, ok)
        self.ffmpeg_ok = ok
        self.available_codecs = available
        if ok:
            self._on_codec_changed()
        ok_gpu, text_gpu = info["gpu"]
        self.badge_gpu.set_state(text_gpu, ok_gpu)
        tm = info["tonemap"]
        if tm == "libplacebo":
            self.badge_tonemap.set_state("HDR → SDR · libplacebo (GPU)", True)
        elif tm == "zscale":
            self.badge_tonemap.set_state("HDR → SDR · zscale (CPU)", True)
        else:
            self.badge_tonemap.set_state("HDR → SDR nicht verfügbar", False)
        if not ok:
            self._set_status("⚠️", f"<b>{text}.</b><br>Bitte ffmpeg mit NVENC (av1_nvenc / hevc_nvenc) installieren "
                                   "und in den PATH legen.")
        self._update_file_buttons()

    # ================================================================== Einstellungen

    def _load_settings(self) -> None:
        st = self.settings
        geo = st.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        idx = self.cmb_res.findData(int(st.value("width", core.DEFAULT_WIDTH)))
        self.cmb_res.setCurrentIndex(max(0, idx))
        idx = self.cmb_quality.findData(st.value("quality", core.DEFAULT_QUALITY))
        self.cmb_quality.setCurrentIndex(max(0, idx))
        self._update_quality_info()
        codec = st.value("codec", core.DEFAULT_CODEC)
        self.codec.set_index(self.codec_keys.index(codec) if codec in self.codec_keys else 0)
        self._on_codec_changed()
        self.mode.set_index(1 if st.value("use_bitrate", False, type=bool) else 0)
        self.rate_stack.setCurrentIndex(self.mode.index())
        self.spin_bitrate.setValue(int(st.value("bitrate", core.BITRATE_DEFAULT)))
        self.slider_bitrate.setValue(self.spin_bitrate.value())
        self.tg_10bit.switch.setChecked(st.value("ten_bit", False, type=bool))
        self.tg_hdr.switch.setChecked(st.value("hdr_to_sdr", False, type=bool))
        self.tg_bars.switch.setChecked(st.value("remove_bars", False, type=bool))
        self.tg_stereo.switch.setChecked(st.value("stereo", False, type=bool))
        self.txt_out_dir.setText(st.value("out_dir", ""))
        # Ohne gespeicherten Ordner nicht im Modus "Eigener Ordner" starten (würde sofort den Dialog öffnen)
        custom = st.value("out_custom", False, type=bool) and bool(self.txt_out_dir.text().strip())
        self.out_mode.group.blockSignals(True)
        self.out_mode.set_index(1 if custom else 0)
        self.out_mode.group.blockSignals(False)
        self._update_out_row()
        self.tg_sample.switch.setChecked(st.value("sample", False, type=bool))
        self.txt_sample.setText(st.value("sample_time", "00:10:00"))
        self._update_sample_field()

    def _save_settings(self) -> None:
        st = self.settings
        st.setValue("geometry", self.saveGeometry())
        st.setValue("width", self.cmb_res.currentData())
        st.setValue("quality", self.cmb_quality.currentData())
        st.setValue("codec", self._codec_key())
        st.setValue("use_bitrate", self.mode.index() == 1)
        st.setValue("bitrate", self.spin_bitrate.value())
        st.setValue("ten_bit", self.tg_10bit.isChecked())
        st.setValue("hdr_to_sdr", self.tg_hdr.isChecked())
        st.setValue("remove_bars", self.tg_bars.isChecked())
        st.setValue("stereo", self.tg_stereo.isChecked())
        st.setValue("out_custom", self.out_mode.index() == 1)
        st.setValue("out_dir", self.txt_out_dir.text().strip())
        st.setValue("sample", self.tg_sample.isChecked())
        st.setValue("sample_time", self.txt_sample.text())

    def closeEvent(self, e):
        self._save_settings()
        if self.worker is not None and self.enc_thread is not None:
            self.worker.cancel()
            self.enc_thread.quit()
            self.enc_thread.wait(5000)
        super().closeEvent(e)


# ====================================================================== Start

def _app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(QSize(size, size))
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        AppIcon.draw(p, QRectF(0, 0, size, size))
        p.end()
        icon.addPixmap(pm)
    return icon


def _dark_title_bar(window: QWidget) -> None:
    """Dunkle Fenster-Titelleiste unter Windows 10/11."""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(window.winId())
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
    except (AttributeError, OSError):
        pass


def main() -> int:
    if sys.platform == "win32":
        try:  # eigenes Taskleisten-Symbol statt python.exe
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AV1EncoderStudio")
        except (AttributeError, OSError):
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    app.setFont(QFont(style.FONT, 10))
    app.setStyleSheet(style.build_stylesheet())
    app.setWindowIcon(_app_icon())
    window = MainWindow()
    _dark_title_bar(window)
    window.show()
    return app.exec()
