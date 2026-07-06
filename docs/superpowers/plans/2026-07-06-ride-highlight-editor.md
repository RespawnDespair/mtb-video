# ride-highlight-editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local macOS Python CLI that turns MTB action-cam footage plus a Strava/Garmin GPX export into a music-backed highlight video with a stats intro.

**Architecture:** Three stages — highlight detection (GPX speed + video-timeline sync + OpenCV optical flow → interest score → segments), auto-edit (FFmpeg cut/concat + music mixing), and a MoviePy stats intro — orchestrated by an argparse CLI. Telemetry is external (GPX), synced to the video via the video's creation timestamp, making the pipeline camera-agnostic.

**Tech Stack:** Python 3.11–3.14, opencv-python, numpy, moviepy, gpxpy, geopy, requests, garminconnect, ffmpeg-python, FFmpeg (Homebrew), pytest.

## Global Constraints

- Python: works on 3.11–3.14; system Python here is 3.14.5. Pin conservative dependency versions; recommend a venv.
- FFmpeg installed via Homebrew (`brew install ffmpeg`); code must check presence and error clearly if missing.
- All processing local. Only optional network calls: Strava API, Garmin Connect, Nominatim geocoding.
- Strava and Garmin integrations are OPTIONAL: skipped gracefully when unconfigured; API failures log a warning and continue with GPX-only data.
- Telemetry input is GPX only for v1 (no FIT).
- Cutting: `cut_mode="reencode"` (frame-accurate) is the default; `cut_mode="copy"` (keyframe-snapped, `-c copy`) is an optional toggle.
- Final assembly re-encodes the intro+highlights concat so codec/resolution/framerate match across cameras.
- `--dry-run` prints detected segments and scores and renders no video.
- Secrets/outputs never committed: `.strava_token.json`, venv, and output media are gitignored.
- Tests use pytest. All dependencies pinned in `requirements.txt`.

---

## File Structure

- `config.py` — `Config` dataclass of all tunable thresholds.
- `highlight_detector.py` — Stage 1: GPX parsing, ffprobe timestamp read, timeline sync, optical flow, scoring, segment merge.
- `video_editor.py` — Stage 2: FFmpeg presence check, cut/concat, music mixing with fade-out.
- `intro_generator.py` — Stage 3: stats computation, reverse-geocode, MoviePy intro render, final assembly concat.
- `strava_client.py` — Strava OAuth2 + token refresh + activity stats.
- `garmin_client.py` — Garmin Connect login + HR/power stats.
- `main.py` — argparse CLI, orchestration, `--dry-run`.
- `test_sync.py` — pytest tests for the sync/scoring core.
- `requirements.txt`, `README.md`, `.gitignore`.

---

## Task 1: Project scaffolding, config, and dependency pinning

**Files:**
- Create: `requirements.txt`
- Create: `.gitignore`
- Create: `config.py`
- Test: `test_sync.py` (start the file; more added later)

**Interfaces:**
- Produces: `Config` dataclass with fields `min_speed_kmh: float=3.0`, `score_cutoff: float=0.35`, `gps_weight: float=0.6`, `flow_weight: float=0.4`, `original_audio_volume: float=0.4`, `music_volume: float=1.0`, `fade_out_seconds: float=3.0`, `intro_duration: float=7.0`, `cut_mode: str="reencode"`, `flow_sample_fps: float=2.0`, `min_segment_seconds: float=2.0`, `gap_bridge_seconds: float=2.0`.

- [ ] **Step 1: Write `.gitignore`**

```gitignore
__pycache__/
*.pyc
.venv/
venv/
.strava_token.json
*.mp4
*.mov
*.mp3
*.aac
!tests/**/*.mp4
.DS_Store
```

- [ ] **Step 2: Write `requirements.txt` (pinned)**

```text
opencv-python==4.10.0.84
numpy==1.26.4
moviepy==1.0.3
gpxpy==1.6.2
geopy==2.4.1
requests==2.32.3
garminconnect==0.2.19
ffmpeg-python==0.2.0
pytest==8.3.2
```

- [ ] **Step 3: Write the failing test for `Config` defaults**

Append to `test_sync.py`:

```python
from config import Config


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
```

- [ ] **Step 4: Run test to verify it fails**

Run: `python -m pytest test_sync.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'config'`.

- [ ] **Step 5: Write `config.py`**

