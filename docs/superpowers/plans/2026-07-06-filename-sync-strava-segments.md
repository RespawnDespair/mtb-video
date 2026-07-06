# Filename sync + Strava-segment highlights Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the MP4 filename timestamp as a sync-offset source, and a Strava-segment-driven highlight mode where each qualifying segment effort becomes a clip with a lower-third stats overlay.

**Architecture:** Part A adds filename parsing and a flow-free `resolve_sync_offset` (extends the offset precedence with a "filename" source). Part B fetches Strava `segment_efforts`, maps them to video-time clips via the resolved offset (`segment_detector.py`), and renders each clip with an FFmpeg `drawtext`/`drawbox` lower-third (`video_editor.build_segment_reel`). `main.py` gains `--mode {auto,segments,flow}` routing.

**Tech Stack:** Python 3.11–3.14, numpy, opencv-python, FFmpeg (drawtext/drawbox), requests, existing modules, pytest.

## Global Constraints

- Offset precedence: `--sync-offset` (manual) > `--auto-sync` (auto) > `creation_time` metadata > filename > file mtime.
- Filename pattern: `VID_YYYYMMDD_HHMMSS` (e.g. `VID_20260705_144402_00_003.mp4`); parsed as **naive local** time, localized via the system timezone to UTC.
- Segment filter (`is_noteworthy`): keep an effort if it has any achievement OR `pr_rank` is set OR the segment is starred.
- `--mode auto` (default): segment mode when Strava is configured, `--strava-activity-id` given, and ≥1 qualifying clip falls in the video window; else flow mode. `segments`: force (error if unavailable). `flow`: force existing detection.
- Segment mode resolves the offset WITHOUT computing optical flow unless `--auto-sync` is set.
- Segment clips are cut re-encoded (overlay requires it); flow-mode behavior unchanged.
- Lower-third: semi-transparent band + segment name + stats line `"m:ss · X.X km/u · N W · N bpm"`, omitting missing power/HR.
- Tests use pytest; run via `.venv/bin/python`. The venv at `.venv` (Python 3.14.5) has all deps; do NOT recreate it.
- All commits end with: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## Existing interfaces (already committed)

- `highlight_detector.py`: `GpxData` (fields incl. `start_time`, `speeds_kmh`), `get_video_creation_time(path)->(datetime,bool)`, `get_video_duration(path)->float`, `compute_offset_seconds(video_start, activity_start)->float`, `compute_optical_flow_per_second(video_path,cfg)->list[float]`, `estimate_offset_by_motion(flow,gpx,cfg)->(float,float)`, `speed_at_video_time(t,offset,speeds)->float`, `score_seconds(...)`, `merge_segments(...)`, `Segment(start,end,score)`, `Analysis` dataclass, `_resolve_offset(metadata_offset,from_metadata,auto_offset,offset_override,use_auto)->(float,str)`, `analyze_video(video_path,gpx,cfg,offset_override=None,use_auto=False)->Analysis`.
- `strava_client.py`: `is_configured()`, `_load_token()`, `_refresh_if_needed(token,now_epoch)`, `_save_token(token)`, `get_activity_stats(activity_id)`, module consts `_API="https://www.strava.com/api/v3"`, `TOKEN_FILE`. Auth pattern: refresh token, then `headers={"Authorization": f"Bearer {token['access_token']}"}`.
- `video_editor.py`: `check_ffmpeg()`, `_amix_filter(cfg,has_music,reel_duration)->str`, `_cut_segment(video_path,seg,out_path,cfg)`, `build_highlight_reel(video_path,segments,music_path,cfg)->str`.
- `main.py`: `build_parser()`, `build_config_from_args(args)->Config`, `print_segments(segments,scores)`, `print_inspection(analysis,gpx)`, `render_sparkline(values,width)`, `resolve_offset_args(args)->(float|None,bool)`, `main()`.
- `config.py`: `Config` with `min_segment_seconds`, `cut_mode`, `sparkline_columns`, audio/intro fields.

---

## Task 1: Filename timestamp parsing + precedence with "filename" source

**Files:**
- Modify: `highlight_detector.py`
- Test: `test_sync.py`

