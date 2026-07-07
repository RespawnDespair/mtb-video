# Multiple video files for one ride — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Accept several video files that jointly cover one ride: keep the existing segment/highlight selection, resolve which source file(s) cover each selected ride-time range, cut from the right file, and render only the covered parts.

**Architecture:** Two paths. One `--video` file → the **existing** code path runs completely unchanged. Multiple files → a **new parallel parts-based path**: `clip_sources.build_clip_sources` places each file on the ride timeline (own recording time + one shared `--sync-offset` correction); selection produces ride-time ranges; `clip_sources.resolve_render_parts` maps ranges onto source coverage; `video_editor.build_reel_from_parts` cuts each part from its source (HUD per part) and concatenates in ride order.

**Tech Stack:** Python 3.14 (`.venv`), FFmpeg (`-ss/-t`, scale, overlay, concat demuxer), numpy, pytest.

## Global Constraints

- **Backward compatibility is paramount.** With exactly one `--video` file, behaviour is byte-for-byte the current behaviour (existing `build_highlight_reel`/`build_segment_reel`/`build_final_video`/`analyze_video`/`resolve_sync_offset`, `--cut-mode copy`, absolute `--sync-offset` override). The multi-file path is separate and only runs when `len(args.video) > 1`.
- Ride-time (activity seconds) is the common currency between selection and source resolution. Per file: `ride_t = local_t + base_offset`, `base_offset = (own recording-time offset) + (shared --sync-offset correction, default 0)`.
- Coverage rules: range fully in one file → one part; partially covered → clamp to coverage; spanning a boundary → one part per covered piece; no coverage → drop + warn; two files covering the same ride second → keep earliest-start (then longest), never render a ride-second twice.
- Existing suite is 117 tests and must stay green. Use `.venv/bin/python`. Real-ffmpeg tests use `@pytest.mark.skipif(shutil.which("ffmpeg") is None, ...)`.
- `git add` only the files each task changes (stray `*.mp3`/scratchpad files exist — never `git add -A`).
- Commit messages end with a blank line then: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## Existing interfaces (verified against current code)

- `highlight_detector.resolve_sync_offset(video_path, gpx, cfg, offset_override=None, use_auto=False) -> ResolvedOffset` (fields `offset_used`, `offset_source`, `video_creation_time`, …). `get_video_duration(path)->float`, `get_video_resolution(path)->(w,h)`. `Segment(start,end,score)`. `analyze_video(video_path, gpx, cfg, offset_override=, use_auto=) -> Analysis` (`.segments`, `.scores`).
- `segment_detector`: `SegmentEffort`, `SegmentClip(start,end,name,stats)`, `parse_efforts`, `is_noteworthy`, `efforts_to_clips(efforts, gpx_start, offset_seconds, video_duration, cfg)`, `format_segment_stats(clip)`.
- `video_editor`: `_cut_segment(video_path, seg, out_path, cfg)`, `_render_hud_pngs(video_path, clip, source, gpx, offset_seconds, workdir, cfg)`, `_segment_coords(gpx, clip, offset_seconds)`, `_concat_and_music(part_paths, music_path, reel_duration, workdir, cfg)`, `build_highlight_reel(video_path, segments, music_path, cfg)`, `build_segment_reel(video_path, clips, music_path, cfg, gpx=None, offset_seconds=0.0, telemetry_source=None)`, `_lower_third_filter`, `_run_ffmpeg_progress`, `check_ffmpeg`.
- `intro_select.curviest_window(turn, offset_seconds, video_duration, clip_len)`, `heading_change_per_sec(coords, speeds_kmh)`.
- `intro_generator.build_intro_clip(gpx, cfg, out_path, extra_stats=None, size=(1920,1080), video_path=None, offset_seconds=0.0, gpx_path=None)`, `build_final_video(reel_path, gpx, cfg, output, args, offset_seconds=0.0)`.
- `main.py`: `--video` single, `resolve_output_height(value, video_path)`, `select_segment_clips(args, gpx, cfg, offset_seconds)`, segment/flow branches in `main()`.

---

## Task 1: clip_sources.py — sources on the ride timeline + parts resolution

**Files:** Create `clip_sources.py`; Test `test_sync.py`.

**Interfaces produced:**
- `ClipSource(path:str, base_offset:float, duration:float, width:int, height:int, creation_time, offset_source:str)`.
- `RenderPart(source_path:str, local_start:float, local_end:float, base_offset:float, name:"str|None"=None, stats:"dict|None"=None)`.
- `build_clip_sources(video_paths, gpx, cfg, offset_override=None, use_auto=False) -> list[ClipSource]`.
- `resolve_render_parts(sources, ranges) -> tuple[list[RenderPart], list[str]]` where `ranges` is `list[(activity_start, activity_end, name, stats)]`.

