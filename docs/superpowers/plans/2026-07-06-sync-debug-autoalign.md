# Sync debug + auto-align Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Diagnose GPX↔video alignment and correct a wrong sync offset — manually or via motion/GPS-speed cross-correlation — because copied videos have unreliable `creation_time`.

**Architecture:** Add `estimate_offset_by_motion` (cross-correlate the already-computed optical-flow-per-second against GPS speed) and an `analyze_video` orchestrator that computes flow once, resolves the offset by precedence, and returns a rich `Analysis`. `detect_highlights` becomes a thin wrapper. The CLI gains `--inspect`, `--sync-offset`, `--auto-sync`, and an ASCII-sparkline report on video-time.

**Tech Stack:** Python 3.11–3.14, numpy, opencv-python, existing highlight_detector/main modules, pytest.

## Global Constraints

- Single video per run (multi-clip is out of scope).
- Telemetry is GPX only.
- `offset_seconds` semantics unchanged: video-time `t` maps to activity-time `t + offset_seconds`.
- Offset precedence for detection/render: `--sync-offset` > `--auto-sync` estimate > `creation_time`/mtime (current default, keep the existing mtime warning).
- `--inspect` renders nothing; it prints the debug report and exits 0.
- Sparklines plotted on **video time with the chosen offset applied**, bucketed into `config.sparkline_columns` (default 100) columns, using blocks `▁▂▃▄▅▆▇█`.
- Tests use pytest; run everything via `.venv/bin/python`.
- All commits end with: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- The venv at `.venv` (Python 3.14.5) already has all dependencies; do NOT recreate it.

---

## File Structure

- `config.py` — add `sparkline_columns: int = 100`.
- `highlight_detector.py` — add `estimate_offset_by_motion`, `Analysis` dataclass, `analyze_video`; refactor `detect_highlights` into a wrapper.
- `main.py` — add the three flags, `resolve_offset_args`, `render_sparkline`, `print_inspection`; route the render path through `analyze_video`.
- `test_sync.py` — new tests for the estimator, precedence resolver, and sparkline renderer.
- `README.md` — document the new flags and a "fixing sync" workflow.

Existing per-second series already available (from the completed base project):
- `gpx.speeds_kmh: list[float]` — per activity-second speed.
- `compute_optical_flow_per_second(video_path, cfg) -> list[float]` — per video-second motion.
- `speed_at_video_time(video_t, offset_seconds, speeds_kmh) -> float`.
- `compute_offset_seconds(video_start, activity_start) -> float`.
- `get_video_creation_time(path) -> tuple[datetime, bool]`, `get_video_duration(path) -> float`.
- `score_seconds(speeds_at_video, flow, cfg) -> list[float]`, `merge_segments(scores, cfg) -> list[Segment]`.
- `Segment(start, end, score)`.

---

## Task 1: Config width + cross-correlation offset estimator

**Files:**
- Modify: `config.py`
- Modify: `highlight_detector.py`
- Test: `test_sync.py`

**Interfaces:**
- Consumes: `GpxData.speeds_kmh`, `Config`.
- Produces:
  - `Config.sparkline_columns: int = 100`.
  - `estimate_offset_by_motion(flow_per_sec: list[float], gpx: GpxData, cfg: Config) -> tuple[float, float]` — returns `(offset_seconds, correlation)`. `offset_seconds` is the integer lag (as float) maximizing Pearson correlation between `flow_per_sec[t]` and `gpx.speeds_kmh[t + lag]` over the overlapping window; `correlation` is that Pearson value in `[-1.0, 1.0]`. Degenerate/zero-variance windows contribute correlation `0.0`. If no valid lag exists, returns `(0.0, 0.0)`.

- [ ] **Step 1: Add the config field**

In `config.py`, add to the `Config` dataclass (in the Stage 1 group, after `gap_bridge_seconds`):

```python
    sparkline_columns: int = 100        # width of --inspect ASCII sparklines
```

- [ ] **Step 2: Write failing tests for the estimator**

Append to `test_sync.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest test_sync.py -k estimate_offset -v`
Expected: FAIL — `ImportError: cannot import name 'estimate_offset_by_motion'`.

- [ ] **Step 4: Implement the estimator**

Append to `highlight_detector.py` (numpy is already imported as `np`):

