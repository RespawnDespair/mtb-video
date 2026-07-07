# Cinematic animated intro Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the black-screen MoviePy intro with an animated title card: the curviest ride clip dimmed behind a route map that draws itself in, plus route name / date / stats — at the configured resolution.

**Architecture:** A pure `intro_select` picks the curviest window (honouring the resolved sync offset); a Pillow `intro_renderer` draws the transparent overlay frames (scrim + route-draw + title + stats); `build_intro_clip` extracts the bg clip, renders the PNG sequence, and composites via FFmpeg. `build_final_video`/`main` thread the resolved offset.

**Tech Stack:** Python 3.11–3.14, Pillow, FFmpeg (overlay/scale/-progress), gpxpy, pytest. Reference: the user-approved `scratchpad/intro_proto2.py` (its code is the basis below).

## Global Constraints

- Background = the curviest `intro_duration`-second window of the ride, dimmed by a ~55% (`cfg.intro_scrim_opacity`) black scrim.
- Curviness = summed per-second GPS heading change, counted only while moving (speed > 8 km/h).
- Clip selection uses the resolved sync offset (`r.offset_used`), searching only the activity window the video covers; `curviest_window` returns the **video-time** start.
- Stats: AFSTAND, TIJD, GEM. SNELHEID, HOOGTEMETERS, and VERMOGEN only when `extra_stats["avg_power"]` is present. Title = GPX `<name>` else humanised filename. Dutch date `"{d} {maand} {y} · HH:MM"`.
- Rendered at `cfg.output_height` (overlay scales by `k = H/1080`, like the HUD); intro fps `cfg.intro_fps` (default 30).
- Fallback: if the video is shorter than the intro / no window fits, render the overlay on a solid dark background (no bg clip) so the intro always succeeds.
- MoviePy is no longer used by the intro (drop the TextClip/ImageMagick reliance).
- Tests use pytest; run via `.venv/bin/python`. Venv at `.venv` (Python 3.14.5) has all deps; do NOT recreate it.
- All commits end with: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## Existing interfaces

- `highlight_detector.py`: `GpxData` dataclass (has `start_time`, `coords`, `speeds_kmh`, `total_distance_km`, `elevation_gain_m`, `moving_time_s`, `first_coord`, …); `load_gpx(path)` iterates `gpx.tracks`; `get_video_duration(path) -> float`.
- `minimap.py`: `compute_bounds(coords)`, `project(lat, lon, bounds, w, h, pad)`.
- `hud_renderer.py`: `_font(cfg, size, bold=True)` (lru-cached), module constants `_RED=(232,65,44,255)`, `_WHITE=(255,255,255,255)`, `_GREY`.
- `video_editor.py`: `_run_ffmpeg_progress(cmd, total_seconds, label)`.
- `intro_generator.py`: `build_intro_clip(gpx, cfg, out_path, extra_stats=None, size=(1920,1080))` (currently MoviePy), `_gather_extra_stats(gpx, args)`, `_concat_intro_and_reel(intro, reel, intro_duration, output, width, height)`, `build_final_video(reel_path, gpx, cfg, output, args)` (computes `(W,H)` from `get_video_resolution(args.video)` + `cfg.output_height`, calls build_intro_clip then _concat_intro_and_reel).
- `main.py`: segment path and flow path each call `build_final_video(reel, gpx, cfg, args.output, args)` and have `r` (ResolvedOffset with `offset_used`).
- `config.py`: `Config` with `output_height`, `intro_duration=7.0`, HUD fields.

---

## Task 1: GpxData.name (route name from the GPX)

**Files:** Modify `highlight_detector.py`; Test `test_sync.py`.

**Interfaces:** `GpxData` gains `name: "str | None" = None`; `load_gpx` populates it from the GPX track/metadata name.

- [ ] **Step 1: Failing test** — append to `test_sync.py`:

```python
import textwrap as _tw

NAMED_GPX = _tw.dedent("""\
<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>Namiddagrit op mountainbike</name><trkseg>
    <trkpt lat="46.0" lon="7.0"><ele>10</ele><time>2026-07-06T10:00:00Z</time></trkpt>
    <trkpt lat="46.0001" lon="7.0"><ele>11</ele><time>2026-07-06T10:00:01Z</time></trkpt>
  </trkseg></trk>
</gpx>
""")


def test_load_gpx_reads_track_name(tmp_path):
    from highlight_detector import load_gpx
    p = tmp_path / "n.gpx"; p.write_text(NAMED_GPX)
    assert load_gpx(str(p)).name == "Namiddagrit op mountainbike"


def test_load_gpx_name_none_when_absent(tmp_path):
    from highlight_detector import load_gpx
    p = tmp_path / "p.gpx"; p.write_text(SYNTHETIC_GPX)   # existing fixture, no <name>
    assert load_gpx(str(p)).name is None
```

- [ ] **Step 2: Run — FAIL** (`AttributeError: name`):
`.venv/bin/python -m pytest test_sync.py -k "track_name or name_none" -v`

- [ ] **Step 3: Implement.** Add to the `GpxData` dataclass (trailing field with default):

```python
    name: "str | None" = None
```

In `load_gpx`, after `gpx = gpxpy.parse(f)` and before/around building the result, derive the name (first track name, else the gpx-level name):

```python
    gpx_name = None
    for track in gpx.tracks:
        if track.name:
            gpx_name = track.name
            break
    if gpx_name is None:
        gpx_name = getattr(gpx, "name", None)
```

Pass `name=gpx_name` into the `GpxData(...)` construction.

- [ ] **Step 4: Run — PASS.** **Step 5: Full suite** (`.venv/bin/python -m pytest test_sync.py -q`, was 89 → 91). **Step 6: Commit** `feat: parse route name from the GPX`.

---

## Task 2: intro_select.py — curviest-window selection

**Files:** Create `intro_select.py`; Test `test_sync.py`.

**Interfaces:**
- `heading_change_per_sec(coords, speeds_kmh, min_speed_kmh=8.0) -> list[float]`.
- `curviest_window(turn, offset_seconds, video_duration, clip_len) -> float` (video-time start).

- [ ] **Step 1: Failing tests** — append to `test_sync.py`:

```python
from intro_select import heading_change_per_sec, curviest_window


def test_heading_change_straight_vs_turn():
    # straight north-ish line -> ~0 turning; then a sharp east turn -> a spike
    straight = [(46.0 + i * 1e-3, 7.0) for i in range(5)]
    turn = heading_change_per_sec(straight, [20.0] * 5)
    assert max(turn) < 1.0
    corner = [(46.0, 7.0), (46.001, 7.0), (46.002, 7.0), (46.002, 7.001), (46.002, 7.002)]
    t2 = heading_change_per_sec(corner, [20.0] * 5)
    assert max(t2) > 60.0                       # ~90° turn registers


def test_heading_change_ignores_stationary_jitter():
    jitter = [(46.0, 7.0), (46.0, 7.0001), (46.0001, 7.0), (46.0, 7.0)]  # spinning while slow
    turn = heading_change_per_sec(jitter, [2.0, 2.0, 2.0, 2.0])          # speed <= 8
    assert all(t == 0.0 for t in turn)


def test_curviest_window_respects_offset():
    turn = [0.0] * 20
    turn[10] = turn[11] = turn[12] = 50.0       # curvy burst at activity 10-12
    # offset 5, video covers activity 5..17 (duration 12), clip 3s
    vt = curviest_window(turn, offset_seconds=5.0, video_duration=12.0, clip_len=3.0)
    assert vt == 5.0                            # best activity start 10 -> video 10-5


def test_curviest_window_no_fit_returns_zero():
    assert curviest_window([0.0] * 5, offset_seconds=0.0, video_duration=2.0, clip_len=7.0) == 0.0
```