- [ ] **Step 1: Failing tests** — append to `test_sync.py`:

```python
def _src(base, dur, path="x.mp4", w=1920, h=1080):
    import clip_sources
    from datetime import datetime, timezone
    return clip_sources.ClipSource(path=path, base_offset=base, duration=dur, width=w,
                                   height=h, creation_time=datetime(2026,7,5,tzinfo=timezone.utc),
                                   offset_source="metadata")


def test_render_parts_fully_inside_one_source():
    import clip_sources
    s = _src(100.0, 60.0, "A.mp4")           # covers ride 100..160
    parts, dropped = clip_sources.resolve_render_parts([s], [(110.0, 130.0, "seg", {})])
    assert dropped == []
    assert len(parts) == 1
    p = parts[0]
    assert p.source_path == "A.mp4"
    assert abs(p.local_start - 10.0) < 1e-6 and abs(p.local_end - 30.0) < 1e-6
    assert p.base_offset == 100.0 and p.name == "seg"


def test_render_parts_partial_coverage_is_clamped():
    import clip_sources
    s = _src(100.0, 60.0, "A.mp4")           # 100..160
    parts, dropped = clip_sources.resolve_render_parts([s], [(150.0, 200.0, "seg", {})])
    assert len(parts) == 1 and dropped == []
    assert abs(parts[0].local_start - 50.0) < 1e-6
    assert abs(parts[0].local_end - 60.0) < 1e-6   # clamped to source end (160 ride)


def test_render_parts_spanning_two_sources_splits_in_order():
    import clip_sources
    a = _src(0.0, 100.0, "A.mp4")            # 0..100
    b = _src(100.0, 100.0, "B.mp4")          # 100..200
    parts, dropped = clip_sources.resolve_render_parts([a, b], [(80.0, 140.0, "seg", {})])
    assert dropped == [] and len(parts) == 2
    assert parts[0].source_path == "A.mp4" and abs(parts[0].local_start - 80.0) < 1e-6
    assert abs(parts[0].local_end - 100.0) < 1e-6
    assert parts[1].source_path == "B.mp4" and abs(parts[1].local_start - 0.0) < 1e-6
    assert abs(parts[1].local_end - 40.0) < 1e-6


def test_render_parts_no_coverage_is_dropped_and_reported():
    import clip_sources
    s = _src(0.0, 50.0, "A.mp4")             # 0..50
    parts, dropped = clip_sources.resolve_render_parts([s], [(100.0, 120.0, "ver weg", {})])
    assert parts == [] and dropped == ["ver weg"]


def test_render_parts_overlapping_sources_render_each_second_once():
    import clip_sources
    a = _src(0.0, 120.0, "A.mp4")            # 0..120
    b = _src(60.0, 120.0, "B.mp4")           # 60..180 (overlaps A on 60..120)
    parts, dropped = clip_sources.resolve_render_parts([a, b], [(50.0, 170.0, "seg", {})])
    # union of covered ride time == 50..170; no ride-second twice
    covered = sorted((p.base_offset + p.local_start, p.base_offset + p.local_end) for p in parts)
    total = sum(e - s for s, e in covered)
    assert abs(total - 120.0) < 1e-6            # 170-50, counted once
    # intervals must not overlap
    for (s1, e1), (s2, e2) in zip(covered, covered[1:]):
        assert e1 <= s2 + 1e-6


def test_render_parts_sorted_by_ride_time():
    import clip_sources
    a = _src(0.0, 50.0, "A.mp4")
    b = _src(100.0, 50.0, "B.mp4")
    parts, _ = clip_sources.resolve_render_parts([a, b],
        [(120.0, 140.0, "late", {}), (10.0, 20.0, "vroeg", {})])
    assert [p.name for p in parts] == ["vroeg", "late"]


def test_build_clip_sources_shared_correction_and_sort(monkeypatch):
    import clip_sources, highlight_detector
    from datetime import datetime, timezone
    from types import SimpleNamespace
    bases = {"B.mp4": 1000.0, "A.mp4": 300.0}
    monkeypatch.setattr(highlight_detector, "resolve_sync_offset",
        lambda path, gpx, cfg, offset_override=None, use_auto=False: SimpleNamespace(
            offset_used=bases[path], offset_source="metadata",
            video_creation_time=datetime(2026,7,5,tzinfo=timezone.utc)))
    monkeypatch.setattr(clip_sources, "get_video_duration", lambda p: 60.0)
    monkeypatch.setattr(clip_sources, "get_video_resolution", lambda p: (1920, 1080))
    srcs = clip_sources.build_clip_sources(["B.mp4", "A.mp4"], gpx=object(), cfg=object(),
                                           offset_override=577.0)
    assert [s.path for s in srcs] == ["A.mp4", "B.mp4"]        # sorted by base_offset
    assert srcs[0].base_offset == 300.0 + 577.0                # correction added per file
    assert srcs[1].base_offset == 1000.0 + 577.0
```

