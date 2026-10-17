"""Tests für die GUI-freie Encoder-Logik (python -m pytest tests)."""
from pathlib import Path

import pytest

from av1studio import core


def stream(index, codec_type, **extra):
    return {"index": index, "codec_type": codec_type, **extra}


def probe_data(streams, duration="5449.6"):
    return {"format": {"duration": duration}, "streams": streams}


# --------------------------------------------------------------------------- analyze

def test_analyze_skips_cover_art_and_picks_first_audio():
    info = core.analyze(probe_data([
        stream(0, "video", codec_name="mjpeg", disposition={"attached_pic": 1}),
        stream(1, "video", codec_name="h264"),
        stream(2, "audio", codec_name="aac"),
        stream(3, "audio", codec_name="ac3"),
    ]))
    assert info.video["index"] == 1
    assert info.audio["index"] == 2
    assert info.duration == pytest.approx(5449.6)
    assert info.hdr_kind is None


def test_analyze_keeps_forced_text_subtitle_and_skips_image_subtitle():
    text = core.analyze(probe_data([stream(0, "video"), stream(1, "subtitle", codec_name="subrip",
                                                                disposition={"forced": 1})]))
    assert text.subtitle["index"] == 1 and text.skipped_subtitle is None
    pgs = core.analyze(probe_data([stream(0, "video"), stream(1, "subtitle", codec_name="hdmv_pgs_subtitle",
                                                               disposition={"forced": 1})]))
    assert pgs.subtitle is None and pgs.skipped_subtitle == "hdmv_pgs_subtitle"


@pytest.mark.parametrize("video, kind, p5", [
    ({"color_transfer": "smpte2084"}, "HDR10 (PQ)", False),
    ({"color_transfer": "arib-std-b67"}, "HLG", False),
    ({"color_transfer": "bt709"}, None, False),
    ({"color_transfer": "smpte2084", "side_data_list": [
        {"side_data_type": "DOVI configuration record", "dv_profile": 8, "dv_bl_signal_compatibility_id": 1}]},
     "Dolby Vision Profil 8", False),
    ({"side_data_list": [
        {"side_data_type": "DOVI configuration record", "dv_profile": 5, "dv_bl_signal_compatibility_id": 0}]},
     "Dolby Vision Profil 5", True),
])
def test_analyze_hdr_detection(video, kind, p5):
    info = core.analyze(probe_data([stream(0, "video", **video)]))
    assert info.hdr_kind == kind
    assert info.is_dovi_p5 is p5


# --------------------------------------------------------------------------- Dateinamen

@pytest.mark.parametrize("base, width, tonemap, expected", [
    ("Film.2023.1080p.x265", 1280, False, "Film.2023.720p"),
    ("Film.2160p.HDR.DV.x265", 1920, True, "Film.1080p"),
    ("Film.2160p.HDR.DV.x265", 1920, False, "Film.1080p.HDR.DV"),
    ("Film HLG 2160p", 1280, True, "Film 720p"),
    ("Serie.720p.h264", 960, False, "Serie.540p"),
    ("DVD-Rip", 1280, True, "DVD-Rip"),   # „DV“ in einem Wort bleibt stehen
])
def test_clean_name(base, width, tonemap, expected):
    assert core.clean_name(base, width, tonemap) == expected


def test_output_path_full_and_sample(tmp_path):
    src = tmp_path / "Film 1080p.mkv"
    full = core.output_path(src, core.EncodeSettings(width=1280), tonemap=False)
    sample = core.output_path(src, core.EncodeSettings(width=1280, sample_start=600), tonemap=False)
    assert full == tmp_path / "Film 720p_AV1.mp4"
    assert sample == tmp_path / "Film 720p_AV1_Sample_00-10-00.mp4"
    assert core.is_output_file(full) and core.is_output_file(sample)
    assert not core.is_output_file(src)


def test_output_path_custom_folder(tmp_path):
    src = tmp_path / "Quelle" / "Film 1080p.mkv"
    target = tmp_path / "Ziel"
    s = core.EncodeSettings(width=1280, output_dir=str(target))
    assert core.output_path(src, s, tonemap=False) == target / "Film 720p_AV1.mp4"


