from datetime import datetime, timedelta, timezone

from config import Config

from highlight_detector import (
    compute_offset_seconds,
    video_time_to_activity_index,
    speed_at_video_time,
)


def test_config_defaults():
    c = Config()
    assert c.min_speed_kmh == 3.0
    assert c.cut_mode == "reencode"
    assert c.intro_duration == 7.0
    assert 0.0 <= c.original_audio_volume <= 1.0


def test_config_is_overridable():
    c = Config(min_speed_kmh=5.0, cut_mode="copy")
    assert c.min_speed_kmh == 5.0
    assert c.cut_mode == "copy"


def _speeds():
    # per-second speeds: 0..9 km/h ramp
    return [float(i) for i in range(10)]


def test_offset_positive_video_after_activity():
    activity = datetime(2026, 7, 6, 10, 0, 0, tzinfo=timezone.utc)
    video = activity + timedelta(seconds=5)
    assert compute_offset_seconds(video, activity) == 5.0


def test_offset_negative_video_before_activity():
    activity = datetime(2026, 7, 6, 10, 0, 5, tzinfo=timezone.utc)
    video = datetime(2026, 7, 6, 10, 0, 0, tzinfo=timezone.utc)
    assert compute_offset_seconds(video, activity) == -5.0


def test_video_time_maps_with_offset():
    # video started 5s after activity: video t=0 -> activity second 5
    idx = video_time_to_activity_index(0.0, 5.0, series_len=10)
    assert idx == 5


def test_speed_at_video_time_uses_offset():
    speeds = _speeds()
    # video t=2, offset 5 -> activity second 7 -> speed 7.0
    assert speed_at_video_time(2.0, 5.0, speeds) == 7.0


def test_index_clamps_below_zero():
    # video started before activity: early video times clamp to 0
    idx = video_time_to_activity_index(0.0, -5.0, series_len=10)
    assert idx == 0


def test_index_clamps_above_series():
    idx = video_time_to_activity_index(100.0, 5.0, series_len=10)
    assert idx == 9


import textwrap

from highlight_detector import load_gpx


SYNTHETIC_GPX = textwrap.dedent("""\
<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><trkseg>
    <trkpt lat="46.0000000" lon="7.0000000"><ele>1000</ele><time>2026-07-06T10:00:00Z</time></trkpt>
    <trkpt lat="46.0001000" lon="7.0000000"><ele>1005</ele><time>2026-07-06T10:00:01Z</time></trkpt>
    <trkpt lat="46.0002000" lon="7.0000000"><ele>1010</ele><time>2026-07-06T10:00:02Z</time></trkpt>
  </trkseg></trk>
</gpx>
""")


def test_load_gpx_parses_synthetic_fixture(tmp_path):
    gpx_file = tmp_path / "ride.gpx"
    gpx_file.write_text(SYNTHETIC_GPX)

    data = load_gpx(str(gpx_file))

    assert data.start_time.year == 2026
    assert data.start_time.tzinfo is not None
    assert data.first_coord[0] == 46.0
    assert len(data.speeds_kmh) == 3          # seconds 0,1,2
    assert data.elevation_gain_m == 10.0      # +5 +5
    assert data.total_distance_km > 0.0
    # second 1 should have a plausible speed (~11m in 1s ≈ 40 km/h)
    assert data.speeds_kmh[1] > 0.0


GAPPED_GPX = textwrap.dedent("""\
<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><trkseg>
    <trkpt lat="46.0000000" lon="7.0000000"><ele>1000</ele><time>2026-07-06T10:00:00Z</time></trkpt>
    <trkpt lat="46.0001000" lon="7.0000000"><ele>1005</ele><time>2026-07-06T10:00:01Z</time></trkpt>
    <trkpt lat="46.0003000" lon="7.0000000"><ele>1015</ele><time>2026-07-06T10:00:03Z</time></trkpt>
  </trkseg></trk>
</gpx>
""")


def test_load_gpx_forward_fills_gaps(tmp_path):
    gpx_file = tmp_path / "ride.gpx"
    gpx_file.write_text(GAPPED_GPX)

    data = load_gpx(str(gpx_file))

    # second 2 has no trackpoint (points only at t=0,1,3); it should be
    # forward-filled from second 1's real values, not reset to the
    # (0.0 speed, first-point coord) placeholder.
    assert data.coords[2] == data.coords[1]
    assert data.speeds_kmh[2] == data.speeds_kmh[1]
    assert data.coords[1] != data.coords[0]
    assert data.speeds_kmh[1] != 0.0


import pytest

from video_editor import check_ffmpeg


def test_check_ffmpeg_ok_when_installed():
    # On this dev machine ffmpeg is installed via Homebrew; should not raise.
    check_ffmpeg()


def test_check_ffmpeg_error_message(monkeypatch):
    import video_editor
    monkeypatch.setattr(video_editor.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError) as exc:
        check_ffmpeg()
    assert "brew install ffmpeg" in str(exc.value)


def test_creation_time_from_metadata(monkeypatch):
    import highlight_detector
    monkeypatch.setattr(
        highlight_detector, "_ffprobe_json",
        lambda path: {"format": {"tags": {"creation_time": "2026-07-06T10:00:05.000000Z"}}},
    )
    dt, from_meta = highlight_detector.get_video_creation_time("fake.mp4")
    assert from_meta is True
    assert dt.tzinfo is not None
    assert dt.hour == 10 and dt.second == 5


def test_creation_time_falls_back_to_mtime(monkeypatch, tmp_path):
    import highlight_detector
    f = tmp_path / "fake.mp4"
    f.write_text("x")
    monkeypatch.setattr(highlight_detector, "_ffprobe_json", lambda path: {"format": {"tags": {}}})
    dt, from_meta = highlight_detector.get_video_creation_time(str(f))
    assert from_meta is False
    assert dt.tzinfo is not None


