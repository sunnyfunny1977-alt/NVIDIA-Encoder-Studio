"""Encoder-Logik ohne GUI: ffprobe-Auswertung, Dateinamen und ffmpeg-Argumente.

Alles hier ist frei von Qt, damit es sich einzeln testen lässt (siehe tests/test_core.py).
"""
from __future__ import annotations

import functools
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Unter Windows keine Konsolenfenster für ffmpeg/ffprobe aufpoppen lassen
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

VIDEO_EXTENSIONS = {".mkv", ".mp4"}
# Bereits erzeugte Ausgaben (…_AV1.mp4, …_HEVC.mp4, …_AV1_Sample_00-10-00.mp4) nicht erneut encodieren
OUTPUT_NAME_RE = re.compile(r"_(AV1|HEVC)(_Sample_[\d-]+)?$", re.IGNORECASE)


@dataclass(frozen=True)
class Codec:
    label: str
    encoder: str        # ffmpeg-Encoder
    tag: str            # Dateinamens-Suffix: Film_AV1.mp4 / Film_HEVC.mp4
    cq: int             # konstante Qualität; die Skalen unterscheiden sich (AV1 0–63, HEVC 0–51)
    extra: tuple = ()   # zusätzliche Ausgabe-Optionen


CODECS: dict[str, Codec] = {
    "av1": Codec("AV1", "av1_nvenc", "AV1", 28),
    # hvc1-Tag: HEVC in MP4 auch auf Apple-Geräten und Smart-TVs abspielbar
    "hevc": Codec("HEVC (H.265)", "hevc_nvenc", "HEVC", 24, ("-tag:v", "hvc1")),
}
DEFAULT_CODEC = "av1"
# Text-Untertitel lassen sich nach mov_text (MP4) wandeln, Bild-Untertitel (PGS/VobSub) nicht
TEXT_SUB_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"}

SAMPLE_SECONDS = 30
BITRATE_MIN, BITRATE_MAX, BITRATE_DEFAULT = 1, 20, 4

# Zielauflösung = Rahmen, in den das Bild passt (Seitenverhältnis bleibt, kein Hochskalieren)
RESOLUTIONS = [
    (960, "540p  ·  max. 960 × 540"),
    (1280, "720p  ·  max. 1280 × 720"),
    (1920, "1080p  ·  max. 1920 × 1080"),
]
BOX_HEIGHT = {960: 540, 1280: 720, 1920: 1080}
DEFAULT_WIDTH = 1280

# Schwarze Balken: Stichproben über den Film verteilt
CROP_SAMPLES = 8
CROP_SAMPLE_SECONDS = 2.0
CROP_MIN_BAR = 0.01        # Ränder unter 1 % der Bildgröße ignorieren
CROP_RE = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")

# Ton: Stereo 192 kbit/s, Mehrkanal 64 kbit/s pro Kanal (5.1 = 384, 7.1 = 512)
STEREO_BITRATE = 192
SURROUND_BITRATE_PER_CHANNEL = 64

# Qualitätsprofile von av1_nvenc – unabhängig von CQ/Bitrate
_AQ = ["-spatial-aq", "1", "-aq-strength", "8", "-temporal-aq", "1"]
QUALITY_PROFILES: dict[str, tuple[str, list[str], str]] = {
    "fast": ("Schnell", ["-preset", "p4", "-tune", "hq"],
             "p4 · hq – am schnellsten"),
    "balanced": ("Ausgewogen", ["-preset", "p6", "-tune", "hq", "-spatial-aq", "1", "-temporal-aq", "1"],
                 "p6 · hq · Spatial/Temporal AQ"),
    "high": ("Hoch (Standard)", ["-preset", "p7", "-tune", "hq", *_AQ],
             "p7 · hq · Spatial/Temporal AQ"),
    "veryhigh": ("Sehr hoch", ["-preset", "p7", "-tune", "hq", "-multipass", "qres",
                               "-rc-lookahead", "32", "-lookahead_level", "3", *_AQ],
                 "p7 · hq · AQ · Multipass (¼ Auflösung) · Lookahead – etwas langsamer"),
    "max": ("Maximum (langsam)", ["-preset", "p7", "-tune", "uhq", "-multipass", "fullres",
                                  "-rc-lookahead", "32", "-lookahead_level", "3", "-tf_level", "4", *_AQ],
            "p7 · uhq · AQ · Multipass (volle Auflösung) · Lookahead · temporaler Filter – deutlich langsamer"),
}
DEFAULT_QUALITY = "high"