- [ ] **Step 2: Run — FAIL** (`ModuleNotFoundError: intro_select`):
`.venv/bin/python -m pytest test_sync.py -k "heading_change or curviest_window" -v`

- [ ] **Step 3: Implement `intro_select.py`:**

```python
from __future__ import annotations

import math


def _bearing(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return math.degrees(math.atan2(x, y))


def heading_change_per_sec(coords, speeds_kmh, min_speed_kmh=8.0):
    """Per-second absolute heading change (deg), zeroed where speed <= min_speed."""
    n = len(coords)
    turn = [0.0] * n
    for i in range(2, n):
        delta = abs((_bearing(coords[i - 1], coords[i])
                     - _bearing(coords[i - 2], coords[i - 1]) + 180) % 360 - 180)
        moving = i < len(speeds_kmh) and speeds_kmh[i] > min_speed_kmh
        turn[i] = delta if moving else 0.0
    return turn


def curviest_window(turn, offset_seconds, video_duration, clip_len):
    """Video-time start of the curviest clip_len window inside the video's activity
    span [offset, offset + video_duration - clip_len]. 0.0 if nothing fits."""
    off = int(round(offset_seconds))
    clip = int(round(clip_len))
    lo = max(0, off)
    hi = off + int(video_duration) - clip
    if hi < lo:
        return 0.0
    best_a, best_s = None, -1.0
    for a in range(lo, hi + 1):
        if a + clip > len(turn):
            break
        s = sum(turn[a:a + clip])
        if s > best_s:
            best_s, best_a = s, a
    if best_a is None:
        return 0.0
    return max(0.0, float(best_a - off))
```

- [ ] **Step 4: Run — PASS.** **Step 5: Full suite** (95). **Step 6: Commit** `feat: curviest-window selection for the intro background clip`.

---

## Task 3: intro_renderer.py — Pillow overlay frames

**Files:** Create `intro_renderer.py`; Test `test_sync.py`.

**Interfaces:**
- `intro_date(gpx) -> str`; `intro_title(gpx, gpx_path=None) -> str`; `intro_stats(gpx, extra_stats=None) -> list[tuple[str, str]]`.
- `render_intro_frame(t_norm, coords, title, date_str, stats, size, cfg) -> PIL.Image` (transparent RGBA).
- Consumes: `minimap.compute_bounds`/`project`, `hud_renderer._font`/`_RED`/`_WHITE`.

- [ ] **Step 1: Failing tests** — append to `test_sync.py`:

```python
import intro_renderer
from datetime import datetime, timezone


def _named_gpx(name):
    from highlight_detector import GpxData
    return GpxData(start_time=datetime(2026, 7, 5, 14, 32, 0, tzinfo=timezone.utc),
                   speeds_kmh=[20] * 3, coords=[(51.8, 4.0), (51.801, 4.0), (51.802, 4.001)],
                   elevations_m=[5, 6, 7], hr_bpm=[100, 100, 100], cum_distance_m=[0, 10, 20],
                   total_distance_km=15.6, elevation_gain_m=166.0, moving_time_s=2933.0,
                   first_coord=(51.8, 4.0), name=name)


def test_intro_title_prefers_gpx_name():
    assert intro_renderer.intro_title(_named_gpx("Namiddagrit"), "x.gpx") == "Namiddagrit"
    assert intro_renderer.intro_title(_named_gpx(None), "Mooie_Rit.gpx") == "Mooie Rit"


def test_intro_stats_power_only_when_present():
    g = _named_gpx("R")
    base = intro_renderer.intro_stats(g, None)
    labels = [l for l, _ in base]
    assert "AFSTAND" in labels and "GEM. SNELHEID" in labels and "VERMOGEN" not in labels
    with_p = intro_renderer.intro_stats(g, {"avg_power": 218})
    assert ("VERMOGEN", "218 W") in with_p


def test_render_intro_frame_regions():
    from config import Config
    g = _named_gpx("Namiddagrit")
    stats = intro_renderer.intro_stats(g, None)
    img = intro_renderer.render_intro_frame(1.0, g.coords, "NAMIDDAGRIT",
                                            intro_renderer.intro_date(g), stats,
                                            (1920, 1080), Config())
    assert img.size == (1920, 1080) and img.mode == "RGBA"
    A = img.getchannel("A")
    assert A.getpixel((5, 5)) > 0                                   # full-frame scrim
    assert img.crop((60, 90, 700, 240)).getchannel("A").getextrema()[1] > 0   # title
    assert img.crop((0, 900, 1920, 1080)).getchannel("A").getextrema()[1] > 0 # stat row
```