- [ ] **Step 2: Run — FAIL** (`ModuleNotFoundError: clip_sources`):
`.venv/bin/python -m pytest test_sync.py -k "render_parts or build_clip_sources" -v`

- [ ] **Step 3: Implement `clip_sources.py`:**

```python
from __future__ import annotations

import sys
from dataclasses import dataclass, field

from highlight_detector import (get_video_duration, get_video_resolution,
                                 resolve_sync_offset)

_EPS = 1e-6


@dataclass
class ClipSource:
    path: str
    base_offset: float          # ride-seconds at this file's local t=0
    duration: float
    width: int
    height: int
    creation_time: object
    offset_source: str


@dataclass
class RenderPart:
    source_path: str
    local_start: float
    local_end: float
    base_offset: float          # ride_t = local_t + base_offset (for HUD/telemetry)
    name: "str | None" = None
    stats: "dict | None" = None


def build_clip_sources(video_paths, gpx, cfg, offset_override=None,
                       use_auto=False) -> list:
    """Place each file on the ride timeline via its own recording-time offset plus one
    shared additive correction (offset_override). Multi-file path only."""
    correction = float(offset_override or 0.0)
    sources = []
    for path in video_paths:
        r = resolve_sync_offset(path, gpx, cfg, offset_override=None, use_auto=use_auto)
        w, h = get_video_resolution(path)
        sources.append(ClipSource(
            path=path, base_offset=r.offset_used + correction,
            duration=get_video_duration(path), width=w, height=h,
            creation_time=r.video_creation_time, offset_source=r.offset_source))
    sources.sort(key=lambda s: s.base_offset)

    res = {(s.width, s.height) for s in sources}
    if len(res) > 1:
        print(f"[warn] videobestanden hebben verschillende resoluties {sorted(res)}; "
              "ze worden naar de doelhoogte geschaald.", file=sys.stderr)
    for a, b in zip(sources, sources[1:]):
        if b.base_offset < a.base_offset + a.duration - _EPS:
            print(f"[warn] overlappende dekking tussen {a.path} en {b.path}; "
                  "overlap wordt eenmalig gerenderd.", file=sys.stderr)
    return sources


def resolve_render_parts(sources, ranges):
    """Map ride-time ranges onto source coverage. Returns (parts, dropped_names).
    Never renders a ride-second twice (earliest/longest source wins on overlap)."""
    parts = []
    dropped = []
    ordered = sorted(sources, key=lambda s: (s.base_offset, -s.duration))
    for (a_start, a_end, name, stats) in ranges:
        cursor = a_start          # earliest ride-second still needing coverage
        made = False
        for s in ordered:
            cov0, cov1 = s.base_offset, s.base_offset + s.duration
            ov0 = max(cursor, cov0)
            ov1 = min(a_end, cov1)
            if ov1 - ov0 > _EPS:
                parts.append(RenderPart(
                    source_path=s.path, local_start=ov0 - s.base_offset,
                    local_end=ov1 - s.base_offset, base_offset=s.base_offset,
                    name=name, stats=stats))
                made = True
                cursor = ov1      # advance past what we just covered
            if cursor >= a_end - _EPS:
                break
        if not made:
            dropped.append(name)
    parts.sort(key=lambda p: p.base_offset + p.local_start)
    return parts, dropped
```

- [ ] **Step 4: Run — PASS** (`-k "render_parts or build_clip_sources"`). **Step 5: Full suite** (`.venv/bin/python -m pytest test_sync.py -q`; 117 → 125). **Step 6: Commit** `feat: clip_sources — place files on the ride + resolve render parts`.

---

## Task 2: efforts_to_activity_ranges — selection in ride time

**Files:** Modify `segment_detector.py`; Test `test_sync.py`.

**Interfaces produced:** `efforts_to_activity_ranges(efforts, gpx_start, cfg) -> list[tuple]` → `(activity_start, activity_end, name, stats)`.

- [ ] **Step 1: Failing test** — append to `test_sync.py`:

```python
def test_efforts_to_activity_ranges():
    from datetime import datetime, timezone
    from segment_detector import SegmentEffort, efforts_to_activity_ranges

    class C: min_segment_seconds = 3.0
    start = datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc)
    efforts = [
        SegmentEffort(name="Afdaling", start_date=datetime(2026,7,5,12,5,0,tzinfo=timezone.utc),
                      elapsed_time=40.0, distance_m=200.0, starred=True),
        SegmentEffort(name="Kort", start_date=datetime(2026,7,5,12,10,0,tzinfo=timezone.utc),
                      elapsed_time=1.0, distance_m=5.0, starred=True),        # < min -> dropped
        SegmentEffort(name="Saai", start_date=datetime(2026,7,5,12,20,0,tzinfo=timezone.utc),
                      elapsed_time=30.0, distance_m=100.0),                    # not noteworthy
    ]
    ranges = efforts_to_activity_ranges(efforts, start, C())
    assert len(ranges) == 1
    a0, a1, name, stats = ranges[0]
    assert abs(a0 - 300.0) < 1e-6 and abs(a1 - 340.0) < 1e-6
    assert name == "Afdaling" and stats["elapsed_s"] == 40.0
```