```python
from dataclasses import dataclass


@dataclass
class Config:
    """All tunable thresholds for the highlight pipeline."""

    # Stage 1 — detection
    min_speed_kmh: float = 3.0          # below this = standstill, dropped
    score_cutoff: float = 0.35          # per-second interest score threshold [0,1]
    gps_weight: float = 0.6             # weight of GPS speed in interest score
    flow_weight: float = 0.4            # weight of optical flow in interest score
    flow_sample_fps: float = 2.0        # frames sampled per second for optical flow
    min_segment_seconds: float = 2.0    # discard segments shorter than this
    gap_bridge_seconds: float = 2.0     # merge kept segments separated by <= this

    # Stage 2 — edit / audio
    original_audio_volume: float = 0.4  # multiplier on original video audio
    music_volume: float = 1.0           # multiplier on music track
    fade_out_seconds: float = 3.0       # music fade-out at end
    cut_mode: str = "reencode"          # "reencode" (frame-accurate) or "copy"

    # Stage 3 — intro
    intro_duration: float = 7.0         # seconds
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest test_sync.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Commit**

```bash
git add .gitignore requirements.txt config.py test_sync.py
git commit -m "feat: scaffold project with Config dataclass and pinned deps"
```

---

## Task 2: GPX parsing and timeline sync (the tested core)

**Files:**
- Create: `highlight_detector.py`
- Test: `test_sync.py`

**Interfaces:**
- Consumes: `Config` from Task 1.
- Produces:
  - `@dataclass GpxData` with `start_time: datetime` (tz-aware UTC), `speeds_kmh: list[float]` (per-second, index = seconds since start), `coords: list[tuple[float, float]]` (per-second lat,lon), `total_distance_km: float`, `elevation_gain_m: float`, `moving_time_s: float`, `first_coord: tuple[float, float]`.
  - `load_gpx(path: str) -> GpxData`.
  - `compute_offset_seconds(video_start: datetime, activity_start: datetime) -> float`.
  - `video_time_to_activity_index(video_t: float, offset_seconds: float, series_len: int) -> int`.
  - `speed_at_video_time(video_t: float, offset_seconds: float, speeds_kmh: list[float]) -> float`.

- [ ] **Step 1: Write failing tests for the sync math**

Append to `test_sync.py`:

```python
from datetime import datetime, timedelta, timezone

from highlight_detector import (
    compute_offset_seconds,
    video_time_to_activity_index,
    speed_at_video_time,
)


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest test_sync.py -k "offset or video_time or speed or clamp" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'highlight_detector'`.

- [ ] **Step 3: Implement sync functions + GpxData + load_gpx**

Create `highlight_detector.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import gpxpy


@dataclass
class GpxData:
    start_time: datetime                       # tz-aware UTC (first trackpoint)
    speeds_kmh: list[float] = field(default_factory=list)  # per-second series
    coords: list[tuple[float, float]] = field(default_factory=list)  # per-second lat,lon
    total_distance_km: float = 0.0
    elevation_gain_m: float = 0.0
    moving_time_s: float = 0.0
    first_coord: tuple[float, float] = (0.0, 0.0)


def compute_offset_seconds(video_start: datetime, activity_start: datetime) -> float:
    """Seconds to add to a video timestamp to reach activity time."""
    return (video_start - activity_start).total_seconds()


def video_time_to_activity_index(video_t: float, offset_seconds: float, series_len: int) -> int:
    """Map a video-relative time (seconds) to an index into the per-second GPS series."""
    idx = int(round(video_t + offset_seconds))
    if idx < 0:
        return 0
    if idx > series_len - 1:
        return series_len - 1
    return idx


def speed_at_video_time(video_t: float, offset_seconds: float, speeds_kmh: list[float]) -> float:
    idx = video_time_to_activity_index(video_t, offset_seconds, len(speeds_kmh))
    return speeds_kmh[idx]


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_gpx(path: str) -> GpxData:
    """Parse a GPX file into a per-second GpxData series."""
    with open(path, "r") as f:
        gpx = gpxpy.parse(f)

    points = [p for track in gpx.tracks for seg in track.segments for p in seg.points]
    if not points:
        raise ValueError(f"No trackpoints found in GPX file: {path}")

    start = _to_utc(points[0].time)
    duration = int((_to_utc(points[-1].time) - start).total_seconds())

    # Per-second series, forward-filled from the nearest earlier point.
    speeds = [0.0] * (duration + 1)
    coords = [(points[0].latitude, points[0].longitude)] * (duration + 1)

    total_distance_m = 0.0
    elevation_gain_m = 0.0
    moving_time_s = 0.0
    prev = None
    for p in points:
        t = _to_utc(p.time)
        sec = int((t - start).total_seconds())
        sec = max(0, min(duration, sec))
        if prev is not None:
            dist = p.distance_3d(prev) or 0.0
            dt = (t - _to_utc(prev.time)).total_seconds()
            speed_kmh = (dist / dt) * 3.6 if dt > 0 else 0.0
            speeds[sec] = speed_kmh
            coords[sec] = (p.latitude, p.longitude)
            total_distance_m += dist
            if speed_kmh > 3.0:
                moving_time_s += dt
            if p.elevation is not None and prev.elevation is not None:
                gain = p.elevation - prev.elevation
                if gain > 0:
                    elevation_gain_m += gain
        prev = p

    # forward-fill gaps in speed/coords
    for i in range(1, len(speeds)):
        if speeds[i] == 0.0 and speeds[i - 1] != 0.0:
            # leave true standstills as 0; only fill obvious gaps between samples
            pass

    return GpxData(
        start_time=start,
        speeds_kmh=speeds,
        coords=coords,
        total_distance_km=total_distance_m / 1000.0,
        elevation_gain_m=elevation_gain_m,
        moving_time_s=moving_time_s,
        first_coord=(points[0].latitude, points[0].longitude),
    )
```

- [ ] **Step 4: Run tests to verify sync tests pass**

Run: `python -m pytest test_sync.py -k "offset or video_time or speed or clamp" -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Write a failing test for `load_gpx` with a synthetic fixture**