from config import Config
from highlight_detector import score_seconds, merge_segments, Segment


def test_score_seconds_standstill_is_zero():
    cfg = Config()
    # speed below min_speed_kmh -> score forced to 0 regardless of flow
    scores = score_seconds([0.0, 2.0], [1.0, 1.0], cfg)
    assert scores[0] == 0.0
    assert scores[1] == 0.0


def test_score_seconds_fast_and_chaotic_scores_high():
    cfg = Config()
    scores = score_seconds([40.0], [1.0], cfg)  # high speed + high flow
    assert scores[0] > cfg.score_cutoff


def test_merge_segments_bridges_small_gaps_and_drops_short():
    cfg = Config(score_cutoff=0.5, min_segment_seconds=2.0, gap_bridge_seconds=2.0)
    # seconds:      0    1    2    3    4    5
    scores =       [0.9, 0.9, 0.1, 0.9, 0.9, 0.1]
    segs = merge_segments(scores, cfg)
    # 0-1 kept, gap at 2 (1s <= bridge) bridges to 3-4 -> one segment 0..5
    assert len(segs) == 1
    assert segs[0].start == 0.0
    assert segs[0].end == 5.0


def test_merge_segments_drops_too_short():
    cfg = Config(score_cutoff=0.5, min_segment_seconds=3.0, gap_bridge_seconds=0.0)
    scores = [0.9, 0.1, 0.1, 0.1]
    segs = merge_segments(scores, cfg)
    assert segs == []


from video_editor import _amix_filter


def test_amix_filter_without_music_is_empty():
    cfg = Config()
    assert _amix_filter(cfg, has_music=False, reel_duration=60.0) == ""


def test_amix_filter_with_music_includes_volumes_and_fade():
    cfg = Config(original_audio_volume=0.4, music_volume=1.0, fade_out_seconds=3.0)
    f = _amix_filter(cfg, has_music=True, reel_duration=60.0)
    assert "volume=0.4" in f       # original audio attenuation
    assert "volume=1.0" in f       # music volume
    assert "amix" in f
    assert "afade=t=out" in f      # music fade-out
    assert "st=57" in f            # fade starts at duration - fade_out (60-3)


from intro_generator import format_moving_time, reverse_geocode


def test_format_moving_time():
    assert format_moving_time(0) == "0m"
    assert format_moving_time(90) == "1m"
    assert format_moving_time(3660) == "1h 1m"


def test_reverse_geocode_falls_back_on_error(monkeypatch):
    import intro_generator
    class Boom:
        def reverse(self, *a, **k):
            raise RuntimeError("network down")
    monkeypatch.setattr(intro_generator, "_geocoder", lambda: Boom())
    result = reverse_geocode(46.0, 7.0)
    assert result == "46.0000, 7.0000"


import json
import shutil
import subprocess

import intro_generator


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg not installed",
)
def test_concat_intro_and_reel_handles_silent_intro(tmp_path):
    intro = tmp_path / "intro.mp4"
    reel = tmp_path / "reel.mp4"
    out = tmp_path / "final.mp4"

    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x240:d=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(intro)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=320x240:d=2",
         "-f", "lavfi", "-i", "sine=frequency=440:d=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(reel)],
        check=True, capture_output=True,
    )

    intro_generator._concat_intro_and_reel(str(intro), str(reel), 2.0, str(out), 320, 240)

    assert out.exists()
    assert out.stat().st_size > 0

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "json", str(out)],
        check=True, capture_output=True, text=True,
    )
    streams = json.loads(probe.stdout)["streams"]
    video_streams = [s for s in streams if s["codec_type"] == "video"]
    audio_streams = [s for s in streams if s["codec_type"] == "audio"]
    assert len(video_streams) == 1
    assert len(audio_streams) == 1


def test_refresh_if_needed_refreshes_when_expired(monkeypatch):
    import strava_client
    called = {}
    def fake_post(url, data=None, timeout=None):
        called["hit"] = True
        class R:
            def raise_for_status(self): pass
            def json(self): return {"access_token": "new", "refresh_token": "r2",
                                     "expires_at": 9999999999}
        return R()
    monkeypatch.setattr(strava_client.requests, "post", fake_post)
    monkeypatch.setenv("STRAVA_CLIENT_ID", "1")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "s")
    token = {"access_token": "old", "refresh_token": "r1", "expires_at": 0}
    new = strava_client._refresh_if_needed(token, now_epoch=100)
    assert called.get("hit") is True
    assert new["access_token"] == "new"


def test_refresh_if_needed_skips_when_valid(monkeypatch):
    import strava_client
    def boom(*a, **k):
        raise AssertionError("should not refresh")
    monkeypatch.setattr(strava_client.requests, "post", boom)
    token = {"access_token": "ok", "refresh_token": "r", "expires_at": 10_000}
    out = strava_client._refresh_if_needed(token, now_epoch=100)
    assert out["access_token"] == "ok"


def test_garmin_not_configured_by_default(monkeypatch):
    import garmin_client
    monkeypatch.delenv("GARMIN_EMAIL", raising=False)
    monkeypatch.delenv("GARMIN_PASSWORD", raising=False)
    assert garmin_client.is_configured() is False


from highlight_detector import estimate_offset_by_motion, GpxData


def _gpx_with_speeds(speeds):
    from datetime import datetime, timezone
    return GpxData(
        start_time=datetime(2026, 7, 6, 10, 0, 0, tzinfo=timezone.utc),
        speeds_kmh=list(speeds),
        coords=[(46.0, 7.0)] * len(speeds),
        total_distance_km=0.0, elevation_gain_m=0.0, moving_time_s=0.0,
        first_coord=(46.0, 7.0),
    )