- [ ] **Step 2: Run — FAIL** (`ImportError: efforts_to_activity_ranges`):
`.venv/bin/python -m pytest test_sync.py -k efforts_to_activity_ranges -v`

- [ ] **Step 3: Implement** — add to `segment_detector.py` (after `efforts_to_clips`):

```python
def efforts_to_activity_ranges(efforts, gpx_start: datetime, cfg) -> list:
    """Noteworthy efforts as ride-time ranges (activity_start, activity_end, name, stats).
    No offset/duration clamping here — coverage clamping happens in resolve_render_parts."""
    ranges = []
    for e in efforts:
        if not is_noteworthy(e):
            continue
        if e.elapsed_time < cfg.min_segment_seconds:
            continue
        a0 = (e.start_date - gpx_start).total_seconds()
        speed_kmh = (e.distance_m / e.elapsed_time * 3.6) if e.elapsed_time > 0 else 0.0
        stats = {
            "elapsed_s": e.elapsed_time,
            "speed_kmh": speed_kmh,
            "power_w": round(e.average_watts) if e.average_watts is not None else None,
            "hr_bpm": round(e.average_heartrate) if e.average_heartrate is not None else None,
        }
        ranges.append((a0, a0 + e.elapsed_time, e.name, stats))
    ranges.sort(key=lambda r: r[0])
    return ranges
```

- [ ] **Step 4: Run — PASS.** **Step 5: Full suite** (126). **Step 6: Commit** `feat: efforts_to_activity_ranges — Strava efforts as ride-time ranges`.

---

## Task 3: build_reel_from_parts — cut per source, HUD per part, concat

**Files:** Modify `video_editor.py`; Test `test_sync.py`.

**Interfaces produced:** `build_reel_from_parts(parts, cfg, target_size, gpx=None, telemetry_source=None) -> str`.

Notes on generalising existing helpers: `_render_hud_pngs` and `_segment_coords` currently take a shared `offset_seconds`; give them a per-call `base_offset` and (for HUD) the part's source path + `target_size` (so HUD frames are sized to the reel target, not the raw source). Keep `build_highlight_reel`/`build_segment_reel` and their current helper calls intact (single-file path); add the generalised path additively.

- [ ] **Step 1: Failing test** — append to `test_sync.py`:

```python
@pytest.mark.skipif(_sh6.which("ffmpeg") is None or _sh6.which("ffprobe") is None,
                    reason="ffmpeg not installed")
def test_build_reel_from_parts_two_sources(tmp_path):
    import subprocess, video_editor, clip_sources
    from config import Config
    a = tmp_path / "A.mp4"; b = tmp_path / "B.mp4"
    subprocess.run(["ffmpeg","-y","-f","lavfi","-i","testsrc=s=320x240:d=5",
                    "-f","lavfi","-i","sine=d=5","-c:v","libx264","-pix_fmt","yuv420p",
                    "-c:a","aac","-shortest",str(a)], check=True, capture_output=True)
    subprocess.run(["ffmpeg","-y","-f","lavfi","-i","testsrc2=s=640x480:d=5",
                    "-f","lavfi","-i","sine=frequency=300:d=5","-c:v","libx264",
                    "-pix_fmt","yuv420p","-c:a","aac","-shortest",str(b)], check=True, capture_output=True)
    parts = [
        clip_sources.RenderPart(source_path=str(a), local_start=1.0, local_end=3.0, base_offset=0.0),
        clip_sources.RenderPart(source_path=str(b), local_start=0.0, local_end=2.0, base_offset=100.0),
    ]
    cfg = Config(); cfg.hud_enabled = False
    reel = video_editor.build_reel_from_parts(parts, cfg, target_size=(640, 480))
    d = float(subprocess.run(["ffprobe","-v","error","-show_entries","format=duration",
                              "-of","csv=p=0",reel], capture_output=True, text=True).stdout)
    assert abs(d - 4.0) < 0.4       # 2s + 2s
    codecs = subprocess.run(["ffprobe","-v","error","-show_entries","stream=codec_type",
                             "-of","csv=p=0",reel], capture_output=True, text=True).stdout.split()
    assert "video" in codecs and "audio" in codecs
```

- [ ] **Step 2: Run — FAIL** (`AttributeError: build_reel_from_parts`):
`.venv/bin/python -m pytest test_sync.py -k build_reel_from_parts -v`