Append to `test_sync.py`:

```python
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
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest test_sync.py::test_load_gpx_parses_synthetic_fixture -v`
Expected: PASS. (If FAIL, adjust `load_gpx`, not the test's known-good values.)

- [ ] **Step 7: Run the full test file**

Run: `python -m pytest test_sync.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add highlight_detector.py test_sync.py
git commit -m "feat: GPX parsing and video-timeline sync with tests"
```

---

## Task 3: Video probing (ffprobe) and FFmpeg presence check

**Files:**
- Modify: `highlight_detector.py` (add probe functions)
- Create: `video_editor.py` (add `check_ffmpeg` only in this task)
- Test: `test_sync.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `get_video_creation_time(path: str) -> tuple[datetime, bool]` in `highlight_detector.py` — returns `(utc_datetime, from_metadata)`; `from_metadata=False` means it fell back to file mtime.
  - `get_video_duration(path: str) -> float` in `highlight_detector.py`.
  - `check_ffmpeg() -> None` in `video_editor.py` — raises `RuntimeError` with a Homebrew hint if ffmpeg/ffprobe missing.

- [ ] **Step 1: Write failing test for the ffmpeg check message**

Append to `test_sync.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_sync.py -k check_ffmpeg -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'video_editor'`.

- [ ] **Step 3: Create `video_editor.py` with `check_ffmpeg`**

```python
from __future__ import annotations

import shutil
import subprocess


def check_ffmpeg() -> None:
    """Ensure ffmpeg and ffprobe are available; raise a clear error otherwise."""
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise RuntimeError(
                f"{tool} not found. Install FFmpeg via Homebrew:\n"
                "    brew install ffmpeg"
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test_sync.py -k check_ffmpeg -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Add ffprobe helpers to `highlight_detector.py`**

Append to `highlight_detector.py`:

```python
import json
import os
import subprocess


def _ffprobe_json(path: str) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def get_video_creation_time(path: str) -> tuple[datetime, bool]:
    """Return (utc creation time, from_metadata). Falls back to file mtime."""
    info = _ffprobe_json(path)
    tag = info.get("format", {}).get("tags", {}).get("creation_time")
    if tag:
        # ffprobe emits ISO 8601, usually ending in 'Z'
        dt = datetime.fromisoformat(tag.replace("Z", "+00:00"))
        return _to_utc(dt), True
    mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
    return mtime, False


def get_video_duration(path: str) -> float:
    info = _ffprobe_json(path)
    return float(info.get("format", {}).get("duration", 0.0))
```

- [ ] **Step 6: Write a test for creation-time parsing using a mocked ffprobe**

Append to `test_sync.py`:

```python
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
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python -m pytest test_sync.py -k "creation_time or check_ffmpeg" -v`
Expected: PASS (4 passed).

- [ ] **Step 8: Commit**

```bash
git add highlight_detector.py video_editor.py test_sync.py
git commit -m "feat: ffprobe video timestamp/duration + ffmpeg presence check"
```

---

## Task 4: Optical flow, interest scoring, and segment merge

**Files:**
- Modify: `highlight_detector.py`
- Test: `test_sync.py`

**Interfaces:**
- Consumes: `Config`, `GpxData`, `speed_at_video_time`, `compute_offset_seconds` from Tasks 1–2; `get_video_duration`, `get_video_creation_time` from Task 3.
- Produces:
  - `@dataclass Segment` with `start: float`, `end: float`, `score: float`.
  - `compute_optical_flow_per_second(video_path: str, cfg: Config) -> list[float]` — mean-magnitude "motion" value per video-second (0.0 when video unreadable is not allowed; raises on open failure).
  - `score_seconds(speeds_at_video_secs: list[float], flow_per_sec: list[float], cfg: Config) -> list[float]` — per-second interest score in [0,1].
  - `merge_segments(scores: list[float], cfg: Config) -> list[Segment]`.
  - `detect_highlights(video_path: str, gpx: GpxData, cfg: Config) -> tuple[list[Segment], list[float]]` — orchestrates sync + flow + score + merge; returns (segments, per-second scores).

- [ ] **Step 1: Write failing tests for scoring and merging (pure functions, no video)**

Append to `test_sync.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest test_sync.py -k "score_seconds or merge_segments" -v`
Expected: FAIL — `ImportError: cannot import name 'score_seconds'`.

- [ ] **Step 3: Implement scoring, merge, flow, and orchestration**

Append to `highlight_detector.py`:

```python
import numpy as np
import cv2


@dataclass
class Segment:
    start: float
    end: float
    score: float


def _normalize(values: list[float]) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return arr
    lo, hi = float(arr.min()), float(arr.max())
    if hi - lo < 1e-9:
        return np.zeros_like(arr)
    return (arr - lo) / (hi - lo)


def score_seconds(speeds_at_video_secs, flow_per_sec, cfg: Config) -> list[float]:
    """Combine per-second GPS speed and optical flow into interest scores [0,1]."""
    n = min(len(speeds_at_video_secs), len(flow_per_sec))
    speeds = list(speeds_at_video_secs[:n])
    flows = list(flow_per_sec[:n])
    norm_speed = _normalize(speeds)
    norm_flow = _normalize(flows)
    scores = []
    for i in range(n):
        if speeds[i] < cfg.min_speed_kmh:
            scores.append(0.0)  # standstill dropped outright
            continue
        s = cfg.gps_weight * float(norm_speed[i]) + cfg.flow_weight * float(norm_flow[i])
        scores.append(max(0.0, min(1.0, s)))
    return scores


def merge_segments(scores: list[float], cfg: Config) -> list[Segment]:
    """Turn per-second scores into kept segments, bridging small gaps."""
    kept = [i for i, s in enumerate(scores) if s >= cfg.score_cutoff]
    if not kept:
        return []
    bridge = int(round(cfg.gap_bridge_seconds))
    segments: list[Segment] = []
    run_start = kept[0]
    prev = kept[0]
    for idx in kept[1:]:
        if idx - prev <= bridge + 1:
            prev = idx
            continue
        segments.append((run_start, prev))
        run_start = idx
        prev = idx
    segments.append((run_start, prev))

    result: list[Segment] = []
    for a, b in segments:
        start = float(a)
        end = float(b + 1)  # inclusive second -> exclusive end
        if end - start < cfg.min_segment_seconds:
            continue
        avg = float(np.mean(scores[a:b + 1]))
        result.append(Segment(start=start, end=end, score=avg))
    return result


def compute_optical_flow_per_second(video_path: str, cfg: Config) -> list[float]:
    """Farneback optical flow sampled per second -> mean motion magnitude per second."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV could not open video: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(fps / cfg.flow_sample_fps)))

    per_second: dict[int, list[float]] = {}
    prev_gray = None
    frame_idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_idx % step == 0:
            gray = cv2.cvtColor(cv2.resize(frame, (320, 180)), cv2.COLOR_BGR2GRAY)
            if prev_gray is not None:
                flow = cv2.calcOpticalFlowFarneback(
                    prev_gray, gray, None, 0.5, 3, 15, 3, 5, 1.2, 0
                )
                mag = float(np.mean(np.linalg.norm(flow, axis=2)))
                sec = int(frame_idx / fps)
                per_second.setdefault(sec, []).append(mag)
            prev_gray = gray
        frame_idx += 1
    cap.release()

    if not per_second:
        return []
    duration_secs = max(per_second.keys()) + 1
    return [float(np.mean(per_second.get(s, [0.0]))) for s in range(duration_secs)]


def detect_highlights(video_path: str, gpx: GpxData, cfg: Config):
    """Sync GPS to video, run optical flow, score, and merge into segments."""
    video_start, _ = get_video_creation_time(video_path)
    offset = compute_offset_seconds(video_start, gpx.start_time)
    duration = int(round(get_video_duration(video_path)))

    speeds_at_video = [
        speed_at_video_time(float(t), offset, gpx.speeds_kmh) for t in range(duration)
    ]
    flow = compute_optical_flow_per_second(video_path, cfg)
    if not flow:
        flow = [0.0] * duration
    scores = score_seconds(speeds_at_video, flow, cfg)
    segments = merge_segments(scores, cfg)
    return segments, scores
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest test_sync.py -k "score_seconds or merge_segments" -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Run the full test file**

Run: `python -m pytest test_sync.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add highlight_detector.py test_sync.py
git commit -m "feat: optical flow, interest scoring, and segment merge"
```

---

## Task 5: CLI orchestration and `--dry-run` (Stage 1 end-to-end)

**Files:**
- Create: `main.py`
- Test: manual (dry-run) — no new pytest (I/O + argparse wiring).

**Interfaces:**
- Consumes: `Config`, `load_gpx`, `detect_highlights`, `check_ffmpeg`.
- Produces: `build_config_from_args(args) -> Config`; `print_segments(segments, scores)`; `main()` entrypoint.

- [ ] **Step 1: Write `main.py` with argparse and dry-run**

```python
from __future__ import annotations

import argparse
import sys

from config import Config
from highlight_detector import load_gpx, detect_highlights
from video_editor import check_ffmpeg


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate an MTB highlight video from action-cam footage + GPX telemetry."
    )
    p.add_argument("--video", required=True, help="Path to the source video file.")
    p.add_argument("--gpx", required=True, help="Path to the Strava/Garmin GPX export.")
    p.add_argument("--music", help="Path to a royalty-free MP3/AAC music track.")
    p.add_argument("--output", default="highlight.mp4", help="Output video path.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print detected segments and scores; render nothing.")
    p.add_argument("--cut-mode", choices=["reencode", "copy"], default="reencode")
    p.add_argument("--min-speed", type=float, help="Min speed km/h (standstill filter).")
    p.add_argument("--score-cutoff", type=float, help="Per-second interest score cutoff.")
    p.add_argument("--strava", action="store_true", help="Enrich intro with Strava stats.")
    p.add_argument("--strava-activity-id", help="Strava activity ID for stats.")
    p.add_argument("--garmin", action="store_true", help="Enrich intro with Garmin stats.")
    return p


def build_config_from_args(args) -> Config:
    cfg = Config(cut_mode=args.cut_mode)
    if args.min_speed is not None:
        cfg.min_speed_kmh = args.min_speed
    if args.score_cutoff is not None:
        cfg.score_cutoff = args.score_cutoff
    return cfg


def print_segments(segments, scores) -> None:
    print(f"\nPer-second interest scores ({len(scores)}s):")
    for t, s in enumerate(scores):
        bar = "#" * int(s * 20)
        print(f"  {t:4d}s  {s:0.2f} {bar}")
    print(f"\nDetected {len(segments)} segment(s) to keep:")
    total = 0.0
    for seg in segments:
        dur = seg.end - seg.start
        total += dur
        print(f"  {seg.start:7.1f}s -> {seg.end:7.1f}s  ({dur:4.1f}s)  score={seg.score:0.2f}")
    print(f"\nTotal kept: {total:.1f}s across {len(segments)} segment(s).")


def main() -> int:
    args = build_parser().parse_args()
    check_ffmpeg()
    cfg = build_config_from_args(args)

    gpx = load_gpx(args.gpx)
    segments, scores = detect_highlights(args.video, gpx, cfg)

    if args.dry_run:
        print_segments(segments, scores)
        return 0

    if not segments:
        print("No highlight segments detected; nothing to render.", file=sys.stderr)
        return 1

    # Stages 2 & 3 wired in Task 8.
    from video_editor import build_highlight_reel
    from intro_generator import build_final_video
    reel = build_highlight_reel(args.video, segments, args.music, cfg)
    build_final_video(reel, gpx, cfg, args.output, args)
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Verify the CLI parses and dry-run path is reachable**

Run: `python main.py --help`
Expected: usage text listing `--video`, `--gpx`, `--dry-run`, etc.

- [ ] **Step 3: Smoke-test dry-run with a fixture (no video available yet)**

Note: full dry-run needs a real video for optical flow. Verify argparse + config wiring only:

Run: `python -c "import main; p=main.build_parser(); a=p.parse_args(['--video','v.mp4','--gpx','r.gpx','--dry-run']); c=main.build_config_from_args(a); print(c.cut_mode, a.dry_run)"`
Expected: `reencode True`

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat: argparse CLI with Stage 1 orchestration and --dry-run"
```

---

## Task 6: Stage 2 — cut, concat, and music mixing (video_editor.py)

**Files:**
- Modify: `video_editor.py`
- Test: manual (requires real media) + one pure-function test for the filter string.

**Interfaces:**
- Consumes: `Config`, `Segment`, `check_ffmpeg`.
- Produces:
  - `build_highlight_reel(video_path: str, segments: list[Segment], music_path: str | None, cfg: Config) -> str` — returns path to the cut+concat+audio-mixed reel (no intro yet).
  - `_amix_filter(cfg: Config, has_music: bool, reel_duration: float) -> str` — builds the FFmpeg filtergraph string (unit-testable).

- [ ] **Step 1: Write a failing test for the audio filter builder**

Append to `test_sync.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_sync.py -k amix_filter -v`
Expected: FAIL — `ImportError: cannot import name '_amix_filter'`.

- [ ] **Step 3: Implement cut/concat/mix in `video_editor.py`**

Append to `video_editor.py`:

```python
import os
import tempfile

from config import Config
from highlight_detector import Segment


def _amix_filter(cfg: Config, has_music: bool, reel_duration: float) -> str:
    """Build the FFmpeg filtergraph for mixing original audio under music."""
    if not has_music:
        return ""
    fade_start = max(0.0, reel_duration - cfg.fade_out_seconds)
    return (
        f"[0:a]volume={cfg.original_audio_volume}[a0];"
        f"[1:a]volume={cfg.music_volume},"
        f"afade=t=out:st={fade_start}:d={cfg.fade_out_seconds}[a1];"
        f"[a0][a1]amix=inputs=2:duration=first:dropout_transition=0[aout]"
    )


def _cut_segment(video_path: str, seg: Segment, out_path: str, cfg: Config) -> None:
    dur = seg.end - seg.start
    if cfg.cut_mode == "copy":
        cmd = ["ffmpeg", "-y", "-ss", str(seg.start), "-i", video_path,
               "-t", str(dur), "-c", "copy", out_path]
    else:
        cmd = ["ffmpeg", "-y", "-ss", str(seg.start), "-i", video_path,
               "-t", str(dur), "-c:v", "libx264", "-preset", "veryfast",
               "-c:a", "aac", out_path]
    subprocess.run(cmd, check=True, capture_output=True)


def build_highlight_reel(video_path, segments, music_path, cfg: Config) -> str:
    """Cut selected segments, concat them, and mix music. Returns output path."""
    check_ffmpeg()
    workdir = tempfile.mkdtemp(prefix="rhe_")
    part_paths = []
    for i, seg in enumerate(segments):
        part = os.path.join(workdir, f"part_{i:03d}.mp4")
        _cut_segment(video_path, seg, part, cfg)
        part_paths.append(part)

    concat_list = os.path.join(workdir, "concat.txt")
    with open(concat_list, "w") as f:
        for p in part_paths:
            f.write(f"file '{p}'\n")

    reel = os.path.join(workdir, "reel.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
         "-c", "copy", reel],
        check=True, capture_output=True,
    )

    if not music_path:
        return reel

    reel_duration = sum(seg.end - seg.start for seg in segments)
    mixed = os.path.join(workdir, "reel_music.mp4")
    filtergraph = _amix_filter(cfg, has_music=True, reel_duration=reel_duration)
    subprocess.run(
        ["ffmpeg", "-y", "-i", reel, "-i", music_path,
         "-filter_complex", filtergraph,
         "-map", "0:v", "-map", "[aout]",
         "-c:v", "copy", "-c:a", "aac", "-shortest", mixed],
        check=True, capture_output=True,
    )
    return mixed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test_sync.py -k amix_filter -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add video_editor.py test_sync.py