def test_estimate_offset_recovers_known_lag():
    # GPS speed pattern over the ride.
    speeds = [0, 0, 0, 5, 20, 35, 40, 38, 10, 0, 0, 25, 30, 5, 0]
    # The video covers activity seconds 4..10 -> its flow mirrors that window,
    # so the correct offset (video_t -> activity index) is +4.
    flow = [speeds[i + 4] * 0.1 for i in range(7)]   # correlated, scaled copy
    cfg = Config()
    offset, corr = estimate_offset_by_motion(flow, _gpx_with_speeds(speeds), cfg)
    assert offset == 4.0
    assert corr > 0.9


def test_estimate_offset_flat_signal_does_not_crash():
    cfg = Config()
    offset, corr = estimate_offset_by_motion([0.0, 0.0, 0.0], _gpx_with_speeds([0.0] * 10), cfg)
    assert isinstance(offset, float)
    assert corr == 0.0


def test_estimate_offset_ignores_spurious_short_window():
    # Well-supported true alignment: flow (8 samples) is a scaled, slightly
    # noisy copy of speeds[3:11] -> the correct lag is +3, with a full
    # 8-sample overlap window and corr ~0.98.
    speeds = [1, 2, 0, 5, 20, 35, 40, 38, 10, 3, 0, 25, 30, 5, 0, 0, 0, 0, 0, 0]
    flow = [0.5, 2.0, 3.6, 4.0, 3.7, 1.1, 0.2, 0.9]

    # At the extreme lag -6, the overlap window shrinks to just 2 samples:
    # flow[6:8] = [0.2, 0.9] vs speeds[0:2] = [1, 2]. Both pairs are strictly
    # increasing, so Pearson correlation over these 2 points is a spurious,
    # perfect 1.0 -- higher than the true lag's ~0.98. Without a minimum
    # overlap guard (old `< 2` skip), this 2-sample window at lag=-6 would
    # win outright over the true, well-supported lag=3.
    cfg = Config()
    offset, corr = estimate_offset_by_motion(flow, _gpx_with_speeds(speeds), cfg)

    assert offset == 3.0
    assert corr > 0.9


from highlight_detector import _resolve_offset


def test_resolve_offset_manual_wins():
    used, src = _resolve_offset(metadata_offset=10.0, from_metadata=True,
                                filename_offset=None, auto_offset=4.0, offset_override=7.5, use_auto=True)
    assert used == 7.5 and src == "manual"


def test_resolve_offset_auto_when_requested():
    used, src = _resolve_offset(10.0, True, None, 4.0, offset_override=None, use_auto=True)
    assert used == 4.0 and src == "auto"


def test_resolve_offset_metadata_default():
    used, src = _resolve_offset(10.0, True, None, 4.0, offset_override=None, use_auto=False)
    assert used == 10.0 and src == "metadata"


def test_resolve_offset_mtime_source_when_not_from_metadata():
    used, src = _resolve_offset(10.0, False, None, 4.0, offset_override=None, use_auto=False)
    assert used == 10.0 and src == "mtime"


from main import render_sparkline, resolve_offset_args


def test_render_sparkline_min_and_max_blocks():
    s = render_sparkline([0.0, 10.0], width=2)
    assert s[0] == "▁"      # min -> lowest block
    assert s[-1] == "█"     # max -> highest block


def test_render_sparkline_empty_is_safe():
    assert render_sparkline([], width=10) == ""


def test_render_sparkline_flat_series():
    s = render_sparkline([5.0, 5.0, 5.0], width=3)
    assert set(s) == {"▁"}   # flat -> all lowest block
    assert len(s) == 3


def test_resolve_offset_args_manual():
    class A: sync_offset = 12.5; auto_sync = True
    assert resolve_offset_args(A()) == (12.5, True)


def test_resolve_offset_args_default():
    class A: sync_offset = None; auto_sync = False
    assert resolve_offset_args(A()) == (None, False)


from datetime import datetime
from highlight_detector import parse_filename_datetime, _resolve_offset


def test_parse_filename_datetime_insta360():
    dt = parse_filename_datetime("VID_20260705_144402_00_003.mp4")
    assert dt == datetime(2026, 7, 5, 14, 44, 2)


def test_parse_filename_datetime_no_match():
    assert parse_filename_datetime("random_clip.mp4") is None


def test_resolve_offset_filename_beats_mtime():
    # no metadata (from_metadata False) but a filename offset -> use filename
    used, src = _resolve_offset(metadata_offset=99.0, from_metadata=False,
                                filename_offset=12.0, auto_offset=4.0,
                                offset_override=None, use_auto=False)
    assert used == 12.0 and src == "filename"


def test_resolve_offset_mtime_when_no_filename():
    used, src = _resolve_offset(99.0, False, None, 4.0, None, False)
    assert used == 99.0 and src == "mtime"


def test_resolve_offset_metadata_beats_filename():
    used, src = _resolve_offset(50.0, True, 12.0, 4.0, None, False)
    assert used == 50.0 and src == "metadata"


def test_resolve_offset_manual_and_auto_still_win():
    assert _resolve_offset(50.0, True, 12.0, 4.0, 7.5, True)[1] == "manual"
    assert _resolve_offset(50.0, True, 12.0, 4.0, None, True) == (4.0, "auto")


from highlight_detector import resolve_sync_offset, ResolvedOffset