- [ ] **Step 2: Run — FAIL** (`ModuleNotFoundError: intro_renderer` — note: there is no such module yet; the existing intro code lives in `intro_generator`):
`.venv/bin/python -m pytest test_sync.py -k "intro_title or intro_stats or render_intro_frame" -v`

- [ ] **Step 3: Implement `intro_renderer.py`:**

```python
from __future__ import annotations

import os

from PIL import Image, ImageDraw

from hud_renderer import _font, _RED, _WHITE
from minimap import compute_bounds, project

_NL_MONTHS = ["", "januari", "februari", "maart", "april", "mei", "juni", "juli",
              "augustus", "september", "oktober", "november", "december"]


def intro_date(gpx) -> str:
    dt = gpx.start_time.astimezone()
    return f"{dt.day} {_NL_MONTHS[dt.month]} {dt.year} · {dt.hour:02d}:{dt.minute:02d}"


def intro_title(gpx, gpx_path=None) -> str:
    name = getattr(gpx, "name", None)
    if name:
        return name
    if gpx_path:
        return os.path.splitext(os.path.basename(gpx_path))[0].replace("_", " ")
    return "Rit"


def intro_stats(gpx, extra_stats=None):
    mt = gpx.moving_time_s
    avg = gpx.total_distance_km / (mt / 3600) if mt > 0 else 0.0
    out = [
        ("AFSTAND", f"{gpx.total_distance_km:.1f} km"),
        ("TIJD", f"{int(mt // 60)}:{int(mt % 60):02d}"),
        ("GEM. SNELHEID", f"{avg:.1f} km/u"),
        ("HOOGTEMETERS", f"+{gpx.elevation_gain_m:.0f} m"),
    ]
    if extra_stats and extra_stats.get("avg_power"):
        out.append(("VERMOGEN", f"{int(extra_stats['avg_power'])} W"))
    return out


def _ease(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def render_intro_frame(t_norm, coords, title, date_str, stats, size, cfg):
    """A transparent RGBA intro overlay frame at animation progress t_norm in [0,1]."""
    W, H = size
    k = H / 1080.0
    def s(v): return max(1, int(round(v * k)))

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    scrim = int(255 * getattr(cfg, "intro_scrim_opacity", 0.55))
    d.rectangle([0, 0, W, H], fill=(0, 0, 0, scrim))

    title_a = _ease(t_norm / 0.10)
    route_p = _ease((t_norm - 0.05) / 0.45)
    stats_a = _ease((t_norm - 0.34) / 0.14)

    d.rectangle([0, 0, int(W * 0.34), s(8)], fill=_RED)

    if coords and len(coords) >= 2:
        mbw, mbh = int(W * 0.60), int(H * 0.50)
        mox, moy = (W - mbw) // 2, int(H * 0.25)
        bounds = compute_bounds(coords)
        pts = [(mox + px, moy + py)
               for px, py in (project(la, lo, bounds, mbw, mbh, s(30)) for la, lo in coords)]
        n = max(2, int(len(pts) * route_p))
        seg = pts[:n]
        if len(seg) >= 2:
            d.line(seg, fill=(232, 65, 44, 90), width=s(16), joint="curve")
            d.line(seg, fill=_RED, width=s(6), joint="curve")
            sx, sy = pts[0]
            d.ellipse([sx - s(9), sy - s(9), sx + s(9), sy + s(9)], fill=_WHITE)
            lx, ly = seg[-1]
            d.ellipse([lx - s(11), ly - s(11), lx + s(11), ly + s(11)], fill=_WHITE, outline=_RED, width=s(4))

    fa = lambda a: int(255 * a)
    d.rectangle([s(80), s(96), s(170), s(104)], fill=(232, 65, 44, fa(title_a)))
    d.text((s(80), s(112)), title.upper(), font=_font(cfg, s(60)), fill=(255, 255, 255, fa(title_a)))
    d.text((s(82), s(190)), date_str, font=_font(cfg, s(30)), fill=(190, 194, 204, fa(title_a)))

    if stats_a > 0 and stats:
        vy = H - s(150) + int((1 - stats_a) * s(40))
        cw = W // len(stats)
        vf, lf = _font(cfg, s(52)), _font(cfg, s(24))
        for i, (label, value) in enumerate(stats):
            cx = cw * i + cw // 2
            vb = d.textbbox((0, 0), value, font=vf)
            d.text((cx - (vb[2] - vb[0]) / 2, vy), value, font=vf, fill=(255, 255, 255, fa(stats_a)))
            lb = d.textbbox((0, 0), label, font=lf)
            d.text((cx - (lb[2] - lb[0]) / 2, vy + s(66)), label, font=lf, fill=(232, 65, 44, fa(stats_a)))

    return img
```