# --------------------------------------------------------------------------- Einstellungen

@dataclass
class EncodeSettings:
    codec: str = DEFAULT_CODEC         # Schlüssel aus CODECS
    width: int = DEFAULT_WIDTH
    use_bitrate: bool = False
    bitrate_mbit: int = BITRATE_DEFAULT
    quality: str = DEFAULT_QUALITY
    ten_bit: bool = False
    hdr_to_sdr: bool = False
    sample_start: float | None = None  # Sekunden; None = ganzer Film
    remove_bars: bool = False          # schwarze Balken erkennen und wegschneiden
    stereo: bool = False               # Mehrkanal-Ton auf Stereo heruntermischen
    output_dir: str | None = None      # None = jeweiliger Quellordner, sonst gemeinsamer Zielordner

    @property
    def is_sample(self) -> bool:
        return self.sample_start is not None

    @property
    def codec_info(self) -> Codec:
        return CODECS.get(self.codec, CODECS[DEFAULT_CODEC])


# --------------------------------------------------------------------------- ffprobe

@dataclass
class ProbeInfo:
    duration: float
    video: dict | None
    audio: dict | None
    subtitle: dict | None          # forced Text-Untertitel, der übernommen wird
    skipped_subtitle: str | None   # Codec eines forced Bild-Untertitels, der ausgelassen wird
    hdr_kind: str | None           # "HDR10 (PQ)", "HLG", "Dolby Vision Profil 8", … oder None
    transfer: str
    is_dovi_p5: bool               # Dolby Vision ohne HDR10-kompatible Basisebene
    width: int = 0
    height: int = 0
    sar: float = 1.0               # Pixel-Seitenverhältnis (anamorphe DVDs z. B. 64:45)
    audio_channels: int = 0
    audio_layout: str = ""

    @property
    def surround(self) -> bool:
        return self.audio_channels > 2


def run_quiet(args: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW, timeout=timeout)


def probe(path: Path) -> ProbeInfo | None:
    """ffprobe ausführen; None, wenn die Datei nicht lesbar ist."""
    try:
        r = run_quiet(["ffprobe", "-v", "quiet", "-print_format", "json",
                       "-show_format", "-show_streams", str(path)], timeout=120)
        data = json.loads(r.stdout or "{}")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    if not data.get("streams"):
        return None
    return analyze(data)