def test_resolve_sync_offset_no_flow_when_not_auto(monkeypatch, tmp_path):
    import highlight_detector as hd
    called = {"flow": 0}
    def boom(*a, **k):
        called["flow"] += 1
        return []
    monkeypatch.setattr(hd, "compute_optical_flow_per_second", boom)
    monkeypatch.setattr(hd, "get_video_creation_time",
                        lambda p: (_gpx_with_speeds([0]).start_time, True))
    monkeypatch.setattr(hd, "get_filename_timestamp", lambda p: None)
    gpx = _gpx_with_speeds([0, 1, 2])
    r = resolve_sync_offset("fake.mp4", gpx, Config(), offset_override=None, use_auto=False)
    assert isinstance(r, ResolvedOffset)
    assert r.offset_source == "metadata"
    assert called["flow"] == 0        # optical flow NOT computed without --auto-sync


def test_get_segment_efforts_uses_include_all_efforts(monkeypatch):
    import strava_client
    captured = {}
    monkeypatch.setattr(strava_client, "is_configured", lambda: True)
    monkeypatch.setattr(strava_client, "_load_token",
                        lambda: {"access_token": "t", "refresh_token": "r", "expires_at": 9e12})
    monkeypatch.setattr(strava_client, "_refresh_if_needed", lambda tok, now_epoch: tok)
    monkeypatch.setattr(strava_client, "_save_token", lambda tok: None)

    class R:
        def raise_for_status(self): pass
        def json(self): return {"segment_efforts": [{"name": "seg1"}]}
    def fake_get(url, headers=None, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return R()
    monkeypatch.setattr(strava_client.requests, "get", fake_get)

    efforts = strava_client.get_segment_efforts("12345")
    assert efforts == [{"name": "seg1"}]
    assert "12345" in captured["url"]
    assert captured["params"].get("include_all_efforts") in (True, "true", 1)


def test_get_segment_efforts_raises_when_unconfigured(monkeypatch):
    import strava_client, pytest
    monkeypatch.setattr(strava_client, "is_configured", lambda: False)
    with pytest.raises(RuntimeError):
        strava_client.get_segment_efforts("12345")


from datetime import datetime, timezone, timedelta
from config import Config
from segment_detector import (
    SegmentEffort, parse_efforts, is_noteworthy, SegmentClip,
    efforts_to_clips, format_segment_stats,
)


def _effort(name, start_offset_s, elapsed, dist=1000.0, watts=None, hr=None,
            pr_rank=None, achievements=None, starred=False):
    gpx_start = datetime(2026, 7, 5, 12, 32, 57, tzinfo=timezone.utc)
    return SegmentEffort(
        name=name, start_date=gpx_start + timedelta(seconds=start_offset_s),
        elapsed_time=elapsed, distance_m=dist, average_watts=watts,
        average_heartrate=hr, pr_rank=pr_rank,
        has_achievement=bool(achievements), starred=starred,
    )


def test_parse_efforts_maps_fields():
    raw = [{
        "name": "MTB Goeree Roggebos", "start_date": "2026-07-05T12:40:00Z",
        "elapsed_time": 250, "distance": 1130.0, "average_watts": 175.0,
        "average_heartrate": 153.0, "pr_rank": 1, "achievements": [{"rank": 1}],
        "segment": {"starred": True},
    }]
    efforts = parse_efforts(raw)
    assert len(efforts) == 1
    e = efforts[0]
    assert e.name == "MTB Goeree Roggebos"
    assert e.start_date == datetime(2026, 7, 5, 12, 40, 0, tzinfo=timezone.utc)
    assert e.elapsed_time == 250 and e.distance_m == 1130.0
    assert e.average_watts == 175.0 and e.pr_rank == 1
    assert e.has_achievement is True and e.starred is True


def test_is_noteworthy():
    assert is_noteworthy(_effort("a", 0, 60, achievements=[{"rank": 2}]))
    assert is_noteworthy(_effort("b", 0, 60, pr_rank=2))
    assert is_noteworthy(_effort("c", 0, 60, starred=True))
    assert not is_noteworthy(_effort("d", 0, 60))


def test_efforts_to_clips_maps_to_video_time_and_filters():
    gpx_start = datetime(2026, 7, 5, 12, 32, 57, tzinfo=timezone.utc)
    cfg = Config(min_segment_seconds=2.0)
    # video started 660s into the activity (offset 660); video is 300s long.
    offset = 660.0
    efforts = [
        # noteworthy, starts at activity 700s -> video 40s, 100s long -> [40,140]
        _effort("in-window", 700, 100, starred=True),
        # noteworthy but before the video window (activity 100s -> video -560) -> dropped
        _effort("too-early", 100, 50, starred=True),
        # not noteworthy -> dropped
        _effort("boring", 720, 80),
    ]
    clips = efforts_to_clips(efforts, gpx_start, offset, video_duration=300.0, cfg=cfg)
    assert len(clips) == 1
    assert clips[0].name == "in-window"
    assert clips[0].start == 40.0
    assert clips[0].end == 140.0


def test_efforts_to_clips_clamps_and_sorts():
    gpx_start = datetime(2026, 7, 5, 12, 32, 57, tzinfo=timezone.utc)
    cfg = Config(min_segment_seconds=2.0)
    offset = 0.0
    efforts = [
        _effort("second", 50, 20, starred=True),   # [50,70]
        _effort("first", 10, 20, starred=True),     # [10,30]
        _effort("overhang", 290, 40, starred=True), # [290,330] -> clamped end 300
    ]
    clips = efforts_to_clips(efforts, gpx_start, offset, video_duration=300.0, cfg=cfg)
    assert [c.name for c in clips] == ["first", "second", "overhang"]
    assert clips[-1].end == 300.0


def test_format_segment_stats_omits_missing():
    clip = SegmentClip(start=0.0, end=250.0, name="X",
                       stats={"elapsed_s": 250.0, "speed_kmh": 16.3,
                              "power_w": None, "hr_bpm": 153})
    s = format_segment_stats(clip)
    assert "4:10" in s          # 250s -> 4:10
    assert "16.3 km/u" in s
    assert "153 bpm" in s
    assert "W" not in s         # power omitted when None


from video_editor import _escape_drawtext, _lower_third_filter


def test_escape_drawtext_escapes_specials():
    out = _escape_drawtext("Rider's: 100% cool\\bad")
    assert "'\\''" in out        # single quote closed/escaped/reopened
    assert "\\:" in out          # colon escaped
    assert "\\%" in out or "%%" in out  # percent escaped


def test_lower_third_filter_contains_band_and_texts():
    cfg = Config()
    f = _lower_third_filter("MTB Goeree Roggebos", "4:10 · 16.3 km/u · 175 W", cfg)
    assert "drawbox" in f
    assert "drawtext" in f
    assert "Goeree" in f          # name present (escaped form still contains it)
    assert "16.3" in f            # stats line present


import shutil as _shutil


@pytest.mark.skipif(_shutil.which("ffmpeg") is None or _shutil.which("ffprobe") is None,
                    reason="ffmpeg not installed")
def test_lower_third_renders_on_real_clip(tmp_path):
    import subprocess, video_editor
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                    "testsrc=s=640x360:d=2", "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", str(src)], check=True, capture_output=True)
    out = tmp_path / "ov.mp4"
    vf = video_editor._lower_third_filter("Test Segment", "1:23 · 20.0 km/u", Config())
    r = subprocess.run(["ffmpeg", "-y", "-i", str(src), "-vf", vf,
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
                       capture_output=True)
    assert r.returncode == 0, r.stderr.decode()[-500:]
    assert out.exists() and out.stat().st_size > 0


@pytest.mark.skipif(_shutil.which("ffmpeg") is None or _shutil.which("ffprobe") is None,
                    reason="ffmpeg not installed")
def test_lower_third_renders_with_apostrophe(tmp_path):
    import subprocess, video_editor
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                    "testsrc=s=640x360:d=2", "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", str(src)], check=True, capture_output=True)
    out = tmp_path / "ov.mp4"
    vf = video_editor._lower_third_filter("Rider's Climb", "1:23 · 20.0 km/u", Config())
    r = subprocess.run(["ffmpeg", "-y", "-i", str(src), "-vf", vf,
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
                       capture_output=True)
    assert r.returncode == 0, r.stderr.decode()[-500:]
    assert out.exists() and out.stat().st_size > 0


import textwrap
from highlight_detector import load_gpx

GPX_HR = textwrap.dedent("""\
<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1"
     xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">
  <trk><trkseg>
    <trkpt lat="51.8000000" lon="4.0000000"><ele>10.0</ele><time>2026-07-05T12:00:00Z</time>
      <extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>100</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions></trkpt>
    <trkpt lat="51.8001000" lon="4.0000000"><ele>16.0</ele><time>2026-07-05T12:00:01Z</time>
      <extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>110</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions></trkpt>
    <trkpt lat="51.8002000" lon="4.0000000"><ele>22.0</ele><time>2026-07-05T12:00:02Z</time>
      <extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>120</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions></trkpt>
  </trkseg></trk>
</gpx>
""")


def test_load_gpx_parses_hr_elevation_distance(tmp_path):
    p = tmp_path / "hr.gpx"
    p.write_text(GPX_HR)
    g = load_gpx(str(p))
    assert len(g.elevations_m) == 3
    assert g.elevations_m[0] == 10.0 and g.elevations_m[2] == 22.0
    assert g.hr_bpm[0] == 100 and g.hr_bpm[2] == 120
    assert g.cum_distance_m[0] == 0.0
    assert g.cum_distance_m[2] > g.cum_distance_m[1] > 0.0   # monotonic


def test_load_gpx_hr_none_when_absent(tmp_path):
    # reuse the earlier no-HR fixture SYNTHETIC_GPX (already in this file)
    p = tmp_path / "plain.gpx"
    p.write_text(SYNTHETIC_GPX)
    g = load_gpx(str(p))
    assert all(h is None for h in g.hr_bpm)
    assert len(g.elevations_m) == len(g.speeds_kmh)


from telemetry import TelemetrySample, sample_telemetry, slope_pct
from highlight_detector import GpxData


def _gpx_series():
    # 5 seconds: speed ramp, elevation +2m/s, hr ramp, distance 10m/s
    return GpxData(
        start_time=datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc),
        speeds_kmh=[0, 10, 20, 30, 40],
        coords=[(51.8, 4.0), (51.8, 4.0), (51.8, 4.0), (51.8, 4.0), (51.8, 4.0)],
        elevations_m=[0.0, 2.0, 4.0, 6.0, 8.0],
        hr_bpm=[100, 110, 120, 130, 140],
        cum_distance_m=[0.0, 10.0, 20.0, 30.0, 40.0],
        total_distance_km=0.04, elevation_gain_m=8.0, moving_time_s=5.0,
        first_coord=(51.8, 4.0),
    )