def test_output_path_numbers_name_clashes_in_common_folder(tmp_path):
    target = tmp_path / "Ziel"
    s = core.EncodeSettings(width=1280, output_dir=str(target))
    taken: set[str] = set()
    paths = []
    for folder in ("Serie A", "Serie B", "Serie C"):   # gleicher Dateiname in drei Ordnern
        out = core.output_path(tmp_path / folder / "S01E01 1080p.mkv", s, False, taken)
        taken.add(str(out).lower())
        paths.append(out.name)
    assert paths == ["S01E01 720p_AV1.mp4", "S01E01 720p (2)_AV1.mp4", "S01E01 720p (3)_AV1.mp4"]
    # Nummerierte Ausgaben werden beim nächsten Scan trotzdem als Ausgabe erkannt
    assert all(core.is_output_file(Path(p)) for p in paths)


def test_output_path_sample_numbering_keeps_sample_suffix(tmp_path):
    s = core.EncodeSettings(output_dir=str(tmp_path), sample_start=90)
    first = core.output_path(tmp_path / "a" / "Film.mkv", s, False, set())
    second = core.output_path(tmp_path / "b" / "Film.mkv", s, False, {str(first).lower()})
    assert second.name == "Film (2)_AV1_Sample_00-01-30.mp4" and core.is_output_file(second)


@pytest.mark.parametrize("text, expected", [
    ("00:10:00", 600), ("10:00", 600), ("90", 90), ("1:02:03.5", 3723.5), ("1,5", 1.5), (" 0:01:20 ", 80),
    ("abc", None), ("10:", None), ("", None), ("1:2:3:4", None),
])
def test_parse_time(text, expected):
    assert core.parse_time(text) == expected


def test_format_size_german():
    assert core.format_size(1.5 * 1024 ** 3) == "1,50 GB"
    assert core.format_size(456 * 1024 ** 2) == "456 MB"
    assert core.format_size(9.5 * 1024 ** 2) == "9,5 MB"
    assert core.format_size(820 * 1024) == "820 KB"


def test_saving_percent():
    assert core.saving_percent(100, 25) == pytest.approx(75)
    assert core.saving_percent(100, 150) == pytest.approx(-50)
    assert core.saving_percent(0, 10) is None


# --------------------------------------------------------------------------- ffmpeg-Argumente

def _info(**kw):
    streams = [stream(0, "video", **kw), stream(1, "audio"),
               stream(2, "subtitle", codec_name="subrip", disposition={"forced": 1})]
    return core.analyze(probe_data(streams))


def _arg_after(args, flag):
    return args[args.index(flag) + 1]


def test_args_cq_high_profile_output_last():
    src, out = Path("C:/in/a.mkv"), Path("C:/in/a_AV1.mp4")
    args = core.build_ffmpeg_args(src, out, _info(), core.EncodeSettings(), None, 100)
    assert args[-1] == str(out)
    assert args[-2] == "-y"
    assert _arg_after(args, "-cq") == "28" and _arg_after(args, "-rc") == "vbr"
    assert _arg_after(args, "-preset") == "p7" and _arg_after(args, "-pix_fmt") == "yuv420p"
    assert _arg_after(args, "-vf") == "scale=1280:-2"
    # Untertitel-Optionen stehen vor der Ausgabedatei
    assert args.index("-c:s") < args.index(str(out))
    assert "-ss" not in args


def test_args_bitrate_max_profile_10bit():
    s = core.EncodeSettings(use_bitrate=True, bitrate_mbit=12, quality="max", ten_bit=True)
    args = core.build_ffmpeg_args(Path("a.mkv"), Path("b.mp4"), _info(), s, None, 100)
    assert _arg_after(args, "-b:v") == "12M" and "-cq" not in args
    assert _arg_after(args, "-tune") == "uhq" and _arg_after(args, "-multipass") == "fullres"
    assert _arg_after(args, "-pix_fmt") == "p010le"