git commit -m "feat: Stage 2 cut/concat and music mixing with fade-out"
```

---

## Task 7: Stage 3 — stats, reverse-geocode, MoviePy intro, final assembly

**Files:**
- Create: `intro_generator.py`
- Test: pure-function tests for stats formatting + geocode fallback.

**Interfaces:**
- Consumes: `Config`, `GpxData`; optional `strava_client`/`garmin_client` (Tasks 9–10, imported lazily).
- Produces:
  - `format_moving_time(seconds: float) -> str` (e.g. `"1h 23m"`).
  - `reverse_geocode(lat: float, lon: float) -> str` — returns a place string; on any failure returns `f"{lat:.4f}, {lon:.4f}"`.
  - `build_intro_clip(gpx: GpxData, cfg: Config, out_path: str, extra_stats: dict | None) -> str`.
  - `build_final_video(reel_path: str, gpx: GpxData, cfg: Config, output: str, args) -> str` — builds intro, gathers optional Strava/Garmin stats, re-encodes intro+reel concat.

- [ ] **Step 1: Write failing tests for formatting and geocode fallback**

Append to `test_sync.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest test_sync.py -k "moving_time or reverse_geocode" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'intro_generator'`.

- [ ] **Step 3: Implement `intro_generator.py`**

```python
from __future__ import annotations