**Interfaces:**
- Produces:
  - `parse_filename_datetime(name: str) -> datetime | None` — naive local datetime from `VID_YYYYMMDD_HHMMSS`; `None` if no match.
  - `get_filename_timestamp(path: str) -> datetime | None` — localizes the naive time to UTC (tz-aware); `None` if unparseable.
  - Updated `_resolve_offset(metadata_offset, from_metadata, filename_offset, auto_offset, offset_override, use_auto) -> tuple[float, str]` — source ∈ `{"manual","auto","metadata","filename","mtime"}`.

- [ ] **Step 1: Write failing tests**

Append to `test_sync.py`:

```python
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
```

Also UPDATE the four pre-existing `_resolve_offset` tests (from the sync-debug feature) to the new signature by inserting `filename_offset=None` (or positional `None`) as the third argument. Find tests named `test_resolve_offset_manual_wins`, `test_resolve_offset_auto_when_requested`, `test_resolve_offset_metadata_default`, `test_resolve_offset_mtime_source_when_not_from_metadata` and add the `filename_offset` argument (value `None`) in the correct position.

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `.venv/bin/python -m pytest test_sync.py -k "parse_filename or resolve_offset" -v`
Expected: FAIL — `ImportError: cannot import name 'parse_filename_datetime'` (and the updated old tests fail on the new signature until Step 3).

- [ ] **Step 3: Implement**

In `highlight_detector.py`, add near the imports:

```python
import os
import re
from datetime import timezone

_FILENAME_TS_RE = re.compile(r"VID_(\d{8})_(\d{6})")
```