@pytest.mark.parametrize("tonemapper, fragment", [("libplacebo", "libplacebo=w=1920"), ("zscale", "tonemap=hable")])
def test_args_tonemapping_10bit(tonemapper, fragment):
    s = core.EncodeSettings(width=1920, hdr_to_sdr=True, ten_bit=True)
    args = core.build_ffmpeg_args(Path("a.mkv"), Path("b.mp4"), _info(color_transfer="smpte2084"), s, tonemapper, 100)
    vf = _arg_after(args, "-vf")
    assert fragment in vf and vf.endswith("format=yuv420p10le")
    assert _arg_after(args, "-color_trc") == "bt709"


def test_args_hdr_switch_has_no_effect_on_sdr():
    s = core.EncodeSettings(hdr_to_sdr=True)
    args = core.build_ffmpeg_args(Path("a.mkv"), Path("b.mp4"), _info(color_transfer="bt709"), s, "libplacebo", 100)
    assert _arg_after(args, "-vf") == "scale=1280:-2" and "-color_trc" not in args


# --------------------------------------------------------------------------- Schwarze Balken & Auflösung

def test_combine_crops_takes_union_of_samples():
    # Dunkle Szene meldet zu kleinen Bereich -> Vereinigung behält das echte Bild
    boxes = [(1920, 800, 0, 140), (1600, 600, 160, 240), (1920, 800, 0, 140)]
    assert core.combine_crops(boxes, 1920, 1080) == (1920, 800, 0, 140)


def test_combine_crops_ignores_thin_borders_and_garbage():
    assert core.combine_crops([(1916, 1076, 2, 2)], 1920, 1080) is None          # < 1 % Rand
    assert core.combine_crops([(1916, 800, 2, 140)], 1920, 1080) == (1920, 800, 0, 140)
    assert core.combine_crops([(64, 32, 900, 500)], 1920, 1080) is None          # schwarzes Bild
    assert core.combine_crops([], 1920, 1080) is None


def test_combine_crops_pillarbox_even_values():
    assert core.combine_crops([(1441, 1080, 239, 0)], 1920, 1080) == (1442, 1080, 238, 0)


def _video(width, height, sar="1:1"):
    return core.analyze(probe_data([stream(0, "video", width=width, height=height, sample_aspect_ratio=sar)]))


@pytest.mark.parametrize("w, h, sar, box, crop, expected", [
    (1920, 1080, "1:1", 1280, None, (1280, 720)),
    (1920, 1080, "1:1", 1280, (1920, 800, 0, 140), (1280, 534)),    # 2,40:1 ohne Balken
    (1920, 1080, "1:1", 1280, (1440, 1080, 240, 0), (960, 720)),    # 4:3 ohne Seitenbalken
    (1280, 720, "1:1", 1920, None, (1280, 720)),                    # kein Hochskalieren
    (720, 576, "64:45", 1280, None, (1024, 576)),                   # anamorphe PAL-DVD
    (3840, 2160, "1:1", 1920, (3840, 1600, 0, 280), (1920, 800)),
])
def test_output_dimensions(w, h, sar, box, crop, expected):
    assert core.output_dimensions(_video(w, h, sar), box, crop) == expected


def test_describe_crop():
    assert core.describe_crop((1920, 800, 0, 140), 1920, 1080) == "1920 × 1080 → 1920 × 800 (oben 140 px, unten 140 px)"


def test_args_crop_then_scale_then_square_pixels():
    s = core.EncodeSettings(width=1280, remove_bars=True)
    args = core.build_ffmpeg_args(Path("a.mkv"), Path("b.mp4"), _video(1920, 1080), s, None, 100,
                                  crop=(1920, 800, 0, 140))
    assert _arg_after(args, "-vf") == "crop=1920:800:0:140,scale=1280:534,setsar=1"