def test_sample_telemetry_interpolates():
    g = _gpx_series()
    s = sample_telemetry(g, activity_time_s=1.5, segment_start_s=1.0)
    assert isinstance(s, TelemetrySample)
    assert abs(s.speed_kmh - 15.0) < 1e-6        # between 10 and 20
    assert abs(s.elevation_m - 3.0) < 1e-6       # between 2 and 4
    assert abs(s.hr_bpm - 115.0) < 1e-6          # between 110 and 120
    # distance within segment: cum(1.5)=15 minus cum(1.0)=10 -> 5 m -> 0.005 km
    assert abs(s.seg_distance_km - 0.005) < 1e-6


def test_slope_pct_known_grade():
    g = _gpx_series()
    # elevation +2 m/s, distance +10 m/s -> grade 20%
    assert abs(slope_pct(g, activity_time_s=2.0, window_s=1) - 20.0) < 1e-6


def test_sample_telemetry_clamps_out_of_range():
    g = _gpx_series()
    s = sample_telemetry(g, activity_time_s=100.0, segment_start_s=0.0)
    assert s.speed_kmh == 40.0        # clamps to last


from minimap import compute_bounds, project, project_track


def test_project_track_fits_box_and_preserves_aspect():
    # a simple L-shaped track
    coords = [(51.800, 4.000), (51.802, 4.000), (51.802, 4.004)]
    pts = project_track(coords, w=200, h=200, pad=20)
    assert len(pts) == 3
    for x, y in pts:
        assert 20 - 1e-6 <= x <= 180 + 1e-6
        assert 20 - 1e-6 <= y <= 180 + 1e-6
    # north (higher lat) maps to smaller y (top): point[1] has higher lat than point[0]
    assert pts[1][1] < pts[0][1]