- [ ] **Step 3: Implement** — add to `video_editor.py`. First a generalised HUD renderer that takes an explicit source path, base offset, and target size:

```python
def _render_hud_pngs_for_part(part, source, gpx, target_size, workdir, cfg):
    """HUD PNG sequence for a RenderPart, sized to target_size, telemetry at
    activity_t = local_t + part.base_offset. Returns the printf pattern path."""
    import os
    W, H = target_size
    clip = _PartClip(part.local_start, part.local_end, part.name, part.stats or {})
    seg_start_activity = part.local_start + part.base_offset
    seg_coords = _segment_coords(gpx, clip, part.base_offset)
    date_str = gpx.start_time.strftime("%d-%m-%Y")
    hud_dir = os.path.join(workdir, os.path.basename(workdir) + "_hud")
    os.makedirs(hud_dir, exist_ok=True)
    dur = clip.end - clip.start
    n_frames = max(1, int(round(dur * cfg.hud_fps)))
    for i in range(n_frames):
        activity_t = clip.start + (i / cfg.hud_fps) + part.base_offset
        sample = sample_telemetry(source, activity_t, seg_start_activity)
        frame = hud_renderer.render_hud_frame(sample, clip.name, seg_coords, (W, H), cfg, date_str)
        frame.save(os.path.join(hud_dir, f"hud_{i:06d}.png"))
    return os.path.join(hud_dir, "hud_%06d.png")
```

Add a tiny clip shim and the builder:

```python
from dataclasses import dataclass


@dataclass
class _PartClip:
    start: float
    end: float
    name: "str | None"
    stats: dict


def build_reel_from_parts(parts, cfg: Config, target_size, gpx=None,
                          telemetry_source=None) -> str:
    """Cut each RenderPart from its own source, scale to target_size so parts from
    different files concatenate, overlay HUD/lower-third when the part is named, and
    concat in order. Music is applied later (final stage), so none here."""
    check_ffmpeg()
    W, H = target_size
    workdir = tempfile.mkdtemp(prefix="rhe_parts_")
    scale = f"scale={W}:{H},setsar=1"
    source = telemetry_source if telemetry_source is not None else gpx
    use_hud = cfg.hud_enabled and gpx is not None
    part_paths = []
    n = len(parts)
    for i, part in enumerate(parts):
        out = os.path.join(workdir, f"part_{i:03d}.mp4")
        dur = part.local_end - part.local_start
        if part.name is not None and use_hud:
            _log(f"[{i+1}/{n}] {part.name} ({dur:.1f}s) — HUD…")
            try:
                pattern = _render_hud_pngs_for_part(part, source, gpx, (W, H), workdir, cfg)
                cmd = ["ffmpeg", "-y", "-ss", str(part.local_start), "-t", str(dur),
                       "-i", part.source_path, "-framerate", str(cfg.hud_fps), "-i", pattern,
                       "-filter_complex", f"[0:v]{scale}[v0];[v0][1:v]overlay=0:0:shortest=1[v]",
                       "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
                       "-c:a", "aac", out]
                _run_ffmpeg_progress(cmd, dur, f"[{i+1}/{n}] {part.name} — overlay")
                part_paths.append(out)
                continue
            except Exception as e:
                print(f"[warn] HUD render failed ({e}); falling back to lower-third.")
        if part.name is not None:
            vf = f"{scale}," + _lower_third_filter(part.name, format_segment_stats(
                _PartClip(part.local_start, part.local_end, part.name, part.stats or {})), cfg)
        else:
            vf = scale
        subprocess.run(["ffmpeg", "-y", "-ss", str(part.local_start), "-i", part.source_path,
                        "-t", str(dur), "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
                        "-c:a", "aac", out], check=True, capture_output=True)
        part_paths.append(out)

    reel_duration = sum(p.local_end - p.local_start for p in parts)
    return _concat_and_music(part_paths, None, reel_duration, workdir, cfg)
```

Note: `format_segment_stats` accepts anything with a `.stats` dict, so `_PartClip` works. `_segment_coords(gpx, clip, base_offset)` already computes `clip.start+base_offset … clip.end+base_offset` — correct for a part.

- [ ] **Step 4: Run — PASS** (`-k build_reel_from_parts`). **Step 5: Full suite** (127) + `.venv/bin/python -c "import video_editor, clip_sources"`. **Step 6: Commit** `feat: build_reel_from_parts — multi-source cut + per-part HUD`.

---

## Task 4: intro across sources

**Files:** Modify `intro_select.py`, `intro_generator.py`; Test `test_sync.py`.

**Interfaces produced:** `intro_select.curviest_window_across(sources, turn, clip_len) -> tuple[str, float]` (source_path, local_start). `build_intro_clip(..., sources=None)`; `build_final_video(..., sources=None)`.

- [ ] **Step 1: Failing test** — append to `test_sync.py`:

```python
def test_curviest_window_across_picks_best_source():
    import intro_select
    from test_sync import _src  # helper from Task 1
    a = _src(0.0, 10.0, "A.mp4")      # ride 0..10
    b = _src(100.0, 10.0, "B.mp4")    # ride 100..110
    turn = [0.0] * 200
    for t in range(100, 108):         # curvy stretch lives in B's span
        turn[t] = 5.0
    path, local = intro_select.curviest_window_across([a, b], turn, clip_len=4.0)
    assert path == "B.mp4"
    assert 0.0 <= local <= 6.0
```

- [ ] **Step 2: Run — FAIL** (`AttributeError: curviest_window_across`):
`.venv/bin/python -m pytest test_sync.py -k curviest_window_across -v`

- [ ] **Step 3: Implement** — add to `intro_select.py`:

```python
def curviest_window_across(sources, turn, clip_len):
    """Across all sources' covered ride spans, pick the (source_path, local_start) whose
    clip_len window has the highest summed heading change. Falls back to the first
    source at local 0.0 if nothing scores."""
    clip = int(round(clip_len))
    best = (None, 0.0, -1.0)          # (path, local_start, score)
    for s in sources:
        lo = int(round(s.base_offset))
        hi = int(round(s.base_offset + s.duration)) - clip
        for a in range(max(0, lo), hi + 1):
            if a + clip > len(turn):
                break
            score = sum(turn[a:a + clip])
            if score > best[2]:
                best = (s.path, float(a - s.base_offset), score)
    if best[0] is None:
        return (sources[0].path, 0.0) if sources else ("", 0.0)
    return best[0], max(0.0, best[1])
```

- [ ] **Step 4:** In `intro_generator.build_intro_clip`, add `sources=None` to the signature and, in the background-selection block (currently guarded by `if video_path and get_video_duration(video_path) >= cfg.intro_duration:`), when `sources` is provided pick the source+window across all files:

```python
        # 2. background: curviest clip across sources (multi) or the single video
        bg_clip = None
        turn = heading_change_per_sec(gpx.coords, gpx.speeds_kmh)
        if sources:
            from intro_select import curviest_window_across
            bg_path, vt = curviest_window_across(sources, turn, cfg.intro_duration)
            bg_source, bg_dur = bg_path, cfg.intro_duration
        elif video_path and get_video_duration(video_path) >= cfg.intro_duration:
            vt = curviest_window(turn, offset_seconds, get_video_duration(video_path), cfg.intro_duration)
            bg_source, bg_dur = video_path, cfg.intro_duration
        else:
            bg_source = None
        if bg_source:
            try:
                bg_clip = os.path.join(workdir, "bg.mp4")
                subprocess.run(["ffmpeg", "-y", "-ss", str(vt), "-t", str(cfg.intro_duration),
                                "-i", bg_source, "-vf", f"scale={W}:{H}", "-an", bg_clip],
                               check=True, capture_output=True)
            except Exception as e:
                print(f"[warn] intro background clip unavailable ({e}); using solid background.")
                bg_clip = None
```

(Keep the existing `heading_change_per_sec`/`curviest_window` imports at the top of the function.)

- [ ] **Step 5:** In `intro_generator.build_final_video`, add `sources=None`. When `sources` is given, derive `(W, H)` from the sources scaled to `cfg.output_height` instead of `get_video_resolution(args.video)`, and pass `sources` into `build_intro_clip`:

```python
def build_final_video(reel_path, gpx, cfg, output, args, offset_seconds=0.0, sources=None):
    from highlight_detector import get_video_resolution
    if sources:
        sw, sh = sources[0].width, sources[0].height
    else:
        sw, sh = get_video_resolution(args.video[0] if isinstance(args.video, list) else args.video)
    W, H = _target_dims(sw, sh, cfg.output_height)
    workdir = tempfile.mkdtemp(prefix="rhe_final_")
    try:
        intro = os.path.join(workdir, "intro.mp4")
        extra = _gather_extra_stats(gpx, args)
        build_intro_clip(gpx, cfg, intro, extra, size=(W, H),
                         video_path=(None if sources else (args.video[0] if isinstance(args.video, list) else args.video)),
                         offset_seconds=offset_seconds, gpx_path=getattr(args, "gpx", None),
                         sources=sources)
        music = getattr(args, "music", None)
        target = os.path.join(workdir, "combined.mp4") if music else output
        _concat_intro_and_reel(intro, reel_path, cfg.intro_duration, target, W, H)
        if music:
            _apply_music(target, output, args, cfg)
        return output
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
```

(Adapt to the actual current body — only add the `sources` branch and the `args.video` list-safety; leave the rest identical.)

- [ ] **Step 6: Run — PASS** (`-k curviest_window_across`). **Step 7: Full suite** (128) + import check. **Step 8: Commit** `feat: intro background chosen across all source files`.

