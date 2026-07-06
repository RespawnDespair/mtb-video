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

    intro_generator._concat_intro_and_reel(str(intro), str(reel), 2.0, str(out))

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