import os
import subprocess
import tempfile

from config import Config
from highlight_detector import GpxData


def format_moving_time(seconds: float) -> str:
    total_min = int(seconds // 60)
    h, m = divmod(total_min, 60)
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m"


def _geocoder():
    from geopy.geocoders import Nominatim
    return Nominatim(user_agent="ride-highlight-editor")


def reverse_geocode(lat: float, lon: float) -> str:
    """Human-readable place for a coordinate; falls back to raw lat/lon on failure."""
    try:
        loc = _geocoder().reverse((lat, lon), language="en", timeout=10)
        if loc and loc.address:
            addr = loc.raw.get("address", {})
            town = (addr.get("city") or addr.get("town") or addr.get("village")
                    or addr.get("municipality"))
            region = addr.get("state") or addr.get("country")
            if town and region:
                return f"{town}, {region}"
            return loc.address.split(",")[0]
    except Exception:
        pass
    return f"{lat:.4f}, {lon:.4f}"


def build_intro_clip(gpx: GpxData, cfg: Config, out_path: str, extra_stats: dict | None = None) -> str:
    """Render a stats intro clip with MoviePy."""
    from moviepy.editor import TextClip, ColorClip, CompositeVideoClip

    location = reverse_geocode(*gpx.first_coord)
    date_str = gpx.start_time.strftime("%d %B %Y")
    lines = [
        location,
        date_str,
        f"{gpx.total_distance_km:.1f} km   +{gpx.elevation_gain_m:.0f} m",
        f"Moving time {format_moving_time(gpx.moving_time_s)}",
    ]
    if extra_stats:
        if extra_stats.get("avg_hr"):
            lines.append(f"Avg HR {extra_stats['avg_hr']} bpm")
        if extra_stats.get("avg_power"):
            lines.append(f"Avg Power {extra_stats['avg_power']} W")

    W, H = 1920, 1080
    bg = ColorClip(size=(W, H), color=(15, 15, 20)).set_duration(cfg.intro_duration)
    text = "\n".join(lines)
    txt = (TextClip(text, fontsize=70, color="white", font="Arial", method="label")
           .set_duration(cfg.intro_duration)
           .set_position("center"))
    clip = CompositeVideoClip([bg, txt]).set_duration(cfg.intro_duration)
    clip.write_videofile(out_path, fps=30, codec="libx264", audio=False, logger=None)
    return out_path


def _gather_extra_stats(gpx: GpxData, args) -> dict:
    stats: dict = {}
    if getattr(args, "strava", False):
        try:
            from strava_client import get_activity_stats
            stats.update(get_activity_stats(getattr(args, "strava_activity_id", None)))
        except Exception as e:
            print(f"[warn] Strava stats skipped: {e}")
    if getattr(args, "garmin", False):
        try:
            from garmin_client import get_activity_stats as garmin_stats
            stats.update(garmin_stats(gpx.start_time))
        except Exception as e:
            print(f"[warn] Garmin stats skipped: {e}")
    return stats


def build_final_video(reel_path: str, gpx: GpxData, cfg: Config, output: str, args) -> str:
    """Prepend the intro to the reel and re-encode the concat so params match."""
    workdir = tempfile.mkdtemp(prefix="rhe_final_")
    intro = os.path.join(workdir, "intro.mp4")
    extra = _gather_extra_stats(gpx, args)
    build_intro_clip(gpx, cfg, intro, extra)

    # Re-encode both into uniform params, then concat via filter (robust across cameras).
    subprocess.run(
        ["ffmpeg", "-y", "-i", intro, "-i", reel_path,
         "-filter_complex",
         "[0:v]scale=1920:1080,setsar=1,fps=30[v0];"
         "[1:v]scale=1920:1080,setsar=1,fps=30[v1];"
         "[v0][0:a?][v1][1:a?]concat=n=2:v=1:a=1[v][a]",
         "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", output],
        check=True, capture_output=True,
    )
    return output
```

Note: the intro has no audio; the concat's `[0:a?]` optional-stream syntax tolerates that by generating silence via `concat` only when present. If audio mapping fails on a silent intro, add `-f lavfi -t {intro_dur} -i anullsrc` as a third input — document this in README troubleshooting.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest test_sync.py -k "moving_time or reverse_geocode" -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add intro_generator.py test_sync.py
git commit -m "feat: Stage 3 stats intro, reverse-geocode, and final assembly"
```

---

## Task 8: Strava client (optional OAuth2 + token refresh)

**Files:**
- Create: `strava_client.py`
- Test: token-refresh logic with mocked requests.

**Interfaces:**
- Consumes: nothing internal.
- Produces:
  - `TOKEN_FILE = ".strava_token.json"`.
  - `is_configured() -> bool` — True if `.strava_token.json` and client env vars exist.
  - `_refresh_if_needed(token: dict, now_epoch: float) -> dict` — refreshes when `expires_at <= now`.
  - `get_activity_stats(activity_id: str | None) -> dict` — returns `{"avg_hr", "avg_power", ...}`; raises if not configured (caller catches).

- [ ] **Step 1: Write failing test for token refresh decision**

Append to `test_sync.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest test_sync.py -k refresh_if_needed -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'strava_client'`.

- [ ] **Step 3: Implement `strava_client.py`**

```python
from __future__ import annotations

import json
import os
import time

import requests

TOKEN_FILE = ".strava_token.json"
_API = "https://www.strava.com/api/v3"
_OAUTH = "https://www.strava.com/oauth/token"


def is_configured() -> bool:
    return (os.path.exists(TOKEN_FILE)
            and bool(os.environ.get("STRAVA_CLIENT_ID"))
            and bool(os.environ.get("STRAVA_CLIENT_SECRET")))


def _load_token() -> dict:
    with open(TOKEN_FILE) as f:
        return json.load(f)


def _save_token(token: dict) -> None:
    with open(TOKEN_FILE, "w") as f:
        json.dump(token, f)


def _refresh_if_needed(token: dict, now_epoch: float) -> dict:
    if token.get("expires_at", 0) > now_epoch:
        return token
    resp = requests.post(_OAUTH, data={
        "client_id": os.environ["STRAVA_CLIENT_ID"],
        "client_secret": os.environ["STRAVA_CLIENT_SECRET"],
        "grant_type": "refresh_token",
        "refresh_token": token["refresh_token"],
    }, timeout=30)
    resp.raise_for_status()
    new = resp.json()
    token.update({
        "access_token": new["access_token"],
        "refresh_token": new["refresh_token"],
        "expires_at": new["expires_at"],
    })
    return token


def get_activity_stats(activity_id: str | None) -> dict:
    if not is_configured():
        raise RuntimeError("Strava not configured (.strava_token.json + env vars).")
    token = _refresh_if_needed(_load_token(), now_epoch=time.time())
    _save_token(token)
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    if not activity_id:
        acts = requests.get(f"{_API}/athlete/activities", headers=headers,
                            params={"per_page": 1}, timeout=30).json()
        if not acts:
            return {}
        activity_id = acts[0]["id"]
    a = requests.get(f"{_API}/activities/{activity_id}", headers=headers, timeout=30).json()
    return {
        "avg_hr": a.get("average_heartrate"),
        "avg_power": a.get("average_watts"),
        "max_speed_kmh": (a.get("max_speed") or 0) * 3.6,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest test_sync.py -k refresh_if_needed -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add strava_client.py test_sync.py
git commit -m "feat: optional Strava OAuth2 client with token refresh"
```

---

## Task 9: Garmin client (optional) + README + final verification

**Files:**
- Create: `garmin_client.py`
- Create: `README.md`
- Test: `is_configured` gate test.

**Interfaces:**
- Consumes: nothing internal.
- Produces:
  - `is_configured() -> bool` — True if `GARMIN_EMAIL` and `GARMIN_PASSWORD` env vars set.
  - `get_activity_stats(activity_start) -> dict` — returns `{"avg_hr", "avg_power"}`; raises if unconfigured (caller catches).

- [ ] **Step 1: Write failing test for the config gate**

Append to `test_sync.py`:

```python
def test_garmin_not_configured_by_default(monkeypatch):
    import garmin_client
    monkeypatch.delenv("GARMIN_EMAIL", raising=False)
    monkeypatch.delenv("GARMIN_PASSWORD", raising=False)
    assert garmin_client.is_configured() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_sync.py -k garmin_not_configured -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'garmin_client'`.

- [ ] **Step 3: Implement `garmin_client.py`**

```python
from __future__ import annotations

import os
from datetime import datetime


def is_configured() -> bool:
    return bool(os.environ.get("GARMIN_EMAIL")) and bool(os.environ.get("GARMIN_PASSWORD"))


def get_activity_stats(activity_start: datetime) -> dict:
    """Fetch HR/power for the activity on the given date. Raises if unconfigured."""
    if not is_configured():
        raise RuntimeError("Garmin not configured (set GARMIN_EMAIL / GARMIN_PASSWORD).")
    from garminconnect import Garmin

    client = Garmin(os.environ["GARMIN_EMAIL"], os.environ["GARMIN_PASSWORD"])
    client.login()
    date_str = activity_start.strftime("%Y-%m-%d")
    activities = client.get_activities_by_date(date_str, date_str)
    if not activities:
        return {}
    a = activities[0]
    return {
        "avg_hr": a.get("averageHR"),
        "avg_power": a.get("avgPower"),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test_sync.py -k garmin_not_configured -v`
Expected: PASS.

- [ ] **Step 5: Write `README.md`**

````markdown
# ride-highlight-editor

Automatically generate highlight videos from mountainbike action-cam footage,
using GPS/telemetry from a Strava or Garmin **GPX export** synced to the video
timeline via the video's creation timestamp. Camera-agnostic (Insta360, GoPro,
phone, anything).