(If `os`/`re`/`timezone` are already imported, don't duplicate.)

Add the functions:

```python
def parse_filename_datetime(name: str) -> "datetime | None":
    """Parse VID_YYYYMMDD_HHMMSS from a filename into a naive local datetime."""
    m = _FILENAME_TS_RE.search(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def get_filename_timestamp(path: str) -> "datetime | None":
    """Filename timestamp localized to tz-aware UTC via the system timezone."""
    naive = parse_filename_datetime(os.path.basename(path))
    if naive is None:
        return None
    # Interpret as local wall-clock time, then convert to UTC.
    local = naive.astimezone()  # attaches system local tz to a naive datetime
    return local.astimezone(timezone.utc)
```

REPLACE `_resolve_offset` with the filename-aware version:

```python
def _resolve_offset(metadata_offset, from_metadata, filename_offset,
                    auto_offset, offset_override, use_auto):
    """Pick the offset by precedence: manual > auto > metadata > filename > mtime."""
    if offset_override is not None:
        return float(offset_override), "manual"
    if use_auto:
        return float(auto_offset), "auto"
    if from_metadata:
        return float(metadata_offset), "metadata"
    if filename_offset is not None:
        return float(filename_offset), "filename"
    return float(metadata_offset), "mtime"
```

Note: `analyze_video` currently calls `_resolve_offset` with the OLD 5-arg signature. To keep the module importable and flow-mode working until Task 2 refactors it, update that single call site in `analyze_video` now to pass `filename_offset=None` positionally:
`_resolve_offset(metadata_offset, from_metadata, None, auto_offset, offset_override, use_auto)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest test_sync.py -k "parse_filename or resolve_offset" -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest test_sync.py -q`
Expected: all PASS (prior 38 + new, minus none removed).

- [ ] **Step 6: Commit**

```bash
git add highlight_detector.py test_sync.py
git commit -m "feat: filename timestamp sync source + filename precedence"
```

---

## Task 2: resolve_sync_offset (flow-free) + Analysis.filename_offset

**Files:**
- Modify: `highlight_detector.py`
- Test: `test_sync.py`

**Interfaces:**
- Produces:
  - `@dataclass ResolvedOffset`: `offset_used: float`, `offset_source: str`, `metadata_offset: float`, `filename_offset: float | None`, `auto_offset: float`, `auto_correlation: float`, `video_creation_time: datetime`, `from_metadata: bool`.
  - `resolve_sync_offset(video_path, gpx, cfg, offset_override=None, use_auto=False, flow=None) -> ResolvedOffset` — computes metadata/filename/mtime offsets always; computes the auto (flow) offset only when `use_auto` is True (using `flow` if provided, else computing optical flow); resolves via `_resolve_offset`.
  - `Analysis` gains field `filename_offset: float | None`.
- Consumes: `get_video_creation_time`, `get_filename_timestamp`, `compute_offset_seconds`, `estimate_offset_by_motion`, `compute_optical_flow_per_second`, `_resolve_offset`.

- [ ] **Step 1: Write a failing test (flow-free path)**

Append to `test_sync.py`:

```python
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
```

(`_gpx_with_speeds` already exists in test_sync.py from the sync-debug feature.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest test_sync.py -k resolve_sync_offset -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_sync_offset'`.

- [ ] **Step 3: Implement ResolvedOffset + resolve_sync_offset; refactor analyze_video**

Add to `highlight_detector.py`:

```python
@dataclass
class ResolvedOffset:
    offset_used: float
    offset_source: str
    metadata_offset: float
    filename_offset: "float | None"
    auto_offset: float
    auto_correlation: float
    video_creation_time: datetime
    from_metadata: bool


def resolve_sync_offset(video_path, gpx, cfg, offset_override=None,
                        use_auto=False, flow=None) -> ResolvedOffset:
    """Resolve the sync offset without computing optical flow unless auto is used."""
    video_start, from_metadata = get_video_creation_time(video_path)
    metadata_offset = compute_offset_seconds(video_start, gpx.start_time)

    fname_dt = get_filename_timestamp(video_path)
    filename_offset = (compute_offset_seconds(fname_dt, gpx.start_time)
                       if fname_dt is not None else None)

    auto_offset, auto_corr = 0.0, 0.0
    if use_auto:
        f = flow if flow is not None else compute_optical_flow_per_second(video_path, cfg)
        auto_offset, auto_corr = estimate_offset_by_motion(f, gpx, cfg)

    offset_used, source = _resolve_offset(
        metadata_offset, from_metadata, filename_offset,
        auto_offset, offset_override, use_auto,
    )
    return ResolvedOffset(
        offset_used=offset_used, offset_source=source,
        metadata_offset=metadata_offset, filename_offset=filename_offset,
        auto_offset=auto_offset, auto_correlation=auto_corr,
        video_creation_time=video_start, from_metadata=from_metadata,
    )
```

Add `filename_offset: "float | None"` to the `Analysis` dataclass (after `metadata_offset`).

REPLACE the body of `analyze_video` so it computes flow once and delegates offset resolution:

```python
def analyze_video(video_path, gpx: GpxData, cfg: Config,
                  offset_override=None, use_auto=False) -> Analysis:
    """Compute flow once, resolve the sync offset, score, and merge (flow mode)."""
    duration = int(round(get_video_duration(video_path)))
    flow = compute_optical_flow_per_second(video_path, cfg)
    if not flow:
        flow = [0.0] * duration

    r = resolve_sync_offset(video_path, gpx, cfg, offset_override, use_auto, flow=flow)

    speeds_at_video = [
        speed_at_video_time(float(t), r.offset_used, gpx.speeds_kmh) for t in range(duration)
    ]
    scores = score_seconds(speeds_at_video, flow, cfg)
    segments = merge_segments(scores, cfg)

    fps = 0.0
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        cap.release()
    except Exception:
        pass

    return Analysis(
        flow_per_sec=flow, speeds_at_video=speeds_at_video, scores=scores,
        segments=segments, offset_used=r.offset_used, offset_source=r.offset_source,
        metadata_offset=r.metadata_offset, filename_offset=r.filename_offset,
        auto_offset=r.auto_offset, auto_correlation=r.auto_correlation,
        video_creation_time=r.video_creation_time, from_metadata=r.from_metadata,
        video_duration=float(duration), fps=fps,
    )
```

(Ensure the `Analysis(...)` construction lists `filename_offset` — matching the new field. `detect_highlights` remains the thin wrapper returning `(a.segments, a.scores)`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest test_sync.py -k "resolve_sync_offset or resolve_offset or parse_filename" -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest test_sync.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add highlight_detector.py test_sync.py
git commit -m "feat: flow-free resolve_sync_offset + filename_offset in Analysis"
```

---

## Task 3: Strava segment-efforts fetch

**Files:**
- Modify: `strava_client.py`
- Test: `test_sync.py`

**Interfaces:**
- Produces: `get_segment_efforts(activity_id: str) -> list[dict]` — the activity's `segment_efforts` list. Raises `RuntimeError` if not configured.
- Consumes: `is_configured`, `_load_token`, `_refresh_if_needed`, `_save_token`, `_API`.

- [ ] **Step 1: Write a failing test (mocked requests)**

Append to `test_sync.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest test_sync.py -k get_segment_efforts -v`
Expected: FAIL — `AttributeError: module 'strava_client' has no attribute 'get_segment_efforts'`.

- [ ] **Step 3: Implement**

Append to `strava_client.py`:

```python
def get_segment_efforts(activity_id: str) -> list:
    """Return the activity's segment_efforts (each with timing + stats)."""
    if not is_configured():
        raise RuntimeError("Strava not configured (.strava_token.json + env vars).")
    token = _refresh_if_needed(_load_token(), now_epoch=time.time())
    _save_token(token)
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    resp = requests.get(f"{_API}/activities/{activity_id}", headers=headers,
                        params={"include_all_efforts": True}, timeout=30)
    resp.raise_for_status()
    return resp.json().get("segment_efforts", []) or []
```

(`time` and `requests` are already imported in strava_client.py.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest test_sync.py -k get_segment_efforts -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add strava_client.py test_sync.py
git commit -m "feat: fetch Strava segment_efforts (include_all_efforts)"
```

---

## Task 4: segment_detector.py — efforts → clips + stats

**Files:**
- Create: `segment_detector.py`
- Test: `test_sync.py`

**Interfaces:**
- Consumes: `Config` (uses `min_segment_seconds`), `GpxData.start_time`.
- Produces:
  - `@dataclass SegmentEffort`: `name: str`, `start_date: datetime` (UTC), `elapsed_time: float`, `distance_m: float`, `average_watts: float | None`, `average_heartrate: float | None`, `pr_rank: int | None`, `has_achievement: bool`, `starred: bool`.
  - `parse_efforts(raw: list[dict]) -> list[SegmentEffort]`.
  - `is_noteworthy(effort: SegmentEffort) -> bool`.
  - `@dataclass SegmentClip`: `start: float`, `end: float`, `name: str`, `stats: dict`.
  - `efforts_to_clips(efforts: list[SegmentEffort], gpx_start: datetime, offset_seconds: float, video_duration: float, cfg: Config) -> list[SegmentClip]`.
  - `format_segment_stats(clip: SegmentClip) -> str`.

- [ ] **Step 1: Write failing tests**

Append to `test_sync.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest test_sync.py -k "efforts or noteworthy or segment_stats or parse_efforts" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'segment_detector'`.

- [ ] **Step 3: Implement `segment_detector.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class SegmentEffort:
    name: str
    start_date: datetime
    elapsed_time: float
    distance_m: float
    average_watts: "float | None" = None
    average_heartrate: "float | None" = None
    pr_rank: "int | None" = None
    has_achievement: bool = False
    starred: bool = False


@dataclass
class SegmentClip:
    start: float
    end: float
    name: str
    stats: dict = field(default_factory=dict)


def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_efforts(raw: list) -> list:
    """Normalize raw Strava segment_effort dicts into SegmentEffort objects."""
    out = []
    for e in raw:
        seg = e.get("segment") or {}
        out.append(SegmentEffort(
            name=e.get("name") or seg.get("name") or "Segment",
            start_date=_parse_dt(e["start_date"]),
            elapsed_time=float(e.get("elapsed_time") or 0.0),
            distance_m=float(e.get("distance") or 0.0),
            average_watts=e.get("average_watts"),
            average_heartrate=e.get("average_heartrate"),
            pr_rank=e.get("pr_rank"),
            has_achievement=bool(e.get("achievements")),
            starred=bool(seg.get("starred")),
        ))
    return out


def is_noteworthy(effort: SegmentEffort) -> bool:
    """Keep efforts with an achievement, a PR rank, or a starred segment."""
    return effort.has_achievement or effort.pr_rank is not None or effort.starred


def efforts_to_clips(efforts, gpx_start: datetime, offset_seconds: float,
                     video_duration: float, cfg) -> list:
    """Map noteworthy segment efforts to video-time clips within the video window."""
    clips = []
    for e in efforts:
        if not is_noteworthy(e):
            continue
        activity_start_s = (e.start_date - gpx_start).total_seconds()
        video_start = activity_start_s - offset_seconds
        start = max(0.0, video_start)
        end = min(video_duration, video_start + e.elapsed_time)
        if end - start < cfg.min_segment_seconds:
            continue
        speed_kmh = (e.distance_m / e.elapsed_time * 3.6) if e.elapsed_time > 0 else 0.0
        stats = {
            "elapsed_s": e.elapsed_time,
            "speed_kmh": speed_kmh,
            "power_w": round(e.average_watts) if e.average_watts is not None else None,
            "hr_bpm": round(e.average_heartrate) if e.average_heartrate is not None else None,
        }
        clips.append(SegmentClip(start=start, end=end, name=e.name, stats=stats))
    clips.sort(key=lambda c: c.start)
    return clips


def format_segment_stats(clip: SegmentClip) -> str:
    """A stats line like '4:10 · 16.3 km/u · 175 W · 153 bpm' (missing fields omitted)."""
    s = clip.stats
    parts = []
    elapsed = int(s.get("elapsed_s") or 0)
    parts.append(f"{elapsed // 60}:{elapsed % 60:02d}")
    if s.get("speed_kmh"):
        parts.append(f"{s['speed_kmh']:.1f} km/u")
    if s.get("power_w") is not None:
        parts.append(f"{s['power_w']} W")
    if s.get("hr_bpm") is not None:
        parts.append(f"{s['hr_bpm']} bpm")
    return " · ".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest test_sync.py -k "efforts or noteworthy or segment_stats or parse_efforts" -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest test_sync.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add segment_detector.py test_sync.py
git commit -m "feat: segment_detector — efforts to video clips + stats"
```

---

## Task 5: Lower-third overlay + build_segment_reel (shared concat/music helper)

**Files:**
- Modify: `config.py`, `video_editor.py`
- Test: `test_sync.py`

**Interfaces:**
- Consumes: `Config`, `SegmentClip`, `format_segment_stats`, `check_ffmpeg`, `_amix_filter`.
- Produces:
  - `config.py`: `overlay_font_path: str` (macOS default), `overlay_band_opacity: float = 0.5`, `overlay_name_fontsize: int = 48`, `overlay_stats_fontsize: int = 32`.
  - `video_editor._escape_drawtext(text: str) -> str`.
  - `video_editor._lower_third_filter(name: str, stats: str, cfg) -> str` — a filtergraph string with a `drawbox` band + two `drawtext` lines.
  - `video_editor._concat_and_music(part_paths, music_path, reel_duration, workdir, cfg) -> str` — shared concat + optional music mix (extracted from `build_highlight_reel`).
  - `video_editor.build_segment_reel(video_path, clips, music_path, cfg) -> str`.
  - `build_highlight_reel` refactored to call `_concat_and_music` (unchanged behavior).

- [ ] **Step 1: Add config fields**

In `config.py` `Config`, add (new Stage-2 group lines):

```python
    # Segment overlay (lower-third)
    overlay_font_path: str = "/System/Library/Fonts/Supplemental/Arial.ttf"
    overlay_band_opacity: float = 0.5
    overlay_name_fontsize: int = 48
    overlay_stats_fontsize: int = 32
```

- [ ] **Step 2: Write failing tests for the filter builders**

Append to `test_sync.py`:

```python
from video_editor import _escape_drawtext, _lower_third_filter


def test_escape_drawtext_escapes_specials():
    out = _escape_drawtext("Rider's: 100% cool\\bad")
    assert "\\'" in out          # single quote escaped
    assert "\\:" in out          # colon escaped
    assert "\\%" in out or "%%" in out  # percent escaped


def test_lower_third_filter_contains_band_and_texts():
    cfg = Config()
    f = _lower_third_filter("MTB Goeree Roggebos", "4:10 · 16.3 km/u · 175 W", cfg)
    assert "drawbox" in f
    assert "drawtext" in f
    assert "Goeree" in f          # name present (escaped form still contains it)
    assert "16.3" in f            # stats line present
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest test_sync.py -k "drawtext or lower_third" -v`
Expected: FAIL — `ImportError: cannot import name '_escape_drawtext'`.

- [ ] **Step 4: Implement in `video_editor.py`**

Add (near the top, after existing imports — `os`, `tempfile`, `subprocess`, `Config`, `Segment` are already imported):

```python
from segment_detector import format_segment_stats


def _escape_drawtext(text: str) -> str:
    """Escape characters special to FFmpeg drawtext text values."""
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "\\'")
    text = text.replace("%", "\\%")
    return text


def _lower_third_filter(name: str, stats: str, cfg: Config) -> str:
    """Build a filtergraph: a semi-transparent lower band + name and stats lines."""
    font = cfg.overlay_font_path
    band_h = cfg.overlay_name_fontsize + cfg.overlay_stats_fontsize + 60
    box = (f"drawbox=x=0:y=ih-{band_h}:w=iw:h={band_h}:"
           f"color=black@{cfg.overlay_band_opacity}:t=fill")
    name_txt = (f"drawtext=fontfile='{font}':text='{_escape_drawtext(name)}':"
                f"x=40:y=ih-{band_h}+20:fontsize={cfg.overlay_name_fontsize}:"
                f"fontcolor=white")
    stats_txt = (f"drawtext=fontfile='{font}':text='{_escape_drawtext(stats)}':"
                 f"x=40:y=ih-{cfg.overlay_stats_fontsize}-20:"
                 f"fontsize={cfg.overlay_stats_fontsize}:fontcolor=white")
    return f"{box},{name_txt},{stats_txt}"
```

Extract the concat + music logic from `build_highlight_reel` into a shared helper, then have `build_highlight_reel` call it. Add:

```python
def _concat_and_music(part_paths, music_path, reel_duration, workdir, cfg: Config) -> str:
    """Concat pre-cut parts, then optionally mix music with fade-out. Returns path."""
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

Refactor `build_highlight_reel` so that, after cutting the segment parts into `part_paths` in `workdir`, it returns `_concat_and_music(part_paths, music_path, sum(seg.end-seg.start for seg in segments), workdir, cfg)` instead of its own inline concat/mix block. Keep the cutting loop and `check_ffmpeg()` call as-is.

Add `build_segment_reel`:

```python
def build_segment_reel(video_path, clips, music_path, cfg: Config) -> str:
    """Cut each segment clip with a lower-third overlay, concat, and mix music."""
    check_ffmpeg()
    workdir = tempfile.mkdtemp(prefix="rhe_seg_")
    part_paths = []
    for i, clip in enumerate(clips):
        part = os.path.join(workdir, f"seg_{i:03d}.mp4")
        stats = format_segment_stats(clip)
        vf = _lower_third_filter(clip.name, stats, cfg)
        dur = clip.end - clip.start
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(clip.start), "-i", video_path,
             "-t", str(dur), "-vf", vf,
             "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", part],
            check=True, capture_output=True,
        )
        part_paths.append(part)
    reel_duration = sum(c.end - c.start for c in clips)
    return _concat_and_music(part_paths, music_path, reel_duration, workdir, cfg)
```

- [ ] **Step 5: Run the filter-builder tests**

Run: `.venv/bin/python -m pytest test_sync.py -k "drawtext or lower_third" -v`
Expected: PASS.

- [ ] **Step 6: Add a real-ffmpeg overlay smoke test**

Append to `test_sync.py`:

```python
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
```

- [ ] **Step 7: Run the overlay smoke test + full suite**

Run: `.venv/bin/python -m pytest test_sync.py -k "lower_third_renders" -v`
Expected: PASS (not skipped on this machine — ffmpeg is installed; drawtext must succeed with the configured font).
Then: `.venv/bin/python -m pytest test_sync.py -q`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add config.py video_editor.py test_sync.py
git commit -m "feat: lower-third overlay + build_segment_reel with shared concat/music helper"
```

---

## Task 6: main.py mode routing + inspect filename source + README

**Files:**
- Modify: `main.py`, `README.md`
- Test: manual (routing) + the existing suite must stay green.

**Interfaces:**
- Consumes: `resolve_sync_offset`, `analyze_video`, `load_gpx`, `check_ffmpeg`, `build_config_from_args`, `resolve_offset_args`, `get_video_duration`, `strava_client.get_segment_efforts`, `segment_detector.parse_efforts/efforts_to_clips/format_segment_stats`, `video_editor.build_segment_reel/build_highlight_reel`, `intro_generator.build_final_video`.
- Produces: `--mode {auto,segments,flow}` flag; `select_segment_clips(args, gpx, cfg, offset) -> list[SegmentClip] | None`; segment-mode branch in `main()`; filename line in `print_inspection`.

- [ ] **Step 1: Add the `--mode` flag**

In `build_parser()`:

```python
    p.add_argument("--mode", choices=["auto", "segments", "flow"], default="auto",
                   help="Highlight source: auto (segments if available else flow), "
                        "segments (Strava segments only), or flow (speed/motion).")
```

- [ ] **Step 2: Add filename source to `print_inspection`**

In `print_inspection`, within the `=== Sync ===` block, add a filename line after the metadata line:

```python
    fo = a.filename_offset
    print(f"  filename offset: {('%+.1f s' % fo) if fo is not None else 'n/a'}")
```

- [ ] **Step 3: Add the segment-clip selector**

Add to `main.py`:

```python
def select_segment_clips(args, gpx, cfg, offset_seconds):
    """Fetch + map Strava segment efforts to clips, or None if unavailable."""
    from highlight_detector import get_video_duration
    try:
        import strava_client
        from segment_detector import parse_efforts, efforts_to_clips
        if not strava_client.is_configured() or not args.strava_activity_id:
            return None
        raw = strava_client.get_segment_efforts(args.strava_activity_id)
        efforts = parse_efforts(raw)
        duration = get_video_duration(args.video)
        clips = efforts_to_clips(efforts, gpx.start_time, offset_seconds, duration, cfg)
        return clips or None
    except Exception as e:
        print(f"[warn] Strava segments unavailable: {e}", file=sys.stderr)
        return None
```

- [ ] **Step 4: Rewrite `main()` to route by mode**

Replace `main()` with:

```python
def main() -> int:
    args = build_parser().parse_args()
    check_ffmpeg()
    cfg = build_config_from_args(args)

    gpx = load_gpx(args.gpx)
    offset_override, use_auto = resolve_offset_args(args)

    from highlight_detector import resolve_sync_offset

    if args.inspect:
        analysis = analyze_video(args.video, gpx, cfg,
                                 offset_override=offset_override, use_auto=use_auto)
        if analysis.offset_source == "mtime":
            print(f"[warn] {args.video} has no creation_time metadata; using file mtime "
                  "for sync — alignment may be approximate.", file=sys.stderr)
        print_inspection(analysis, gpx)
        return 0

    # Resolve the offset flow-free unless --auto-sync (avoids optical flow on big clips).
    r = resolve_sync_offset(args.video, gpx, cfg,
                            offset_override=offset_override, use_auto=use_auto)
    if r.offset_source == "mtime":
        print(f"[warn] {args.video} has no creation_time metadata; using file mtime "
              "for sync — alignment may be approximate.", file=sys.stderr)

    clips = None
    if args.mode in ("auto", "segments"):
        clips = select_segment_clips(args, gpx, cfg, r.offset_used)
        if args.mode == "segments" and not clips:
            print("Segment mode requested but no Strava segments available "
                  "(need --strava-activity-id, configured Strava, and segments in "
                  "the video window).", file=sys.stderr)
            return 1

    if clips:  # segment mode
        if args.dry_run:
            print(f"Segment highlights ({len(clips)}):")
            for c in clips:
                from segment_detector import format_segment_stats
                print(f"  {c.start:7.1f}s -> {c.end:7.1f}s  {c.name}  "
                      f"[{format_segment_stats(c)}]")
            return 0
        from video_editor import build_segment_reel
        from intro_generator import build_final_video
        reel = build_segment_reel(args.video, clips, args.music, cfg)
        try:
            build_final_video(reel, gpx, cfg, args.output, args)
        finally:
            import os, shutil
            shutil.rmtree(os.path.dirname(reel), ignore_errors=True)
        print(f"Wrote {args.output}")
        return 0

    # flow mode (fallback / --mode flow)
    analysis = analyze_video(args.video, gpx, cfg,
                             offset_override=offset_override, use_auto=use_auto)
    segments, scores = analysis.segments, analysis.scores
    if args.dry_run:
        print_segments(segments, scores)
        return 0
    if not segments:
        print("No highlight segments detected; nothing to render.", file=sys.stderr)
        return 1
    from video_editor import build_highlight_reel
    from intro_generator import build_final_video
    reel = build_highlight_reel(args.video, segments, args.music, cfg)
    try:
        build_final_video(reel, gpx, cfg, args.output, args)
    finally:
        import os, shutil
        shutil.rmtree(os.path.dirname(reel), ignore_errors=True)
    print(f"Wrote {args.output}")
    return 0
```

Note: flow mode computes optical flow (via `analyze_video`); segment mode does not (offset resolved flow-free). The duplicate mtime-warning is acceptable (inspect vs render paths).

- [ ] **Step 5: Verify parsing + full suite**

Run: `.venv/bin/python main.py --help`
Expected: usage shows `--mode {auto,segments,flow}`.
Run: `.venv/bin/python -m pytest test_sync.py -q`
Expected: all PASS.

- [ ] **Step 6: Manual smoke — flow fallback still works (no Strava activity id)**

Let `SP=/private/tmp/claude-501/-Users-Jelle-Tigchelaar-git-mtb-video/a50afca1-eb59-40a3-ad61-11a286923478/scratchpad`.
Run: `.venv/bin/python main.py --video $SP/ride.mp4 --gpx $SP/ride.gpx --dry-run`
Expected: with no `--strava-activity-id`, `auto` mode falls back to flow; prints the per-second scores + segments as before (exit 0).

- [ ] **Step 7: Update README**

Add a "Highlight modes" subsection under Usage:

````markdown
### Highlight modes

By default (`--mode auto`) the tool uses your **Strava segments** as highlights when
available, otherwise it falls back to speed/motion detection.

```bash
# Segment mode: one clip per noteworthy segment (medal/PR/starred) with a
# lower-third summary (name · time · speed · power · HR). Needs Strava + activity id:
python main.py --video ride.mp4 --gpx ride.gpx --music track.mp3 \
    --strava --strava-activity-id 1234567890 --mode segments

# Force the classic speed/motion mode:
python main.py --video ride.mp4 --gpx ride.gpx --mode flow
```

Segment timing uses the same sync offset as the rest of the pipeline
(`--inspect` / `--auto-sync` / `--sync-offset`). The filename timestamp
(`VID_YYYYMMDD_HHMMSS`) is used as a sync source when `creation_time` metadata is
missing.
````

- [ ] **Step 8: Commit**

```bash
git add main.py README.md
git commit -m "feat: --mode routing (auto/segments/flow) + filename source in --inspect"
```

---

## Self-Review

**Spec coverage:**
- Part A filename parse + localize + precedence + Analysis.filename_offset + inspect line → Tasks 1, 2, 6. ✓
- Flow-free `resolve_sync_offset` → Task 2; used by segment mode in Task 6. ✓
- Strava `get_segment_efforts` (include_all_efforts) → Task 3. ✓
- `segment_detector` (parse_efforts, is_noteworthy medal/PR/starred, efforts_to_clips video-time mapping + filter + sort, format_segment_stats) → Task 4. ✓
- Lower-third overlay (drawbox+drawtext, escaping, font config) + build_segment_reel + shared concat/music helper → Task 5. ✓
- `--mode auto/segments/flow` routing, segment dry-run, error when segments forced but unavailable → Task 6. ✓
- Config overlay fields → Task 5. ✓
- Tests: filename parse, precedence, flow-free resolve, efforts fetch, efforts_to_clips/is_noteworthy/stats, filter builders + real-ffmpeg overlay smoke → Tasks 1–5. ✓
- README modes + filename note → Task 6. ✓

**Placeholder scan:** No TBD/TODO; every code step carries complete code.

**Type consistency:** `_resolve_offset` new 6-arg signature is defined in Task 1 and its sole caller (`analyze_video`) is updated in Task 1, then `analyze_video` is rebuilt in Task 2 to call it via `resolve_sync_offset`. `ResolvedOffset` fields (Task 2) are consumed by `main()` (Task 6: `r.offset_used`, `r.offset_source`). `Analysis.filename_offset` (Task 2) is read by `print_inspection` (Task 6). `SegmentClip(start,end,name,stats)` consistent across Tasks 4/5/6. `efforts_to_clips(efforts, gpx_start, offset_seconds, video_duration, cfg)` signature identical in Task 4 def and Task 6 call. `build_segment_reel(video_path, clips, music_path, cfg)` consistent Tasks 5/6. `_concat_and_music(part_paths, music_path, reel_duration, workdir, cfg)` consistent within Task 5.