def test_project_single_point_is_centered():
    coords = [(51.8, 4.0)]
    x, y = project_track(coords, w=100, h=100, pad=10)[0]
    assert abs(x - 50) < 1e-6 and abs(y - 50) < 1e-6


from telemetry import TelemetrySample
from config import Config
import hud_renderer


def _sample():
    return TelemetrySample(speed_kmh=15.0, elevation_m=-1.0, slope_pct=1.0,
                           hr_bpm=149, lat=51.8006, lon=4.001, seg_distance_km=0.29)


def _alpha_region_nonzero(img, box):
    crop = img.crop(box).getchannel("A")
    return crop.getextrema()[1] > 0   # some non-transparent pixel


def test_render_hud_frame_dimensions_and_regions():
    W, H = 1920, 1080
    coords = [(51.800, 4.000), (51.801, 4.000), (51.802, 4.002), (51.803, 4.004)]
    img = hud_renderer.render_hud_frame(_sample(), "MTB Goeree Roggebos",
                                        coords, (W, H), Config(), "05-07-2026")
    assert img.size == (W, H)
    assert img.mode == "RGBA"
    assert _alpha_region_nonzero(img, (0, 0, 600, 200))            # top-left name/HR
    assert _alpha_region_nonzero(img, (W - 400, 0, W, 200))        # top-right stats
    assert _alpha_region_nonzero(img, (W - 360, H - 360, W, H))    # bottom-right speedo
    assert _alpha_region_nonzero(img, (0, H - 320, 360, H))        # bottom-left minimap


def test_render_hud_frame_without_hr_omits_bpm():
    W, H = 1280, 720
    s = _sample(); s.hr_bpm = None
    coords = [(51.8, 4.0), (51.801, 4.001)]
    img = hud_renderer.render_hud_frame(s, "Seg", coords, (W, H), Config(), "05-07-2026")
    assert img.size == (W, H)   # renders without error when HR is None


import shutil as _sh


@pytest.mark.skipif(_sh.which("ffmpeg") is None or _sh.which("ffprobe") is None,
                    reason="ffmpeg not installed")
def test_hud_overlay_renders_on_real_clip(tmp_path):
    import subprocess
    from datetime import datetime, timezone
    from config import Config
    from highlight_detector import GpxData
    from segment_detector import SegmentClip
    import video_editor

    # a 2s synthetic clip
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=640x360:r=30:d=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
                   check=True, capture_output=True)
    n = 6
    gpx = GpxData(
        start_time=datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc),
        speeds_kmh=[10] * n, coords=[(51.8 + i * 1e-4, 4.0) for i in range(n)],
        elevations_m=[float(i) for i in range(n)], hr_bpm=[120] * n,
        cum_distance_m=[i * 10.0 for i in range(n)],
        total_distance_km=0.05, elevation_gain_m=5.0, moving_time_s=float(n),
        first_coord=(51.8, 4.0),
    )
    clip = SegmentClip(start=0.0, end=2.0, name="Test Segment",
                       stats={"elapsed_s": 2.0, "speed_kmh": 10.0, "power_w": None, "hr_bpm": 120})
    cfg = Config(hud_fps=10)
    reel = video_editor.build_segment_reel(str(src), [clip], None, cfg,
                                           gpx=gpx, offset_seconds=0.0)
    import os
    assert reel and os.path.getsize(reel) > 0
    # ffprobe: one video stream present
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v",
                          "-show_entries", "stream=codec_type", "-of", "csv=p=0", reel],
                         capture_output=True, text=True)
    assert "video" in out.stdout


def test_parse_pick_selects_indices():
    from main import parse_pick
    assert parse_pick("1,3", 3) == [1, 3]
    assert parse_pick(" 3 , 1 ", 3) == [1, 3]      # sorted + whitespace-tolerant
    assert parse_pick("1,1,2", 3) == [1, 2]        # dedup
    assert parse_pick("1,9", 3) == [1]             # out-of-range dropped
    assert parse_pick("x,2", 3) == [2]             # non-numeric dropped
    assert parse_pick(None, 3) is None             # None -> keep all
    assert parse_pick("", 3) is None
    assert parse_pick("9", 3) == []                # spec given but nothing valid