```python
def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson correlation of two equal-length arrays; 0.0 if either is flat."""
    if a.size < 2:
        return 0.0
    a_sd = a.std()
    b_sd = b.std()
    if a_sd < 1e-9 or b_sd < 1e-9:
        return 0.0
    return float(np.mean((a - a.mean()) * (b - b.mean())) / (a_sd * b_sd))


def estimate_offset_by_motion(flow_per_sec, gpx: GpxData, cfg: Config):
    """Find the offset (video_t -> activity index = t + offset) that best aligns
    the video's per-second motion with GPS speed, via max Pearson correlation.

    Returns (offset_seconds, correlation). offset is an integer lag as float.
    """
    flow = np.asarray(flow_per_sec, dtype=float)
    speeds = np.asarray(gpx.speeds_kmh, dtype=float)
    n_flow = flow.size
    n_speed = speeds.size
    if n_flow < 2 or n_speed < 2:
        return 0.0, 0.0

    # Candidate lags: video may start before the GPX (negative) or anywhere within it.
    best_offset = 0.0
    best_corr = 0.0
    found = False
    for lag in range(-n_flow + 1, n_speed):
        # Overlap where both flow[t] and speeds[t + lag] are valid.
        t_start = max(0, -lag)
        t_end = min(n_flow, n_speed - lag)
        if t_end - t_start < 2:
            continue
        fseg = flow[t_start:t_end]
        sseg = speeds[t_start + lag:t_end + lag]
        corr = _pearson(fseg, sseg)
        if not found or corr > best_corr:
            best_corr = corr
            best_offset = float(lag)
            found = True
    return best_offset, best_corr
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest test_sync.py -k estimate_offset -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest test_sync.py -q`
Expected: all PASS (prior 26 + 2 new = 28).

- [ ] **Step 7: Commit**

```bash
git add config.py highlight_detector.py test_sync.py
git commit -m "feat: motion/GPS cross-correlation offset estimator + sparkline width config"
```

---

## Task 2: Analysis dataclass + analyze_video orchestrator; detect_highlights wrapper

**Files:**
- Modify: `highlight_detector.py`
- Test: `test_sync.py` (offset-precedence unit test that does not need a video)

**Interfaces:**
- Consumes: `estimate_offset_by_motion`, `compute_optical_flow_per_second`, `get_video_creation_time`, `get_video_duration`, `compute_offset_seconds`, `speed_at_video_time`, `score_seconds`, `merge_segments`, `Segment`, `GpxData`, `Config`.
- Produces:
  - `@dataclass Analysis` with fields: `flow_per_sec: list[float]`, `speeds_at_video: list[float]`, `scores: list[float]`, `segments: list[Segment]`, `offset_used: float`, `offset_source: str`, `metadata_offset: float`, `auto_offset: float`, `auto_correlation: float`, `video_creation_time: datetime`, `from_metadata: bool`, `video_duration: float`, `fps: float`.
  - `_resolve_offset(metadata_offset, from_metadata, auto_offset, offset_override, use_auto) -> tuple[float, str]` — pure precedence helper returning `(offset_used, offset_source)` where source ∈ `{"manual","auto","metadata","mtime"}`.
  - `analyze_video(video_path: str, gpx: GpxData, cfg: Config, offset_override: float | None = None, use_auto: bool = False) -> Analysis`.
  - `detect_highlights(video_path, gpx, cfg) -> tuple[list[Segment], list[float]]` — now returns `(a.segments, a.scores)` from `analyze_video`.

- [ ] **Step 1: Write a failing test for the precedence helper**

Append to `test_sync.py`:

```python
from highlight_detector import _resolve_offset


def test_resolve_offset_manual_wins():
    used, src = _resolve_offset(metadata_offset=10.0, from_metadata=True,
                                auto_offset=4.0, offset_override=7.5, use_auto=True)
    assert used == 7.5 and src == "manual"


def test_resolve_offset_auto_when_requested():
    used, src = _resolve_offset(10.0, True, 4.0, offset_override=None, use_auto=True)
    assert used == 4.0 and src == "auto"


def test_resolve_offset_metadata_default():
    used, src = _resolve_offset(10.0, True, 4.0, offset_override=None, use_auto=False)
    assert used == 10.0 and src == "metadata"


def test_resolve_offset_mtime_source_when_not_from_metadata():
    used, src = _resolve_offset(10.0, False, 4.0, offset_override=None, use_auto=False)
    assert used == 10.0 and src == "mtime"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest test_sync.py -k resolve_offset -v`
Expected: FAIL — `ImportError: cannot import name '_resolve_offset'`.

- [ ] **Step 3: Implement Analysis, _resolve_offset, analyze_video, and the wrapper**

Append the dataclass + helpers to `highlight_detector.py`:

```python
@dataclass
class Analysis:
    flow_per_sec: list[float]
    speeds_at_video: list[float]
    scores: list[float]
    segments: list[Segment]
    offset_used: float
    offset_source: str            # "manual" | "auto" | "metadata" | "mtime"
    metadata_offset: float
    auto_offset: float
    auto_correlation: float
    video_creation_time: datetime
    from_metadata: bool
    video_duration: float
    fps: float


def _resolve_offset(metadata_offset, from_metadata, auto_offset, offset_override, use_auto):
    """Pick the offset by precedence: manual > auto > metadata/mtime."""
    if offset_override is not None:
        return float(offset_override), "manual"
    if use_auto:
        return float(auto_offset), "auto"
    return float(metadata_offset), ("metadata" if from_metadata else "mtime")


def analyze_video(video_path, gpx: GpxData, cfg: Config,
                  offset_override=None, use_auto=False) -> Analysis:
    """Compute flow once, resolve the sync offset, score, and merge.

    offset_override forces a manual offset; use_auto selects the cross-correlation
    estimate; otherwise the creation_time/mtime offset is used.
    """
    video_start, from_metadata = get_video_creation_time(video_path)
    metadata_offset = compute_offset_seconds(video_start, gpx.start_time)
    duration = int(round(get_video_duration(video_path)))

    flow = compute_optical_flow_per_second(video_path, cfg)
    if not flow:
        flow = [0.0] * duration

    auto_offset, auto_corr = estimate_offset_by_motion(flow, gpx, cfg)
    offset_used, offset_source = _resolve_offset(
        metadata_offset, from_metadata, auto_offset, offset_override, use_auto
    )

    speeds_at_video = [
        speed_at_video_time(float(t), offset_used, gpx.speeds_kmh) for t in range(duration)
    ]
    scores = score_seconds(speeds_at_video, flow, cfg)
    segments = merge_segments(scores, cfg)

    # fps for reporting (best-effort; 0.0 if unavailable)
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
        segments=segments, offset_used=offset_used, offset_source=offset_source,
        metadata_offset=metadata_offset, auto_offset=auto_offset,
        auto_correlation=auto_corr, video_creation_time=video_start,
        from_metadata=from_metadata, video_duration=float(duration), fps=fps,
    )
```

Then REPLACE the existing `detect_highlights` body so it delegates (keep its signature and return type):

```python
def detect_highlights(video_path, gpx: GpxData, cfg: Config):
    """Sync GPS to video, run optical flow, score, and merge into segments."""
    a = analyze_video(video_path, gpx, cfg)
    return a.segments, a.scores
```