def analyze(data: dict) -> ProbeInfo:
    """ffprobe-JSON auswerten: passende Streams wählen und HDR erkennen."""
    streams = data.get("streams") or []
    try:
        duration = float((data.get("format") or {}).get("duration") or 0)
    except ValueError:
        duration = 0.0

    def disposition(s: dict, key: str) -> int:
        return int((s.get("disposition") or {}).get(key) or 0)

    # Erster echter Videostream – kein eingebettetes Cover-Bild
    video = next((s for s in streams
                  if s.get("codec_type") == "video" and disposition(s, "attached_pic") != 1), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    subtitle = next((s for s in streams
                     if s.get("codec_type") == "subtitle" and disposition(s, "forced") == 1), None)
    skipped_subtitle = None
    if subtitle and subtitle.get("codec_name") not in TEXT_SUB_CODECS:
        skipped_subtitle = subtitle.get("codec_name") or "unbekannt"
        subtitle = None

    transfer = str((video or {}).get("color_transfer") or "")
    dovi = next((sd for sd in (video or {}).get("side_data_list") or []
                 if "DOVI" in str(sd.get("side_data_type", "")).upper()), None)
    if dovi:
        hdr_kind = f"Dolby Vision Profil {dovi.get('dv_profile', '?')}"
    elif transfer == "smpte2084":
        hdr_kind = "HDR10 (PQ)"
    elif transfer == "arib-std-b67":
        hdr_kind = "HLG"
    else:
        hdr_kind = None
    is_dovi_p5 = bool(dovi) and int(dovi.get("dv_bl_signal_compatibility_id") or 0) == 0

    width = int((video or {}).get("width") or 0)
    height = int((video or {}).get("height") or 0)
    sar = 1.0
    m = re.fullmatch(r"(\d+):(\d+)", str((video or {}).get("sample_aspect_ratio") or ""))
    if m and int(m.group(1)) > 0 and int(m.group(2)) > 0:
        sar = int(m.group(1)) / int(m.group(2))
    channels = int((audio or {}).get("channels") or 0)
    layout = str((audio or {}).get("channel_layout") or (f"{channels} Kanäle" if channels else ""))

    return ProbeInfo(duration, video, audio, subtitle, skipped_subtitle, hdr_kind, transfer, is_dovi_p5,
                     width, height, sar, channels, layout)


@functools.lru_cache(maxsize=1)
def detect_tonemapper() -> str:
    """'libplacebo' (GPU/Vulkan, kann Dolby Vision) oder 'zscale' (CPU) als Fallback."""
    try:
        r = run_quiet(["ffmpeg", "-hide_banner", "-v", "error", "-f", "lavfi", "-i",
                       "color=c=gray:s=64x64:d=0.1", "-vf",
                       "libplacebo=tonemapping=bt.2390:colorspace=bt709:color_primaries=bt709:"
                       "color_trc=bt709:format=yuv420p", "-f", "null", "-"], timeout=60)
        return "libplacebo" if r.returncode == 0 else "zscale"
    except (OSError, subprocess.SubprocessError):
        return "zscale"


# --------------------------------------------------------------------------- Schwarze Balken

Crop = tuple[int, int, int, int]   # (Breite, Höhe, x, y) wie bei ffmpeg crop=w:h:x:y


def detect_crop(path: Path, info: ProbeInfo, should_cancel=lambda: False) -> Crop | None:
    """Schwarze Balken per cropdetect an mehreren Stellen des Films suchen.

    None = keine Balken (oder nicht sicher erkennbar).
    """
    if not info.video or info.width <= 0 or info.height <= 0:
        return None
    if info.duration > CROP_SAMPLE_SECONDS * 2:
        positions = [info.duration * (i + 1) / (CROP_SAMPLES + 1) for i in range(CROP_SAMPLES)]
    else:
        positions = [0.0]
    boxes: list[Crop] = []
    for pos in positions:
        if should_cancel():
            return None
        try:
            r = run_quiet(["ffmpeg", "-hide_banner", "-nostdin", "-hwaccel", "cuda",
                           "-ss", f"{pos:.3f}", "-t", f"{CROP_SAMPLE_SECONDS}", "-i", str(path),
                           "-map", f"0:{info.video['index']}", "-an", "-sn",
                           # limit als Anteil -> passt für 8 und 10 Bit; reset=0 = größte Fläche im Abschnitt
                           "-vf", "cropdetect=limit=0.1:round=2:reset=0", "-f", "null", "-"], timeout=180)
        except (OSError, subprocess.SubprocessError):
            continue
        found = CROP_RE.findall(r.stderr)
        if found:
            boxes.append(tuple(int(v) for v in found[-1]))
    return combine_crops(boxes, info.width, info.height)


def combine_crops(boxes: list[Crop], width: int, height: int) -> Crop | None:
    """Stichproben vereinigen: größte gefundene Bildfläche.

    So schneidet eine dunkle Szene kein echtes Bild weg. Sehr dünne Ränder werden ignoriert.
    """
    # Unbrauchbare Werte (z. B. aus komplett schwarzen Bildern) verwerfen
    boxes = [b for b in boxes if b[0] >= width * 0.25 and b[1] >= height * 0.25]
    if not boxes:
        return None
    x1 = min(b[2] for b in boxes)
    y1 = min(b[3] for b in boxes)
    x2 = max(b[2] + b[0] for b in boxes)
    y2 = max(b[3] + b[1] for b in boxes)
    # Gerade Werte, im Zweifel etwas mehr Bild behalten
    x1, y1 = x1 // 2 * 2, y1 // 2 * 2
    x2, y2 = min(width, (x2 + 1) // 2 * 2), min(height, (y2 + 1) // 2 * 2)
    if width - (x2 - x1) < width * CROP_MIN_BAR:
        x1, x2 = 0, width
    if height - (y2 - y1) < height * CROP_MIN_BAR:
        y1, y2 = 0, height
    if (x2 - x1, y2 - y1) == (width, height):
        return None
    return x2 - x1, y2 - y1, x1, y1


def describe_crop(crop: Crop, width: int, height: int) -> str:
    w, h, x, y = crop
    parts = []
    if h < height:
        parts.append(f"oben {y} px, unten {height - h - y} px")
    if w < width:
        parts.append(f"links {x} px, rechts {width - w - x} px")
    return f"{width} × {height} → {w} × {h} ({'; '.join(parts)})"


def output_dimensions(info: ProbeInfo, box_width: int, crop: Crop | None = None) -> tuple[int, int] | None:
    """Ausgabegröße: passt in den Zielrahmen (z. B. 1280 × 720), Seitenverhältnis bleibt, kein Hochskalieren.

    Berücksichtigt einen Beschnitt und anamorphe Pixel (SAR). None, wenn die Quellgröße unbekannt ist.
    """
    if info.width <= 0 or info.height <= 0:
        return None
    w, h = (crop[0], crop[1]) if crop else (info.width, info.height)
    display_w = w * info.sar
    box_h = BOX_HEIGHT.get(box_width, round(box_width * 9 / 16))
    factor = min(box_width / display_w, box_h / h, 1.0)
    return max(2, round(display_w * factor / 2) * 2), max(2, round(h * factor / 2) * 2)


def audio_bitrate(channels: int) -> int:
    """kbit/s für AAC: Stereo 192, Mehrkanal 64 pro Kanal (höchstens 512)."""
    if channels <= 2:
        return STEREO_BITRATE
    return min(512, SURROUND_BITRATE_PER_CHANNEL * channels)


# --------------------------------------------------------------------------- Dateien

def is_output_file(path: Path) -> bool:
    return bool(OUTPUT_NAME_RE.search(path.stem))


def is_video_file(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def seconds_to_hms(seconds: float, sep: str = ":") -> str:
    s = int(seconds)
    return f"{s // 3600:02d}{sep}{s % 3600 // 60:02d}{sep}{s % 60:02d}"


def parse_time(text: str) -> float | None:
    """'hh:mm:ss', 'mm:ss' oder Sekunden -> Sekunden; None bei ungültiger Eingabe."""
    t = text.strip().replace(",", ".")
    if not re.fullmatch(r"\d+(:\d{1,2}){0,2}(\.\d+)?", t):
        return None
    seconds = 0.0
    for part in t.split(":"):
        seconds = seconds * 60 + float(part)
    return seconds


def clean_name(base: str, width: int, tonemap: bool) -> str:
    name = re.sub(r"(?i)x264|x265|h264|h265|hevc", "", base)
    if width == 960:
        name = re.sub(r"(?i)2160p|1080p|720p", "540p", name)
    elif width == 1280:
        name = re.sub(r"(?i)2160p|1080p", "720p", name)
    elif width == 1920:
        name = re.sub(r"(?i)2160p", "1080p", name)
    if tonemap:
        # HDR-Kennzeichnungen entfernen, die Ausgabe ist SDR
        name = re.sub(r"(?i)(?:(?<=^)|(?<=[.\s_-]))(HDR10\+|HDR10Plus|HDR10|HDR|DoVi|DV|HLG)(?=$|[.\s_-])",
                      "", name)
    name = re.sub(r"\.{2,}", ".", name)
    name = re.sub(r"\s{2,}", " ", name)
    name = re.sub(r"-{2,}", "-", name)
    return name.strip(". -")


def output_path(src: Path, settings: EncodeSettings, tonemap: bool, taken: set[str] | None = None) -> Path:
    """Ausgabe in den Quellordner oder den gemeinsamen Zielordner; Samples mit eigenem Namen.

    taken: in diesem Lauf schon vergebene Ausgabepfade (klein geschrieben). Gleichnamige Quellen aus
    verschiedenen Ordnern bekommen im gemeinsamen Zielordner eine Nummer: „Film (2)_AV1.mp4“.
    """
    folder = Path(settings.output_dir) if settings.output_dir else src.parent
    name = clean_name(src.stem, settings.width, tonemap)
    tag = settings.codec_info.tag
    suffix = f"_{tag}_Sample_{seconds_to_hms(settings.sample_start, '-')}" if settings.is_sample else f"_{tag}"
    candidate = folder / f"{name}{suffix}.mp4"
    n = 1
    while taken is not None and str(candidate).lower() in taken:
        n += 1
        candidate = folder / f"{name} ({n}){suffix}.mp4"
    return candidate


# --------------------------------------------------------------------------- ffmpeg-Argumente

def build_ffmpeg_args(src: Path, out: Path, info: ProbeInfo, s: EncodeSettings,
                      tonemapper: str | None, enc_duration: float, crop: Crop | None = None) -> list[str]:
    """ffmpeg-Argumente (ohne 'ffmpeg' selbst). Fortschritt kommt als key=value über stdout."""
    do_tonemap = s.hdr_to_sdr and info.hdr_kind is not None
    filter_fmt = "yuv420p10le" if s.ten_bit else "yuv420p"
    pix_fmt = "p010le" if s.ten_bit else "yuv420p"

    args = ["-hide_banner", "-nostdin", "-nostats", "-loglevel", "warning",
            "-progress", "pipe:1", "-stats_period", "0.5", "-hwaccel", "cuda"]
    if s.is_sample:
        # Schnelles Springen zur Startzeit (-ss vor -i), dann nur den Abschnitt lesen
        args += ["-ss", f"{s.sample_start:.3f}", "-t", f"{enc_duration:.3f}"]
    args += ["-i", str(src), "-map", f"0:{info.video['index']}"]
    if info.audio:
        args += ["-map", f"0:{info.audio['index']}"]
    if info.subtitle:
        args += ["-map", f"0:{info.subtitle['index']}"]

    # Videofilter: Balken wegschneiden -> skalieren (bei HDR -> SDR mit Tonemapping) -> quadratische Pixel
    dims = output_dimensions(info, s.width, crop)
    w_expr, h_expr = (str(dims[0]), str(dims[1])) if dims else (str(s.width), "-2")
    chain = [f"crop={crop[0]}:{crop[1]}:{crop[2]}:{crop[3]}"] if crop else []
    if do_tonemap and tonemapper == "libplacebo":
        chain.append(f"libplacebo=w={w_expr}:h={h_expr}:tonemapping=bt.2390:colorspace=bt709:"
                     f"color_primaries=bt709:color_trc=bt709:range=tv:format={filter_fmt}")
    elif do_tonemap:
        chain += [f"scale={w_expr}:{h_expr}",
                  f"zscale=tin={info.transfer}:min=bt2020nc:pin=bt2020:rin=tv:t=linear:npl=100",
                  "format=gbrpf32le", "zscale=p=bt709", "tonemap=tonemap=hable:desat=0",
                  "zscale=t=bt709:m=bt709:r=tv", f"format={filter_fmt}"]
    else:
        chain.append(f"scale={w_expr}:{h_expr}")
    if dims:
        chain.append("setsar=1")
    args += ["-vf", ",".join(chain)]

    # Qualitätsprofil + Ratensteuerung (CQ oder feste Bitrate)
    codec = s.codec_info
    quality_args = QUALITY_PROFILES.get(s.quality, QUALITY_PROFILES[DEFAULT_QUALITY])[1]
    args += ["-c:v", codec.encoder, *quality_args]
    if s.use_bitrate:
        args += ["-b:v", f"{s.bitrate_mbit}M"]
    else:
        args += ["-rc", "vbr", "-cq", str(codec.cq)]
    args += ["-pix_fmt", pix_fmt]
    if codec.encoder == "hevc_nvenc":
        args += ["-profile:v", "main10" if s.ten_bit else "main"]
    args += list(codec.extra)
    if do_tonemap:
        args += ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
                 "-color_range", "tv"]
    if info.audio:
        if s.stereo and info.surround:
            args += ["-c:a", "aac", "-b:a", f"{STEREO_BITRATE}k", "-ac", "2"]
        else:
            args += ["-c:a", "aac", "-b:a", f"{audio_bitrate(info.audio_channels)}k"]
    if info.subtitle:
        args += ["-c:s", "mov_text", "-disposition:s:0", "forced"]
    args += ["-y", str(out)]  # Ausgabedatei MUSS zuletzt stehen
    return args


# --------------------------------------------------------------------------- Formatierung

def format_size(num_bytes: float) -> str:
    """Deutsche Schreibweise: 1,23 GB · 456 MB · 9,5 MB · 820 KB."""
    def de(value: float, digits: int) -> str:
        return f"{value:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if num_bytes >= 1024 ** 3:
        return de(num_bytes / 1024 ** 3, 2) + " GB"
    if num_bytes >= 100 * 1024 ** 2:
        return de(num_bytes / 1024 ** 2, 0) + " MB"
    if num_bytes >= 1024 ** 2:
        return de(num_bytes / 1024 ** 2, 1) + " MB"
    return de(num_bytes / 1024, 0) + " KB"


def saving_percent(src_size: float, out_size: float) -> float | None:
    """Positiv = kleiner geworden, negativ = größer geworden."""
    if src_size <= 0:
        return None
    return (1 - out_size / src_size) * 100


# --------------------------------------------------------------------------- Systeminfo

def ffmpeg_status() -> tuple[bool, str, set[str]]:
    """(ok, Text, verfügbare Codec-Schlüssel) – ffmpeg/ffprobe vorhanden und NVENC-Encoder verfügbar?"""
    try:
        version = run_quiet(["ffmpeg", "-hide_banner", "-version"], timeout=20).stdout.split("\n")[0]
        encoders = run_quiet(["ffmpeg", "-hide_banner", "-encoders"], timeout=20).stdout
        run_quiet(["ffprobe", "-version"], timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False, "ffmpeg nicht gefunden", set()
    available = {key for key, c in CODECS.items() if re.search(rf"\b{c.encoder}\b", encoders)}
    if not available:
        return False, "ffmpeg ohne NVENC-Encoder", set()
    m = re.search(r"ffmpeg version (\S+)", version)
    ver = m.group(1) if m else ""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", ver)
    names = " · ".join(CODECS[k].tag for k in CODECS if k in available)
    return True, f"ffmpeg {m.group(1) if m else ver[:12]} · {names}", available


def gpu_status() -> tuple[bool, str]:
    try:
        out = run_quiet(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                        timeout=20).stdout.strip().splitlines()
        name, mem = [x.strip() for x in out[0].split(",")[:2]]
    except (OSError, subprocess.SubprocessError, IndexError, ValueError):
        return False, "Keine NVIDIA-GPU gefunden"
    name = re.sub(r"^NVIDIA\s+(GeForce\s+)?", "", name)
    return True, f"{name} · {round(int(mem) / 1024)} GB · NVENC"