## Requirements

- macOS with Python 3.11–3.14
- FFmpeg via Homebrew:

  ```bash
  brew install ffmpeg
  ```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> Python 3.14 is new; if `opencv-python`/`moviepy` wheels are unavailable for
> your interpreter, use Python 3.11 or 3.12 in the venv.

## Usage

```bash
# Dry run — print detected segments and scores, render nothing:
python main.py --video ride.mp4 --gpx ride.gpx --dry-run

# Full render with music:
python main.py --video ride.mp4 --gpx ride.gpx --music track.mp3 --output highlight.mp4

# Frame-accurate (default) vs fast keyframe copy:
python main.py --video ride.mp4 --gpx ride.gpx --cut-mode copy
```

Tunable flags: `--min-speed`, `--score-cutoff`, `--cut-mode`.
Deeper tuning lives in `config.py`.

## Optional: Strava stats

1. Create an API app at https://www.strava.com/settings/api → note Client ID/Secret.
2. Complete the OAuth2 flow once and save the tokens to `.strava_token.json`:

   ```json
   {"access_token": "...", "refresh_token": "...", "expires_at": 0}
   ```

3. Export credentials and run with `--strava`:

   ```bash
   export STRAVA_CLIENT_ID=xxxxx
   export STRAVA_CLIENT_SECRET=xxxxx
   python main.py --video ride.mp4 --gpx ride.gpx --strava
   ```