---

## Task 5: main.py — multiple --video files, orchestration + single-file regression

**Files:** Modify `main.py`; Test `test_sync.py`.

- [ ] **Step 1:** `--video` accepts multiple paths. Change the argument to:

```python
    p.add_argument("--video", required=True, nargs="+",
                   help="One or more source video files that jointly cover the ride.")
```

Everywhere the single-file body reads `args.video`, use `args.video[0]`. In `resolve_output_height(args.output_height, args.video[0])` (line ~224), `resolve_sync_offset(args.video[0], ...)`, `analyze_video(args.video[0], ...)`, `select_segment_clips` (uses `get_video_duration(args.video)` → `args.video[0]`), the `[warn] {args.video[0]} …` messages, and `build_segment_reel(args.video[0], …)` / `build_highlight_reel(args.video[0], …)`.

- [ ] **Step 2:** Add the multi-file guard near the top of `main()` (after `gpx`/offset resolution, before the single-file `if args.inspect:` block). Extract the current single-file body into a helper `_run_single(args, cfg, gpx, offset_override, use_auto)` returning an int, OR wrap: `if len(args.video) > 1: return _run_multi(args, cfg, gpx, offset_override, use_auto)`. Implement `_run_multi`:

```python
def _run_multi(args, cfg, gpx, offset_override, use_auto) -> int:
    import sys
    from clip_sources import build_clip_sources, resolve_render_parts
    from video_editor import build_reel_from_parts
    from intro_generator import build_final_video
    sources = build_clip_sources(args.video, gpx, cfg, offset_override=offset_override,
                                 use_auto=use_auto)
    from intro_generator import _target_dims
    sw, sh = sources[0].width, sources[0].height
    W, H = _target_dims(sw, sh, cfg.output_height)

    ranges = None
    if args.mode in ("auto", "segments"):
        ranges = _segment_ranges_multi(args, gpx, cfg)
        if args.mode == "segments" and not ranges:
            print("Segment mode requested but no Strava segments available.", file=sys.stderr)
            return 1

    if ranges:
        parts, dropped = resolve_render_parts(sources, ranges)
        if dropped:
            print(f"[warn] geen video voor: {', '.join(dropped)}", file=sys.stderr)
        if not parts:
            print("Geen van de segmenten wordt door video gedekt; niets te renderen.",
                  file=sys.stderr)
            return 1
        if args.dry_run:
            print(f"Render-delen ({len(parts)}):")
            for p in parts:
                print(f"  {_mmss(p.base_offset + p.local_start)}  {p.name}  "
                      f"[{os.path.basename(p.source_path)} {p.local_start:.1f}-{p.local_end:.1f}s]")
            return 0
        telemetry_source = _telemetry_source_multi(args, gpx)
        reel = build_reel_from_parts(parts, cfg, (W, H), gpx=gpx,
                                     telemetry_source=telemetry_source)
    else:
        # flow mode across files: analyse each source, keep its video-local segments
        from clip_sources import RenderPart
        from highlight_detector import analyze_video
        parts = []
        for s in sources:
            a = analyze_video(s.path, gpx, cfg, offset_override=None, use_auto=use_auto)
            for seg in a.segments:
                parts.append(RenderPart(source_path=s.path, local_start=seg.start,
                                        local_end=seg.end, base_offset=s.base_offset))
        parts.sort(key=lambda p: p.base_offset + p.local_start)
        if args.dry_run:
            print(f"Flow-highlights over {len(sources)} bestanden: {len(parts)} deel(en).")
            return 0
        if not parts:
            print("No highlight segments detected; nothing to render.", file=sys.stderr)
            return 1
        reel = build_reel_from_parts(parts, cfg, (W, H), gpx=None)

    try:
        print("Intro + eindmontage renderen…", file=sys.stderr)
        build_final_video(reel, gpx, cfg, args.output, args, sources=sources)
    finally:
        shutil.rmtree(os.path.dirname(reel), ignore_errors=True)
    print(f"Wrote {args.output}")
    return 0


def _segment_ranges_multi(args, gpx, cfg):
    """Strava efforts as ride-time ranges for the multi-file path, or None."""
    import sys
    try:
        import strava_client
        from segment_detector import parse_efforts, efforts_to_activity_ranges
        if not strava_client.is_configured() or not args.strava_activity_id:
            return None
        efforts = parse_efforts(strava_client.get_segment_efforts(args.strava_activity_id))
        return efforts_to_activity_ranges(efforts, gpx.start_time, cfg) or None
    except Exception as e:
        print(f"[warn] Strava segments unavailable: {e}", file=sys.stderr)
        return None


def _telemetry_source_multi(args, gpx):
    import sys
    try:
        import strava_client
        from telemetry import telemetry_from_streams
        if strava_client.is_configured() and args.strava_activity_id:
            streams = strava_client.get_activity_streams(args.strava_activity_id)
            if streams:
                print("Telemetrie: Strava-streams", file=sys.stderr)
                return telemetry_from_streams(streams, gpx)
    except Exception as e:
        print(f"[warn] Strava streams unavailable ({e}); using GPX telemetry.", file=sys.stderr)
    return None
```