def test_get_activity_streams_requests_keys(monkeypatch):
    import strava_client
    cap = {}
    monkeypatch.setattr(strava_client, "is_configured", lambda: True)
    monkeypatch.setattr(strava_client, "_load_token",
                        lambda: {"access_token": "t", "refresh_token": "r", "expires_at": 9e12})
    monkeypatch.setattr(strava_client, "_refresh_if_needed", lambda tok, now_epoch: tok)
    monkeypatch.setattr(strava_client, "_save_token", lambda tok: None)

    class R:
        def raise_for_status(self): pass
        def json(self): return {"velocity_smooth": {"data": [1, 2, 3]}}
    def fake_get(url, headers=None, params=None, timeout=None):
        cap["url"] = url; cap["params"] = params
        return R()
    monkeypatch.setattr(strava_client.requests, "get", fake_get)

    out = strava_client.get_activity_streams("999")
    assert out == {"velocity_smooth": {"data": [1, 2, 3]}}
    assert "999" in cap["url"] and "streams" in cap["url"]
    assert cap["params"].get("key_by_type") in (True, "true", 1)
    assert "velocity_smooth" in cap["params"].get("keys", "")


def test_get_activity_streams_raises_when_unconfigured(monkeypatch):
    import strava_client, pytest
    monkeypatch.setattr(strava_client, "is_configured", lambda: False)
    with pytest.raises(RuntimeError):
        strava_client.get_activity_streams("999")


from telemetry import TelemetrySeries, telemetry_from_streams
from highlight_detector import GpxData
from datetime import datetime, timezone


def _gpx_n(n):
    return GpxData(
        start_time=datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc),
        speeds_kmh=[1.0] * n, coords=[(51.8, 4.0)] * n,
        elevations_m=[5.0] * n, hr_bpm=[90] * n, cum_distance_m=[float(i) for i in range(n)],
        total_distance_km=0.0, elevation_gain_m=0.0, moving_time_s=0.0, first_coord=(51.8, 4.0))


def _streams(n=3):
    return {
        "time": {"data": list(range(n))},
        "velocity_smooth": {"data": [0.0, 5.0, 10.0][:n]},   # m/s
        "heartrate": {"data": [120, 130, 140][:n]},
        "watts": {"data": [100, 200, 300][:n]},
        "altitude": {"data": [10.0, 11.0, 12.0][:n]},
        "grade_smooth": {"data": [1.0, 2.0, 3.0][:n]},
        "latlng": {"data": [[51.80, 4.0], [51.801, 4.0], [51.802, 4.0]][:n]},
        "distance": {"data": [0.0, 10.0, 20.0][:n]},
    }


def test_telemetry_from_streams_maps_and_scales():
    g = _gpx_n(3)
    ts = telemetry_from_streams(_streams(3), g)
    assert isinstance(ts, TelemetrySeries)
    # speed comes from the distance derivative (distance 0,10,20 = 10 m/s = 36 km/h),
    # which takes priority over velocity_smooth
    assert ts.speeds_kmh == [36.0, 36.0, 36.0]
    assert ts.watts == [100.0, 200.0, 300.0]
    assert ts.slopes_pct == [1.0, 2.0, 3.0]        # grade_smooth
    assert ts.hr_bpm == [120.0, 130.0, 140.0]


def test_telemetry_from_streams_missing_watts_falls_back():
    g = _gpx_n(3)
    s = _streams(3); del s["watts"]
    del s["velocity_smooth"]; del s["distance"]
    ts = telemetry_from_streams(s, g)
    assert all(w is None for w in ts.watts)         # no watts -> all None
    assert ts.speeds_kmh == g.speeds_kmh            # no distance/velocity -> GPX speed


def test_sample_telemetry_from_stream_source_has_power_and_stream_slope():
    from telemetry import sample_telemetry
    g = _gpx_n(3)
    ts = telemetry_from_streams(_streams(3), g)
    s = sample_telemetry(ts, activity_time_s=1.0, segment_start_s=0.0)
    assert abs(s.speed_kmh - 36.0) < 1e-6          # distance derivative (10 m/s)
    assert s.power_w == 200.0
    assert abs(s.slope_pct - 2.0) < 1e-6            # from grade_smooth, not computed


def test_sample_telemetry_still_works_on_bare_gpx():
    from telemetry import sample_telemetry
    g = _gpx_n(3)
    s = sample_telemetry(g, activity_time_s=1.0, segment_start_s=0.0)
    assert s.power_w is None                         # GpxData has no watts


def test_render_hud_frame_draws_power_when_present():
    from telemetry import TelemetrySample
    from config import Config
    import hud_renderer
    W, H = 1920, 1080
    coords = [(51.8, 4.0), (51.801, 4.0)]
    s = TelemetrySample(speed_kmh=18.0, elevation_m=-1.0, slope_pct=4.0, hr_bpm=150,
                        lat=51.8, lon=4.0, seg_distance_km=0.3, power_w=218.0)
    img = hud_renderer.render_hud_frame(s, "Seg", coords, (W, H), Config(), "05-07-2026")
    # power panel sits just under the HR panel (top-left, ~y 168..226 at 1080p)
    band = img.crop((40, 165, 300, 230)).getchannel("A")
    assert band.getextrema()[1] > 0


def test_render_hud_frame_no_power_panel_when_none():
    from telemetry import TelemetrySample
    from config import Config
    import hud_renderer
    W, H = 1920, 1080
    s = TelemetrySample(speed_kmh=18.0, elevation_m=-1.0, slope_pct=4.0, hr_bpm=None,
                        lat=51.8, lon=4.0, seg_distance_km=0.3, power_w=None)
    img = hud_renderer.render_hud_frame(s, "Seg", [(51.8, 4.0), (51.801, 4.0)],
                                        (W, H), Config(), "05-07-2026")
    # with no HR and no power, the top-left area under the name has no panel pixels
    band = img.crop((40, 165, 300, 230)).getchannel("A")
    assert band.getextrema()[1] == 0