- [ ] **Step 4: Run — PASS.** **Step 5: Full suite** (98). **Step 6: Commit** `feat: Pillow intro overlay renderer (route-draw, title, stats)`.

---

## Task 4: build_intro_clip rewrite (bg clip + composite) + wiring

**Files:** Modify `config.py`, `intro_generator.py`, `main.py`, `README.md`; Test `test_sync.py`.

**Interfaces:**
- `config`: `intro_fps: int = 30`, `intro_scrim_opacity: float = 0.55`.
- `build_intro_clip(gpx, cfg, out_path, extra_stats=None, size=(1920,1080), video_path=None, offset_seconds=0.0, gpx_path=None)` — renders the animated intro (bg clip if `video_path` given + a window fits, else solid dark).
- `build_final_video(reel_path, gpx, cfg, output, args, offset_seconds=0.0)` — passes `args.video`, `offset_seconds`, `args.gpx`, `(W,H)` into build_intro_clip.
- `main.py`: both render paths call `build_final_video(..., offset_seconds=r.offset_used)`.

- [ ] **Step 1: Add config fields** — in `config.py` `Config`:

```python
    intro_fps: int = 30
    intro_scrim_opacity: float = 0.55
```

- [ ] **Step 2: Failing tests (real-ffmpeg)** — append to `test_sync.py`:

```python
import shutil as _sh5


@pytest.mark.skipif(_sh5.which("ffmpeg") is None or _sh5.which("ffprobe") is None,
                    reason="ffmpeg not installed")
def test_build_intro_clip_with_bg(tmp_path):
    import subprocess, intro_generator
    g = _named_gpx("Namiddagrit")   # helper defined earlier in this file
    vid = tmp_path / "src.mp4"      # 10s so a 7s window fits
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=640x360:d=10",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(vid)],
                   check=True, capture_output=True)
    from config import Config
    cfg = Config(); cfg.intro_duration = 2.0; cfg.intro_fps = 10
    out = tmp_path / "intro.mp4"
    intro_generator.build_intro_clip(g, cfg, str(out), extra_stats=None, size=(1280, 720),
                                     video_path=str(vid), offset_seconds=0.0, gpx_path="x.gpx")
    import os
    assert out.exists() and os.path.getsize(out) > 0
    h = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=height", "-of", "csv=p=0", str(out)],
                       capture_output=True, text=True).stdout.strip()
    assert h == "720"


@pytest.mark.skipif(_sh5.which("ffmpeg") is None,
                    reason="ffmpeg not installed")
def test_build_intro_clip_solid_fallback_when_no_video(tmp_path):
    import intro_generator, os
    from config import Config
    g = _named_gpx("Namiddagrit")
    cfg = Config(); cfg.intro_duration = 2.0; cfg.intro_fps = 10
    out = tmp_path / "intro.mp4"
    intro_generator.build_intro_clip(g, cfg, str(out), extra_stats=None, size=(640, 360),
                                     video_path=None, offset_seconds=0.0)
    assert out.exists() and os.path.getsize(out) > 0
```

