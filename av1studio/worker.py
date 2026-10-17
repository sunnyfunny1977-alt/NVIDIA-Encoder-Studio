"""Encoder-Lauf im Hintergrund-Thread. Meldet alles per Qt-Signal an die Oberfläche."""
from __future__ import annotations

import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from . import core


class EncodeWorker(QObject):
    log = Signal(str)
    file_started = Signal(int, str)          # Index, Dateiname
    progress = Signal(float, float)          # Datei-%, Gesamt-%
    fps = Signal(float)                      # aktuelle FPS (0 = keine Anzeige)
    file_finished = Signal(int, dict)        # Index, Ergebnis
    finished = Signal(int, int, bool)        # erfolgreich, fehlgeschlagen, abgebrochen

    def __init__(self, files: list[Path], settings: core.EncodeSettings):
        super().__init__()
        self.files = files
        self.settings = settings
        self._cancel = False
        self._proc: subprocess.Popen | None = None

    def cancel(self) -> None:
        """Aus dem UI-Thread aufrufbar: laufendes ffmpeg sofort beenden."""
        self._cancel = True
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    # ------------------------------------------------------------------ Hauptschleife

    @Slot()
    def run(self) -> None:
        s = self.settings
        total = len(self.files)
        ok = failed = 0
        tonemapper: str | None = None
        taken: set[str] = set()   # in diesem Lauf vergebene Ausgabepfade (gegen gegenseitiges Überschreiben)

        if s.output_dir:
            try:
                Path(s.output_dir).mkdir(parents=True, exist_ok=True)
            except OSError as e:
                self.log.emit(f"FEHLER: Zielordner kann nicht angelegt werden: {s.output_dir} ({e})")
                for i in range(total):
                    self.file_finished.emit(i, {"status": "failed"})
                self.finished.emit(0, total, False)
                return

        def fail(index: int, message: str) -> None:
            nonlocal failed
            failed += 1
            self.log.emit(f"  FEHLER: {message}")
            self.log.emit("")
            self.file_finished.emit(index, {"status": "failed"})

        for i, src in enumerate(self.files):
            if self._cancel:
                break
            self.file_started.emit(i, src.name)
            self.progress.emit(0.0, i / total * 100)
            self.log.emit(f"[{i + 1}/{total}] {src.name}")

            info = core.probe(src)
            if info is None:
                fail(i, "ffprobe konnte die Datei nicht lesen – übersprungen.")
                continue
            if info.video is None:
                fail(i, "kein Videostream gefunden – übersprungen.")
                continue
            if info.skipped_subtitle:
                self.log.emit(f"  Hinweis: forced Untertitel ist {info.skipped_subtitle} (Bild-Untertitel) – "
                              "kann nicht in MP4 übernommen werden, wird ausgelassen.")

            # Sample: Abschnitt prüfen
            enc_duration = info.duration
            if s.is_sample:
                if info.duration > 0 and s.sample_start >= info.duration:
                    fail(i, f"Startzeit {core.seconds_to_hms(s.sample_start)} liegt hinter dem Filmende "
                            f"({core.seconds_to_hms(info.duration)}) – übersprungen.")
                    continue
                enc_duration = (min(core.SAMPLE_SECONDS, info.duration - s.sample_start)
                                if info.duration > 0 else core.SAMPLE_SECONDS)
                self.log.emit(f"  Sample: {enc_duration:.0f} Sek. ab {core.seconds_to_hms(s.sample_start)}")

            # HDR
            do_tonemap = s.hdr_to_sdr and info.hdr_kind is not None
            if do_tonemap and tonemapper is None:
                tonemapper = core.detect_tonemapper()
                self.log.emit("  Tonemapping-Methode: libplacebo (GPU)" if tonemapper == "libplacebo"
                              else "  Tonemapping-Methode: zscale + tonemap (CPU) – libplacebo/Vulkan nicht verfügbar")
            if do_tonemap:
                if info.is_dovi_p5 and tonemapper != "libplacebo":
                    fail(i, f"{info.hdr_kind} kann ohne libplacebo nicht korrekt nach SDR umgewandelt werden "
                            "– übersprungen.")
                    continue
                self.log.emit(f"  HDR erkannt: {info.hdr_kind} → wird nach SDR (BT.709) umgewandelt")
            elif info.hdr_kind:
                note = "" if s.ten_bit else " (8 Bit – für HDR wird 10-Bit empfohlen)"
                self.log.emit(f"  Hinweis: HDR erkannt ({info.hdr_kind}), HDR → SDR ist aus – HDR bleibt erhalten{note}.")
                if info.is_dovi_p5:
                    self.log.emit("  ACHTUNG: Dolby Vision Profil 5 ohne Tonemapping ergibt falsche Farben (grün/lila).")
            elif s.hdr_to_sdr:
                self.log.emit("  SDR-Quelle – kein Tonemapping nötig.")

            # Schwarze Balken
            crop = None
            if s.remove_bars:
                self.log.emit(f"  Schwarze Balken: suche an {core.CROP_SAMPLES} Stellen im Film …")
                crop = core.detect_crop(src, info, should_cancel=lambda: self._cancel)
                if self._cancel:
                    break
                if crop:
                    self.log.emit(f"  Schwarze Balken erkannt: {core.describe_crop(crop, info.width, info.height)}")
                else:
                    self.log.emit("  Keine schwarzen Balken gefunden.")
            dims = core.output_dimensions(info, s.width, crop)
            if dims:
                self.log.emit(f"  Ausgabe: {dims[0]} × {dims[1]}")

            # Ton
            if info.surround:
                layout = f"{info.audio_layout} ({info.audio_channels} Kanäle)"
                if s.stereo:
                    self.log.emit(f"  Ton: {layout} → Stereo, {core.STEREO_BITRATE} kbit/s")
                else:
                    self.log.emit(f"  Ton: {layout} bleibt erhalten, {core.audio_bitrate(info.audio_channels)} kbit/s")

            out = core.output_path(src, s, do_tonemap, taken)
            taken.add(str(out).lower())
            args = core.build_ffmpeg_args(src, out, info, s, tonemapper, enc_duration, crop)
            self.log.emit("  ffmpeg " + subprocess.list2cmdline(args))

            try:
                returncode, tail, avg_fps = self._run_ffmpeg(args, enc_duration, i, total)
            except OSError as e:
                fail(i, f"ffmpeg konnte nicht gestartet werden (im PATH?): {e}")
                break

            if self._cancel:
                out.unlink(missing_ok=True)
                self.log.emit("  Abgebrochen – unvollständige Ausgabedatei entfernt.")
                break
            if returncode != 0:
                self.log.emit(f"  ffmpeg ExitCode {returncode} – letzte Ausgabe:")
                for line in tail:
                    self.log.emit(f"    {line}")
                out.unlink(missing_ok=True)
                fail(i, "Encoding fehlgeschlagen – Datei übersprungen.")
                continue

            ok += 1
            self.progress.emit(100.0, (i + 1) / total * 100)
            self.file_finished.emit(i, self._result(src, out, info.duration, enc_duration))
            result_text = self._result_text(src, out, info.duration, enc_duration, avg_fps)
            self.log.emit(f"  Fertig » {out}  ({result_text})")
            self.log.emit("")

        if self._cancel:
            self.log.emit("» Abgebrochen durch Benutzer.")
        else:
            self.progress.emit(100.0, 100.0)
            self.log.emit(f"=== Fertig: {ok} erfolgreich, {failed} fehlgeschlagen ===")
        self.finished.emit(ok, failed, self._cancel)

    # ------------------------------------------------------------------ ffmpeg

    def _run_ffmpeg(self, args: list[str], enc_duration: float, index: int, total: int
                    ) -> tuple[int, list[str], float]:
        proc = subprocess.Popen(["ffmpeg", *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                creationflags=core.CREATE_NO_WINDOW)
        self._proc = proc
        if self._cancel:  # Abbruch kam zwischen Start und Zuweisung
            proc.kill()

        # stderr parallel leeren (sonst kann ffmpeg bei vollem Puffer hängen); letzte Zeilen für Fehler merken
        tail: deque[str] = deque(maxlen=12)
        reader = threading.Thread(target=lambda: [tail.append(l.rstrip()) for l in proc.stderr if l.strip()],
                                  daemon=True)
        reader.start()

        # Aktuelle FPS aus dem Bildzähler über ca. 1 Sekunde; ffmpegs fps= ist der Durchschnitt (fürs Log)
        fps_started = False
        last_frames, last_t = 0, 0.0
        avg_fps = 0.0
        block: dict[str, str] = {}
        for line in proc.stdout:
            key, _, value = line.strip().partition("=")
            if key != "progress":
                block[key] = value
                continue
            # Ein Fortschrittsblock ist komplett
            out_us = block.get("out_time_us") or block.get("out_time_ms") or ""
            if out_us.isdigit() and enc_duration > 0:
                file_pct = min(100.0, int(out_us) / 1e6 / enc_duration * 100)
                self.progress.emit(file_pct, min(100.0, (index + file_pct / 100) / total * 100))
            frames = int(block["frame"]) if block.get("frame", "").isdigit() else 0
            now = time.monotonic()
            if not fps_started:
                if frames > 0:  # Messung erst ab dem ersten Bild, sonst zählt die Startzeit mit
                    fps_started, last_frames, last_t = True, frames, now
            elif now - last_t >= 1.0:
                self.fps.emit((frames - last_frames) / (now - last_t))
                last_frames, last_t = frames, now
            try:
                avg_fps = float(block.get("fps") or avg_fps)
            except ValueError:
                pass
            block = {}

        proc.wait()
        reader.join(timeout=2)
        self._proc = None
        self.fps.emit(0.0)
        return proc.returncode, list(tail), avg_fps

    # ------------------------------------------------------------------ Ergebnis

    def _sizes(self, src: Path, out: Path, duration: float, enc_duration: float
               ) -> tuple[float, int, float]:
        """(Vergleichsgröße, Ausgabegröße, hochgerechnete Gesamtgröße bei Samples)."""
        out_size = out.stat().st_size if out.exists() else 0
        ref_size = float(src.stat().st_size)
        full_est = 0.0
        if self.settings.is_sample and duration > 0 and enc_duration > 0:
            ref_size *= enc_duration / duration    # gleich langer Abschnitt der Quelle (geschätzt)
            full_est = out_size * duration / enc_duration
        return ref_size, out_size, full_est

    def _result(self, src: Path, out: Path, duration: float, enc_duration: float) -> dict:
        ref_size, out_size, full_est = self._sizes(src, out, duration, enc_duration)
        return {"status": "ok", "src_size": ref_size, "out_size": out_size, "out_path": str(out),
                "is_sample": self.settings.is_sample, "full_est": full_est}

    def _result_text(self, src: Path, out: Path, duration: float, enc_duration: float, avg_fps: float) -> str:
        ref_size, out_size, full_est = self._sizes(src, out, duration, enc_duration)
        parts = [core.format_size(out_size)]
        pct = core.saving_percent(ref_size, out_size)
        if pct is not None:
            text = f"{pct:.0f} % kleiner" if pct >= 0 else f"{-pct:.0f} % größer"
            parts.append(f"ca. {text} als derselbe Abschnitt der Quelle" if self.settings.is_sample else text)
        if full_est:
            parts.append(f"ganzer Film ca. {core.format_size(full_est)}")
        if avg_fps:
            parts.append(f"Ø {avg_fps:.0f} fps")
        return "; ".join(parts)