import shutil as _sh2


@pytest.mark.skipif(_sh2.which("ffmpeg") is None or _sh2.which("ffprobe") is None,
                    reason="ffmpeg not installed")
def test_segment_reel_with_stream_source_renders(tmp_path):
    import subprocess
    from datetime import datetime, timezone
    from config import Config
    from highlight_detector import GpxData
    from segment_detector import SegmentClip
    from telemetry import telemetry_from_streams
    import video_editor
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=640x360:r=30:d=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
                   check=True, capture_output=True)
    n = 6
    gpx = GpxData(start_time=datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc),
                  speeds_kmh=[10] * n, coords=[(51.8 + i * 1e-4, 4.0) for i in range(n)],
                  elevations_m=[float(i) for i in range(n)], hr_bpm=[120] * n,
                  cum_distance_m=[i * 10.0 for i in range(n)],
                  total_distance_km=0.05, elevation_gain_m=5.0, moving_time_s=float(n),
                  first_coord=(51.8, 4.0))
    streams = {"time": {"data": list(range(n))},
               "velocity_smooth": {"data": [5.0] * n},
               "watts": {"data": [200] * n},
               "grade_smooth": {"data": [3.0] * n}}
    src_series = telemetry_from_streams(streams, gpx)
    clip = SegmentClip(start=0.0, end=2.0, name="Test", stats={"elapsed_s": 2.0})
    cfg = Config(hud_fps=10)
    reel = video_editor.build_segment_reel(str(src), [clip], None, cfg,
                                           gpx=gpx, offset_seconds=0.0,
                                           telemetry_source=src_series)
    import os
    assert reel and os.path.getsize(reel) > 0


def test_resample_interpolates_non_1hz():
    from telemetry import _resample
    # samples at t=0,2,4 -> per-second 0..4 should linearly interpolate
    assert _resample([0, 2, 4], [0.0, 10.0, 20.0], 5) == [0.0, 5.0, 10.0, 15.0, 20.0]


def test_resample_drops_none_entries():
    from telemetry import _resample
    # a mid-stream None is dropped; the gap is interpolated across neighbours
    assert _resample([0, 1, 2], [10.0, None, 30.0], 3) == [10.0, 20.0, 30.0]


def test_resample_all_none_returns_none_list():
    from telemetry import _resample
    assert _resample([0, 1, 2], [None, None, None], 3) == [None, None, None]


def test_telemetry_speed_prefers_distance_derivative():
    g = _gpx_n(4)
    streams = {"time": {"data": [0, 1, 2, 3]},
               "distance": {"data": [0.0, 10.0, 20.0, 30.0]},        # 10 m/s -> 36 km/h
               "velocity_smooth": {"data": [5.0, 5.0, 5.0, 5.0]}}    # 18 km/h (ignored)
    ts = telemetry_from_streams(streams, g)
    assert all(abs(s - 36.0) < 1e-6 for s in ts.speeds_kmh)          # distance wins


def test_telemetry_speed_falls_back_to_velocity_without_distance():
    g = _gpx_n(3)
    streams = {"time": {"data": [0, 1, 2]}, "velocity_smooth": {"data": [5.0, 5.0, 5.0]}}
    ts = telemetry_from_streams(streams, g)
    assert all(abs(s - 18.0) < 1e-6 for s in ts.speeds_kmh)


def test_get_video_resolution(monkeypatch):
    import highlight_detector
    monkeypatch.setattr(highlight_detector, "_ffprobe_json",
                        lambda path: {"streams": [{"codec_type": "audio"},
                                                  {"codec_type": "video", "width": 3840, "height": 2160}]})
    assert highlight_detector.get_video_resolution("x.mp4") == (3840, 2160)


def test_resolve_output_height_source(monkeypatch):
    import main, highlight_detector
    monkeypatch.setattr(highlight_detector, "get_video_resolution", lambda p: (3840, 2160))
    assert main.resolve_output_height("source", "x.mp4") == 2160
    assert main.resolve_output_height("SOURCE", "x.mp4") == 2160


def test_resolve_output_height_int():
    import main
    assert main.resolve_output_height("2160", "x.mp4") == 2160
    assert main.resolve_output_height("1080", "x.mp4") == 1080


import shutil as _sh3


@pytest.mark.skipif(_sh3.which("ffmpeg") is None or _sh3.which("ffprobe") is None,
                    reason="ffmpeg not installed")
def test_final_video_respects_output_height(tmp_path, monkeypatch):
    import subprocess
    from datetime import datetime, timezone
    from config import Config
    from highlight_detector import GpxData
    import intro_generator
    # a 16:9 reel (also used as the aspect source)
    reel = tmp_path / "reel.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=640x360:d=2",
                    "-f", "lavfi", "-i", "sine=d=2", "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", "-c:a", "aac", "-shortest", str(reel)],
                   check=True, capture_output=True)
    monkeypatch.setattr(intro_generator, "reverse_geocode", lambda lat, lon: "Test, NL")
    g = GpxData(start_time=datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc),
                speeds_kmh=[10], coords=[(52.0, 4.0)], elevations_m=[5.0], hr_bpm=[100],
                cum_distance_m=[0.0], total_distance_km=5.0, elevation_gain_m=20.0,
                moving_time_s=600.0, first_coord=(52.0, 4.0))
    cfg = Config(); cfg.output_height = 720
    class A:
        video = str(reel); strava = False; garmin = False
    out = tmp_path / "final.mp4"
    intro_generator.build_final_video(str(reel), g, cfg, str(out), A())
    h = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=height", "-of", "csv=p=0", str(out)],
                       capture_output=True, text=True).stdout.strip()
    assert h == "720"