- [ ] **Step 3: Run — FAIL** (build_intro_clip has no video_path/offset params / still MoviePy):
`.venv/bin/python -m pytest test_sync.py -k "build_intro_clip_with_bg or solid_fallback" -v`

- [ ] **Step 4: Rewrite `build_intro_clip` in `intro_generator.py`** (replace the whole MoviePy function). Remove the `from moviepy.editor import ...` inside it:

```python
def build_intro_clip(gpx: GpxData, cfg: Config, out_path: str, extra_stats: dict | None = None,
                     size=(1920, 1080), video_path=None, offset_seconds=0.0, gpx_path=None) -> str:
    """Render the animated intro: curviest bg clip (dimmed) + route-draw overlay."""
    import intro_renderer
    from intro_select import heading_change_per_sec, curviest_window
    from highlight_detector import get_video_duration
    from video_editor import _run_ffmpeg_progress

    W, H = size
    workdir = tempfile.mkdtemp(prefix="rhe_intro_")
    try:
        # 1. overlay PNG sequence
        title = intro_renderer.intro_title(gpx, gpx_path)
        date_str = intro_renderer.intro_date(gpx)
        stats = intro_renderer.intro_stats(gpx, extra_stats)
        frames_dir = os.path.join(workdir, "frames")
        os.makedirs(frames_dir)
        nframes = max(1, int(round(cfg.intro_duration * cfg.intro_fps)))
        for i in range(nframes):
            tn = i / (nframes - 1) if nframes > 1 else 1.0
            frame = intro_renderer.render_intro_frame(tn, gpx.coords, title, date_str, stats, (W, H), cfg)
            frame.save(os.path.join(frames_dir, f"f_{i:05d}.png"))
        pattern = os.path.join(frames_dir, "f_%05d.png")

        # 2. background: curviest clip from the video, else solid dark
        bg_clip = None
        if video_path:
            try:
                turn = heading_change_per_sec(gpx.coords, gpx.speeds_kmh)
                vt = curviest_window(turn, offset_seconds, get_video_duration(video_path),
                                     cfg.intro_duration)
                bg_clip = os.path.join(workdir, "bg.mp4")
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", str(vt), "-t", str(cfg.intro_duration),
                     "-i", video_path, "-vf", f"scale={W}:{H}", "-an", bg_clip],
                    check=True, capture_output=True)
            except Exception as e:
                print(f"[warn] intro background clip unavailable ({e}); using solid background.")
                bg_clip = None

        # 3. composite the overlay onto the background
        if bg_clip:
            base = ["-i", bg_clip]
        else:
            base = ["-f", "lavfi", "-i",
                    f"color=c=0x0f1014:s={W}x{H}:r={cfg.intro_fps}:d={cfg.intro_duration}"]
        cmd = (["ffmpeg", "-y"] + base
               + ["-framerate", str(cfg.intro_fps), "-i", pattern,
                  "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=1[v]",
                  "-map", "[v]", "-t", str(cfg.intro_duration),
                  "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", out_path])
        _run_ffmpeg_progress(cmd, cfg.intro_duration, "Intro renderen")
        return out_path
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
```

