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