Wire the guard in `main()` right after `offset_override, use_auto = resolve_offset_args(args)`:

```python
    if len(args.video) > 1:
        return _run_multi(args, cfg, gpx, offset_override, use_auto)
```

(`_run_multi` needs `os`/`shutil`, already imported at module top.)

- [ ] **Step 3: Regression + smoke tests** — append to `test_sync.py`:

```python
def test_video_arg_is_list_single(monkeypatch):
    import main
    args = main.build_parser().parse_args(["--video", "a.mp4", "--gpx", "r.gpx"])
    assert args.video == ["a.mp4"]


def test_video_arg_is_list_multi():
    import main
    args = main.build_parser().parse_args(
        ["--video", "a.mp4", "b.mp4", "c.mp4", "--gpx", "r.gpx"])
    assert args.video == ["a.mp4", "b.mp4", "c.mp4"] and len(args.video) > 1


def test_run_multi_dropped_ranges_error(monkeypatch, capsys):
    """No coverage for any range -> non-zero exit with a clear message."""
    import main, clip_sources
    from datetime import datetime, timezone
    from types import SimpleNamespace
    src = clip_sources.ClipSource(path="A.mp4", base_offset=0.0, duration=10.0, width=1920,
                                  height=1080, creation_time=datetime(2026,7,5,tzinfo=timezone.utc),
                                  offset_source="metadata")
    monkeypatch.setattr(main, "_run_multi", main._run_multi)  # ensure real fn
    monkeypatch.setattr("clip_sources.build_clip_sources", lambda *a, **k: [src])
    monkeypatch.setattr(main, "_segment_ranges_multi", lambda a, g, c: [(100.0, 120.0, "ver", {})])
    from intro_generator import _target_dims  # noqa: ensure importable
    args = SimpleNamespace(video=["A.mp4", "B.mp4"], mode="segments", dry_run=False,
                           strava_activity_id=None, output="out.mp4", output_height="1080",
                           music=None, gpx="r.gpx")
    from config import Config
    rc = main._run_multi(args, Config(), object(), None, False)
    assert rc == 1
    assert "niets te renderen" in capsys.readouterr().err
```

- [ ] **Step 4: Run — PASS** (`-k "video_arg or run_multi"`). **Step 5: Full suite** (131) + `.venv/bin/python -c "import main"`. **Step 6: README** — document multi-video:

```markdown
`--video` accepts several files that jointly cover one ride:
`--video clip1.mp4 clip2.mp4 clip3.mp4`. Each file is placed on the ride by its own
recording time; use `--sync-offset` for a single shared camera-clock correction applied
to all files. The segment/highlight selection runs on the ride as usual; only ride
portions that actually have footage are rendered (uncovered segments are dropped with a
warning). One `--video` file behaves exactly as before.
```

- [ ] **Step 7: Commit** `feat: accept multiple --video files covering one ride`.

---

## Self-Review

**Spec coverage:**
- `ClipSource`/`RenderPart`, `build_clip_sources` (per-file base + shared correction, sort, warnings), `resolve_render_parts` (inside/partial/spanning/dropped/overlap/base_offset) → Task 1. ✓
- `efforts_to_activity_ranges` (ride-time, no offset/clamp) → Task 2. ✓
- `build_reel_from_parts` (multi-source cut, scale to target, HUD per base_offset, name→overlay else plain, concat) → Task 3. ✓
- `curviest_window_across` + intro/`build_final_video` sources → Task 4. ✓
- `--video nargs`, multi orchestration (segment+flow), dropped warning, no-coverage error, dry-run, single-file untouched, README → Task 5. ✓
- Offset fork (single absolute vs multi additive) → single path untouched (Global Constraints) + `build_clip_sources` additive (Task 1). ✓
- Backward compat single-file → Global Constraints + Task 5 guard + `test_video_arg_is_list_single`. ✓

**Placeholder scan:** none.

**Type consistency:** `RenderPart` fields identical across Tasks 1/3/4/5. `resolve_render_parts -> (parts, dropped)` consumed in Task 5. `build_reel_from_parts(parts, cfg, target_size, gpx=, telemetry_source=)` signature identical Tasks 3/5. `curviest_window_across(sources, turn, clip_len) -> (path, local)` Task 4. `efforts_to_activity_ranges(efforts, gpx_start, cfg) -> [(a0,a1,name,stats)]` Tasks 2/5. `build_final_video(..., sources=None)` Tasks 4/5. `_target_dims` reused.