def test_args_crop_with_libplacebo():
    info = core.analyze(probe_data([stream(0, "video", width=3840, height=2160, color_transfer="smpte2084")]))
    s = core.EncodeSettings(width=1920, hdr_to_sdr=True)
    vf = _arg_after(core.build_ffmpeg_args(Path("a.mkv"), Path("b.mp4"), info, s, "libplacebo", 100,
                                           crop=(3840, 1600, 0, 280)), "-vf")
    assert vf.startswith("crop=3840:1600:0:280,libplacebo=w=1920:h=800:")
    assert vf.endswith(",setsar=1")


# --------------------------------------------------------------------------- Ton

def _audio_info(channels):
    return core.analyze(probe_data([stream(0, "video", width=1920, height=1080),
                                    stream(1, "audio", channels=channels, channel_layout="5.1(side)")]))


def test_args_surround_to_stereo():
    args = core.build_ffmpeg_args(Path("a.mkv"), Path("b.mp4"), _audio_info(6),
                                  core.EncodeSettings(stereo=True), None, 100)
    assert _arg_after(args, "-ac") == "2" and _arg_after(args, "-b:a") == "192k"


def test_args_surround_kept_with_matching_bitrate():
    args = core.build_ffmpeg_args(Path("a.mkv"), Path("b.mp4"), _audio_info(6), core.EncodeSettings(), None, 100)
    assert "-ac" not in args and _arg_after(args, "-b:a") == "384k"
    assert core.audio_bitrate(8) == 512 and core.audio_bitrate(2) == 192 and core.audio_bitrate(1) == 192


def test_args_stereo_switch_leaves_stereo_source_alone():
    args = core.build_ffmpeg_args(Path("a.mkv"), Path("b.mp4"), _audio_info(2),
                                  core.EncodeSettings(stereo=True), None, 100)
    assert "-ac" not in args and _arg_after(args, "-b:a") == "192k"


# --------------------------------------------------------------------------- HEVC

def test_hevc_output_name_and_rescan_detection(tmp_path):
    src = tmp_path / "Film.2023.1080p.x265.mkv"
    full = core.output_path(src, core.EncodeSettings(codec="hevc", width=1280), tonemap=False)
    sample = core.output_path(src, core.EncodeSettings(codec="hevc", sample_start=60), tonemap=False)
    assert full.name == "Film.2023.720p_HEVC.mp4"
    assert sample.name == "Film.2023.720p_HEVC_Sample_00-01-00.mp4"
    assert core.is_output_file(full) and core.is_output_file(sample)


def test_args_hevc_8bit():
    args = core.build_ffmpeg_args(Path("a.mkv"), Path("b.mp4"), _info(), core.EncodeSettings(codec="hevc"), None, 100)
    assert _arg_after(args, "-c:v") == "hevc_nvenc"
    assert _arg_after(args, "-cq") == "24"
    assert _arg_after(args, "-profile:v") == "main" and _arg_after(args, "-pix_fmt") == "yuv420p"
    assert _arg_after(args, "-tag:v") == "hvc1"
    assert args[-1] == "b.mp4"


def test_args_hevc_10bit_bitrate():
    s = core.EncodeSettings(codec="hevc", ten_bit=True, use_bitrate=True, bitrate_mbit=8, quality="max")
    args = core.build_ffmpeg_args(Path("a.mkv"), Path("b.mp4"), _info(), s, None, 100)
    assert _arg_after(args, "-profile:v") == "main10" and _arg_after(args, "-pix_fmt") == "p010le"
    assert _arg_after(args, "-b:v") == "8M" and "-cq" not in args
    assert _arg_after(args, "-tune") == "uhq"


def test_args_av1_has_no_hevc_options():
    args = core.build_ffmpeg_args(Path("a.mkv"), Path("b.mp4"), _info(), core.EncodeSettings(), None, 100)
    assert _arg_after(args, "-c:v") == "av1_nvenc"
    assert "-profile:v" not in args and "-tag:v" not in args


def test_args_sample_seeks_before_input():
    s = core.EncodeSettings(sample_start=600)
    args = core.build_ffmpeg_args(Path("a.mkv"), Path("b.mp4"), _info(), s, None, 30)
    assert args.index("-ss") < args.index("-i")
    assert _arg_after(args, "-ss") == "600.000" and _arg_after(args, "-t") == "30.000"