- [ ] **Step 5: Thread the offset in `build_final_video`.** Change its signature and the build_intro_clip call:

```python
def build_final_video(reel_path: str, gpx: GpxData, cfg: Config, output: str, args,
                      offset_seconds: float = 0.0) -> str:
    """Prepend the intro to the reel and re-encode at the configured resolution."""
    from highlight_detector import get_video_resolution
    sw, sh = get_video_resolution(args.video)
    W, H = _target_dims(sw, sh, cfg.output_height)
    workdir = tempfile.mkdtemp(prefix="rhe_final_")
    try:
        intro = os.path.join(workdir, "intro.mp4")
        extra = _gather_extra_stats(gpx, args)
        build_intro_clip(gpx, cfg, intro, extra, size=(W, H),
                         video_path=args.video, offset_seconds=offset_seconds,
                         gpx_path=getattr(args, "gpx", None))
        return _concat_intro_and_reel(intro, reel_path, cfg.intro_duration, output, W, H)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
```

- [ ] **Step 6: Pass the resolved offset from `main.py`.** In BOTH the segment-mode and flow-mode render paths, change the `build_final_video(reel, gpx, cfg, args.output, args)` call to `build_final_video(reel, gpx, cfg, args.output, args, offset_seconds=r.offset_used)`.

- [ ] **Step 7: Run the new tests — PASS** (not skipped):
`.venv/bin/python -m pytest test_sync.py -k "build_intro_clip_with_bg or solid_fallback" -v`

- [ ] **Step 8: Full suite + import check.**
`.venv/bin/python -m pytest test_sync.py -q` (100) and `.venv/bin/python -c "import main, intro_generator, intro_renderer, intro_select"`.

- [ ] **Step 9: README** — replace the intro description under the features/usage with:

```markdown
The intro is an animated title card: the curviest clip of the ride plays dimmed
behind a route map that draws itself in, with the route name, date, and stats
(distance, time, avg speed, elevation, and power when available). It renders at the
configured output resolution and its background clip follows the sync offset.
```

- [ ] **Step 10: Commit** `feat: animated cinematic intro (curviest bg clip + route-draw overlay)`.

---

## Self-Review

**Spec coverage:**
- GPX `<name>` → GpxData.name → Task 1. ✓
- Curviness (heading change, moving-only) + curviest_window honouring offset, video-time start → Task 2. ✓
- Pillow overlay (scrim, route-draw, title/date, stats, power-when-present, Dutch date, H/1080 scaling) → Task 3. ✓
- build_intro_clip: PNG sequence, curviest bg clip extraction, composite, solid fallback, `_run_ffmpeg_progress` "Intro renderen"; MoviePy removed; config intro_fps/scrim; offset threaded via build_final_video + main → Task 4. ✓
- Rendered at output_height (build_final_video passes (W,H)); tests incl. real-ffmpeg bg + fallback → Tasks 3, 4. ✓

**Placeholder scan:** No TBD/TODO; every code step is complete.

**Type consistency:** `GpxData.name` (Task 1) read by `intro_title` (Task 3). `heading_change_per_sec`/`curviest_window` (Task 2) called by `build_intro_clip` (Task 4). `render_intro_frame(t_norm, coords, title, date_str, stats, size, cfg)` / `intro_title(gpx, gpx_path)` / `intro_stats(gpx, extra_stats)` / `intro_date(gpx)` (Task 3) called by `build_intro_clip` (Task 4). `build_intro_clip(..., size, video_path, offset_seconds, gpx_path)` (Task 4) called by `build_final_video` (Task 4). `build_final_video(..., offset_seconds)` (Task 4) called by `main` (Task 4). `_target_dims` + `_concat_intro_and_reel(intro, reel, intro_duration, output, W, H)` already exist (prior feature) and are reused unchanged. `_run_ffmpeg_progress` (video_editor) imported lazily — no cycle.
```