(Remove the old inline implementation of `detect_highlights` — its logic now lives in `analyze_video`. Note: `analyze_video` no longer emits the "no creation_time metadata" warning; that warning moves to main.py in Task 3, driven by `Analysis.offset_source == "mtime"`, so the message appears exactly when the mtime offset is actually used.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest test_sync.py -k resolve_offset -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest test_sync.py -q`
Expected: all PASS (28 + 4 = 32).

- [ ] **Step 6: Commit**

```bash
git add highlight_detector.py test_sync.py
git commit -m "feat: analyze_video orchestrator + offset precedence; detect_highlights delegates"
```

---

## Task 3: CLI flags, sparkline renderer, inspection report; route render through analyze_video

**Files:**
- Modify: `main.py`
- Modify: `README.md`
- Test: `test_sync.py` (sparkline renderer unit tests)

**Interfaces:**
- Consumes: `analyze_video`, `Analysis`, `load_gpx`, `check_ffmpeg`, `build_config_from_args`, `Config`.
- Produces:
  - `render_sparkline(values: list[float], width: int) -> str` — maps a series to `▁▂▃▄▅▆▇█`; empty/flat → a row of `▁` of length `min(width, len)` (or `width` if values non-empty). Buckets into `width` columns by mean.
  - `resolve_offset_args(args) -> tuple[float | None, bool]` — `(offset_override, use_auto)` from `--sync-offset`/`--auto-sync`.
  - `print_inspection(analysis, gpx) -> None`.
  - New argparse flags `--inspect`, `--sync-offset` (float), `--auto-sync` (store_true).
  - `main()` routes both inspect and render through `analyze_video`.

- [ ] **Step 1: Write failing tests for the sparkline renderer**

Append to `test_sync.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest test_sync.py -k "sparkline or resolve_offset_args" -v`
Expected: FAIL — `ImportError: cannot import name 'render_sparkline'`.

- [ ] **Step 3: Add the renderer and offset-arg resolver to `main.py`**

Add near the top of `main.py` (after imports):

```python
_BLOCKS = "▁▂▃▄▅▆▇█"


def render_sparkline(values, width: int) -> str:
    """Map a numeric series to block characters, bucketed into `width` columns."""
    if not values:
        return ""
    n = len(values)
    cols = min(width, n)
    # bucket means
    buckets = []
    for i in range(cols):
        lo = (i * n) // cols
        hi = max(lo + 1, ((i + 1) * n) // cols)
        seg = values[lo:hi]
        buckets.append(sum(seg) / len(seg))
    lo_v = min(buckets)
    hi_v = max(buckets)
    if hi_v - lo_v < 1e-9:
        return _BLOCKS[0] * cols
    out = []
    for v in buckets:
        idx = int((v - lo_v) / (hi_v - lo_v) * (len(_BLOCKS) - 1))
        out.append(_BLOCKS[idx])
    return "".join(out)


def resolve_offset_args(args):
    """(offset_override, use_auto) from --sync-offset / --auto-sync."""
    override = getattr(args, "sync_offset", None)
    return (override, bool(getattr(args, "auto_sync", False)))
```

- [ ] **Step 4: Run the renderer tests to verify they pass**

Run: `.venv/bin/python -m pytest test_sync.py -k "sparkline or resolve_offset_args" -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Add the flags to the argparse parser**

In `main.py` `build_parser()`, add these arguments (alongside the existing ones):

```python
    p.add_argument("--inspect", action="store_true",
                   help="Print a sync/segment diagnosis report and render nothing.")
    p.add_argument("--sync-offset", type=float, default=None,
                   help="Force the sync offset in seconds (video_t -> activity_t + offset).")
    p.add_argument("--auto-sync", action="store_true",
                   help="Use motion/GPS cross-correlation to pick the offset for rendering.")
```

- [ ] **Step 6: Add `print_inspection` to `main.py`**

```python
def print_inspection(analysis, gpx) -> None:
    a = analysis
    local = a.video_creation_time.astimezone()
    print("=== GPX ===")
    print(f"  activity start : {gpx.start_time.isoformat()} (UTC)")
    print(f"  duration       : {len(gpx.speeds_kmh)} s")
    print(f"  distance       : {gpx.total_distance_km:.2f} km")
    print(f"  elevation gain : {gpx.elevation_gain_m:.0f} m")
    print(f"  moving time    : {gpx.moving_time_s / 60:.1f} min")
    print("\n=== Video ===")
    print(f"  creation_time  : {a.video_creation_time.isoformat()} "
          f"({'metadata' if a.from_metadata else 'file mtime — unreliable'})")
    print(f"  local time     : {local.isoformat()}")
    print(f"  duration       : {a.video_duration:.0f} s   fps: {a.fps:.2f}")
    print("\n=== Sync ===")
    print(f"  metadata offset: {a.metadata_offset:+.1f} s")
    print(f"  auto offset    : {a.auto_offset:+.1f} s   (correlation {a.auto_correlation:+.2f})")
    print(f"  USING          : {a.offset_used:+.1f} s   (source: {a.offset_source})")
    if abs(a.auto_correlation) < 0.3:
        print("  ! low correlation — auto-align is uncertain; verify the sparklines "
              "or set --sync-offset manually.")

    width = 100
    dur = int(a.video_duration)
    speed_row = a.speeds_at_video[:dur]
    flow_row = a.flow_per_sec[:dur]
    score_row = a.scores[:dur]
    print("\n=== Aligned on VIDEO time (offset applied) ===")
    print(f"  0s{' ' * (width - 6)}{dur}s")
    print(f"  speed  {render_sparkline(speed_row, width)}")
    print(f"  motion {render_sparkline(flow_row, width)}")
    print(f"  score  {render_sparkline(score_row, width)}")
    total = sum(seg.end - seg.start for seg in a.segments)
    print(f"\n  segments kept: {len(a.segments)}  ({total:.0f}s of {dur}s)")
    for seg in a.segments:
        print(f"    {seg.start:7.1f}s -> {seg.end:7.1f}s  score={seg.score:0.2f}")
```

(The `width` used for the sparklines is `config.sparkline_columns`; for simplicity `print_inspection` uses `100`. To honor config, callers may pass the value — but the fixed 100 matches the default and keeps the signature small.)

- [ ] **Step 7: Route `main()` through `analyze_video` and wire inspect**

In `main.py`, update `main()`. Replace the current highlight-detection + dry-run block so it:
1. builds config and resolves offset args,
2. calls `analyze_video`,
3. emits the mtime warning when `offset_source == "mtime"`,
4. branches to inspect / dry-run / render.

```python
def main() -> int:
    args = build_parser().parse_args()
    check_ffmpeg()
    cfg = build_config_from_args(args)

    gpx = load_gpx(args.gpx)
    offset_override, use_auto = resolve_offset_args(args)
    analysis = analyze_video(args.video, gpx, cfg,
                             offset_override=offset_override, use_auto=use_auto)

    if analysis.offset_source == "mtime":
        print(f"[warn] {args.video} has no creation_time metadata; using file mtime "
              "for sync — alignment may be approximate. Use --inspect / --auto-sync / "
              "--sync-offset to correct it.", file=sys.stderr)

    if args.inspect:
        print_inspection(analysis, gpx)
        return 0

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

Ensure the imports at the top of `main.py` include `analyze_video` (add it to the existing `from highlight_detector import ...` line) and that `sys` is imported.

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/python -m pytest test_sync.py -q`
Expected: all PASS (32 + 5 = 37).

- [ ] **Step 9: Manually verify inspect and render still work on the scratchpad fixtures**

Let `SP=/private/tmp/claude-501/-Users-Jelle-Tigchelaar-git-mtb-video/a50afca1-eb59-40a3-ad61-11a286923478/scratchpad` (ride.mp4, ride.gpx exist).

Run: `.venv/bin/python main.py --video $SP/ride.mp4 --gpx $SP/ride.gpx --inspect`
Expected: the report prints GPX/Video/Sync sections and three sparkline rows; renders nothing; exit 0. The `metadata offset` should be ~0 (fixture creation_time == activity start) and the auto offset/correlation are shown.

Run: `.venv/bin/python main.py --video $SP/ride.mp4 --gpx $SP/ride.gpx --sync-offset 0 --dry-run`
Expected: segments printed (standstill filtered), consistent with prior behavior.

- [ ] **Step 10: Update README**

Add a subsection under Usage in `README.md`:

````markdown
### Fixing sync (copied videos with wrong timestamps)

If the video's `creation_time` was altered by copying, the GPX↔video alignment is
off and too much footage is kept. Diagnose and correct it:

```bash
# 1. Inspect: see the offset sources and aligned speed/motion sparklines (video time).
python main.py --video ride.mp4 --gpx ride.gpx --inspect

# 2a. Let cross-correlation pick the offset automatically for the render:
python main.py --video ride.mp4 --gpx ride.gpx --music track.mp3 --auto-sync

# 2b. Or set the offset by hand (video_t maps to activity_t + offset):
python main.py --video ride.mp4 --gpx ride.gpx --music track.mp3 --sync-offset 137.5
```

`--inspect` shows the metadata offset, the auto-aligned offset with its correlation
(confidence), and which one is selected. Precedence: `--sync-offset` > `--auto-sync`
> file timestamp. A low correlation warning means auto-align is uncertain — compare
the `speed` and `motion` sparklines (peaks should line up) and set `--sync-offset`.
````

- [ ] **Step 11: Commit**

```bash
git add main.py README.md test_sync.py
git commit -m "feat: --inspect/--sync-offset/--auto-sync CLI + sparkline sync report"
```

---

## Self-Review

**Spec coverage:**
- Cross-correlation auto-align → Task 1 (`estimate_offset_by_motion`). ✓
- `Analysis` + single flow computation + offset precedence → Task 2 (`analyze_video`, `_resolve_offset`). ✓
- `detect_highlights` becomes wrapper → Task 2. ✓
- `--inspect`, `--sync-offset`, `--auto-sync` + precedence + mtime warning relocation → Task 3. ✓
- Sparklines on video-time with offset applied, ~100 cols, block chars → Task 3 (`render_sparkline`, `print_inspection`). ✓
- `config.sparkline_columns` → Task 1. ✓
- Tests: estimator recovers lag + degenerate; precedence resolver; sparkline renderer → Tasks 1–3. ✓
- README workflow → Task 3. ✓
- Multi-clip out of scope → not implemented. ✓

**Placeholder scan:** No TBD/TODO; all steps carry concrete code and commands.

**Type consistency:** `estimate_offset_by_motion` returns `(float, float)` consumed by `analyze_video` as `auto_offset, auto_corr`. `_resolve_offset` signature identical across Task 2 definition and use. `Analysis` field names used by `print_inspection` (Task 3) match the dataclass (Task 2): `speeds_at_video`, `flow_per_sec`, `scores`, `offset_used`, `offset_source`, `metadata_offset`, `auto_offset`, `auto_correlation`, `video_creation_time`, `from_metadata`, `video_duration`, `fps`. `render_sparkline(values, width)` signature consistent across Task 3 definition, tests, and `print_inspection`.