Tokens refresh automatically. `.strava_token.json` is gitignored.

## Optional: Garmin Connect stats

```bash
export GARMIN_EMAIL="you@example.com"
export GARMIN_PASSWORD="your-password"
python main.py --video ride.mp4 --gpx ride.gpx --garmin
```

Both integrations are optional and skipped gracefully if unconfigured; API
failures log a warning and the pipeline continues with GPX-only stats.

## Tests

```bash
python -m pytest test_sync.py -v
```

## Troubleshooting

- **"ffmpeg not found"** → `brew install ffmpeg`.
- **Intro/reel concat audio error** → some intros are silent; if the final
  concat fails on the audio map, the intro can be given a silent track via
  `anullsrc` (see `intro_generator.build_final_video`).
- **Segments look off by a second or two** → the video's `creation_time` tag
  may be missing (falls back to file mtime). Prefer footage with intact metadata.
````

- [ ] **Step 6: Run the full test suite**

Run: `python -m pytest test_sync.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add garmin_client.py README.md test_sync.py
git commit -m "feat: optional Garmin client, README, full test suite green"
```

---

## Self-Review

**Spec coverage:**
- Stage 1 (GPX speed, ffprobe timestamp, offset sync, Farneback flow, combined score, segment filtering) → Tasks 2, 3, 4. ✓
- Stage 2 (`-c copy` toggle, concat, amix music mixing, music fade-out, MP3/AAC input) → Task 6. ✓ (default is re-encode per approved design; copy is a toggle.)
- Stage 3 (7s MoviePy intro, date/location reverse-geocode, distance/elevation/moving-time, optional Strava/Garmin, prepend to final) → Task 7. ✓
- Module list (`main`, `highlight_detector`, `video_editor`, `intro_generator`, `strava_client`, `garmin_client`, `config`, `requirements.txt`, `README.md`) → all created. ✓
- Constraints: FFmpeg presence check (Task 3), local-only (design), optional integrations graceful (Tasks 7–9), `--dry-run` (Task 5), `test_sync.py` synthetic GPX sync test (Task 2). ✓
- Pinned deps (Task 1). ✓

**Placeholder scan:** No TBD/TODO. The `anullsrc` fallback is documented as a concrete conditional, not a placeholder.

**Type consistency:** `Segment(start, end, score)` used consistently across Tasks 4/6/7; `Config` field names match Task 1 across all consumers; `get_activity_stats` returns a `dict` consumed by `_gather_extra_stats`.
