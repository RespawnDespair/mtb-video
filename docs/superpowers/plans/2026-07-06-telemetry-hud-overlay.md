# Animated telemetry HUD overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the segment-mode lower-third band with an animated per-frame telemetry HUD (segment name + HR, elevation + slope, speedometer, segment minimap with a live position dot) rendered with Pillow and composited via FFmpeg.

**Architecture:** Extend the GPX loader with per-second elevation/HR/cumulative-distance series; a `telemetry` module samples those at frame time; a `minimap` module projects lat/lon to pixels; a Pillow `hud_renderer` draws a transparent HUD frame; `build_segment_reel` renders a HUD PNG sequence per clip and overlays it with FFmpeg (falling back to the lower-third band when disabled or on render failure).

**Tech Stack:** Python 3.11–3.14, Pillow (already installed via moviepy), gpxpy, FFmpeg overlay, pytest.

**Reference:** A working styling prototype exists at `scratchpad/hud_proto.py` (rendered `hud_preview.png`, user-approved). Its drawing code is the basis for the `hud_renderer`/`minimap` implementations below.

## Global Constraints

- HUD applies in **segment mode only**, replacing the lower-third band; flow mode stays overlay-free.
- All HUD values come from the GPX per-second series (HR direct from `gpxtpx:hr`; speed, slope, distance computed) except the segment name (from Strava). No power/cadence (not in GPX).
- Bottom-left distance = distance **within the current segment** (from segment start).
- Speedometer full-scale = **45 km/h** (`config.speedo_max_kmh`).
- Top-right order: **ELEVATION left, SLOPE right**.
- Bottom-left date format includes the year: **`DD-MM-YYYY`**.
- HUD frames rendered at `config.hud_fps` (default 15); the minimap base is rendered once per segment.
- On `config.hud_enabled == False` OR any Pillow render error → fall back to the existing `_lower_third_filter` band so a render still completes.
- Frame→activity time uses the resolved sync offset (`activity_t = video_t + offset`).
- Tests use pytest; run via `.venv/bin/python`. Venv at `.venv` (Python 3.14.5) has all deps (incl. Pillow); do NOT recreate it.
- All commits end with: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## Existing interfaces

- `highlight_detector.py`: `GpxData` (fields `start_time`, `speeds_kmh`, `coords`, `total_distance_km`, `elevation_gain_m`, `moving_time_s`, `first_coord`), `load_gpx(path)` builds per-second forward-filled `speeds_kmh`/`coords` (arrays length `duration+1`), `_to_utc`. `resolve_sync_offset(...) -> ResolvedOffset` (has `offset_used`).
- `segment_detector.py`: `SegmentClip(start, end, name, stats)`, `format_segment_stats`.
- `video_editor.py`: `check_ffmpeg`, `_lower_third_filter(name, stats, cfg)`, `_concat_and_music(part_paths, music_path, reel_duration, workdir, cfg)`, `build_segment_reel(video_path, clips, music_path, cfg)` (currently cuts each clip with `-vf _lower_third_filter`, re-encodes, then `_concat_and_music`).
- `config.py`: `Config` with `overlay_font_path` (Arial), `min_segment_seconds`, etc.
- `main.py`: segment-mode path calls `build_segment_reel`; offset resolved via `resolve_sync_offset` and passed to `efforts_to_clips` (segment activity-time windows are known).

---

## Task 1: GPX loader — per-second elevation, HR, cumulative distance

**Files:**
- Modify: `highlight_detector.py`
- Test: `test_sync.py`

**Interfaces:**
- Produces: `GpxData` gains three per-second, forward-filled fields (same length as `speeds_kmh`): `elevations_m: list[float]`, `hr_bpm: list[float | None]`, `cum_distance_m: list[float]`. New `_parse_hr(point) -> int | None` helper. `load_gpx` populates all three.

- [ ] **Step 1: Write failing tests**

Append to `test_sync.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest test_sync.py -k "hr_elevation_distance or hr_none" -v`
Expected: FAIL — `AttributeError: 'GpxData' object has no attribute 'elevations_m'`.

- [ ] **Step 3: Implement**

In `highlight_detector.py`, add the three fields to the `GpxData` dataclass (with defaults so existing constructions still work):

```python
    elevations_m: list = field(default_factory=list)      # per-second, forward-filled
    hr_bpm: list = field(default_factory=list)             # per-second, None where absent
    cum_distance_m: list = field(default_factory=list)     # per-second cumulative distance
```

(`field` is already imported from dataclasses in this module; if not, add `from dataclasses import field`.)

Add the HR parser:

```python
def _parse_hr(point):
    """Extract heart rate (bpm) from a gpxpy point's TrackPointExtension, or None."""
    for ext in (point.extensions or []):
        # ext may itself be the <hr> element or a container holding it
        if str(getattr(ext, "tag", "")).endswith("hr") and ext.text:
            try:
                return int(float(ext.text))
            except ValueError:
                return None
        for child in list(ext):
            if str(child.tag).endswith("hr") and child.text:
                try:
                    return int(float(child.text))
                except ValueError:
                    return None
    return None
```

In `load_gpx`, extend the per-second construction. Alongside the existing `speeds`/`coords` arrays (length `duration + 1`), build three more initialized to `None`:

```python
    elevations = [None] * (duration + 1)
    hrs = [None] * (duration + 1)
    cumdist = [None] * (duration + 1)
```

Inside the per-point loop (where `sec` is computed and `coords[sec]`/`speeds[sec]` are assigned), also assign — for the first point seed cumulative distance 0:

```python
        elevations[sec] = p.elevation if p.elevation is not None else 0.0
        hrs[sec] = _parse_hr(p)
        if prev is None:
            cumdist[sec] = 0.0
        else:
            cumdist[sec] = total_distance_m  # running 2D/3D distance already accumulated
```

(Use the same running distance the loop already accumulates for `total_distance_m`; assign it to `cumdist[sec]` after adding the current segment distance so it is the cumulative distance at that second.)

After the loop, forward-fill all three exactly like `speeds`/`coords` are forward-filled (carry the last known value forward; seed with `elevations[0]`/`hrs[0]`/`0.0`). For `hrs`, forward-fill the last known HR (leave leading `None` as `None` until the first real value). Return them in `GpxData(...)`:

```python
        elevations_m=elevations,
        hr_bpm=hrs,
        cum_distance_m=cumdist,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest test_sync.py -k "hr_elevation_distance or hr_none" -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest test_sync.py -q`
Expected: all PASS (prior 56 + 2 new).

- [ ] **Step 6: Commit**

```bash
git add highlight_detector.py test_sync.py
git commit -m "feat: GPX loader parses per-second elevation, HR, cumulative distance"
```

---

## Task 2: telemetry.py — sampling + slope

**Files:**
- Create: `telemetry.py`
- Test: `test_sync.py`

**Interfaces:**
- Consumes: `GpxData` (`speeds_kmh`, `elevations_m`, `hr_bpm`, `cum_distance_m`, `coords`).
- Produces:
  - `@dataclass TelemetrySample`: `speed_kmh: float`, `elevation_m: float`, `slope_pct: float`, `hr_bpm: float | None`, `lat: float`, `lon: float`, `seg_distance_km: float`.
  - `slope_pct(gpx, activity_time_s, window_s=3) -> float`.
  - `sample_telemetry(gpx, activity_time_s, segment_start_s) -> TelemetrySample`.

- [ ] **Step 1: Write failing tests**

Append to `test_sync.py`:

```python
from telemetry import TelemetrySample, sample_telemetry, slope_pct
from highlight_detector import GpxData
from datetime import datetime, timezone


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest test_sync.py -k "sample_telemetry or slope_pct" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'telemetry'`.

- [ ] **Step 3: Implement `telemetry.py`**

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TelemetrySample:
    speed_kmh: float
    elevation_m: float
    slope_pct: float
    hr_bpm: "float | None"
    lat: float
    lon: float
    seg_distance_km: float


def _lerp_series(series, t):
    """Linear interpolation into a per-second series; clamps to [0, len-1]."""
    if not series:
        return 0.0
    if t <= 0:
        return series[0]
    if t >= len(series) - 1:
        return series[-1]
    i = int(t)
    frac = t - i
    a, b = series[i], series[i + 1]
    if a is None:
        return b if b is not None else 0.0
    if b is None:
        return a
    return a + (b - a) * frac


def slope_pct(gpx, activity_time_s, window_s=3) -> float:
    """Grade % over ±window_s: Δelevation / Δhorizontal distance × 100."""
    t0 = max(0.0, activity_time_s - window_s)
    t1 = min(len(gpx.elevations_m) - 1, activity_time_s + window_s)
    d_ele = _lerp_series(gpx.elevations_m, t1) - _lerp_series(gpx.elevations_m, t0)
    d_dist = _lerp_series(gpx.cum_distance_m, t1) - _lerp_series(gpx.cum_distance_m, t0)
    if d_dist < 1.0:
        return 0.0
    return max(-40.0, min(40.0, d_ele / d_dist * 100.0))


def sample_telemetry(gpx, activity_time_s, segment_start_s) -> TelemetrySample:
    """Interpolate all telemetry at an activity-relative time."""
    speed = _lerp_series(gpx.speeds_kmh, activity_time_s)
    elev = _lerp_series(gpx.elevations_m, activity_time_s)
    hr_val = _lerp_series(gpx.hr_bpm, activity_time_s) if any(
        h is not None for h in gpx.hr_bpm) else None
    cum_now = _lerp_series(gpx.cum_distance_m, activity_time_s)
    cum_seg = _lerp_series(gpx.cum_distance_m, segment_start_s)
    seg_km = max(0.0, (cum_now - cum_seg) / 1000.0)

    idx = max(0, min(len(gpx.coords) - 1, int(round(activity_time_s))))
    lat, lon = gpx.coords[idx] if gpx.coords else (0.0, 0.0)

    return TelemetrySample(
        speed_kmh=speed, elevation_m=elev, slope_pct=slope_pct(gpx, activity_time_s),
        hr_bpm=hr_val, lat=lat, lon=lon, seg_distance_km=seg_km,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest test_sync.py -k "sample_telemetry or slope_pct" -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add telemetry.py test_sync.py
git commit -m "feat: telemetry sampling with interpolation + slope"
```

---

## Task 3: minimap.py — lat/lon projection

**Files:**
- Create: `minimap.py`
- Test: `test_sync.py`

**Interfaces:**
- Produces:
  - `compute_bounds(coords) -> tuple` — `(min_lat, max_lat, min_lon, max_lon, mean_lat)`.
  - `project(lat, lon, bounds, w, h, pad) -> tuple[float, float]` — pixel in a `w×h` box, aspect preserved, y flipped (north up), padded.
  - `project_track(coords, w, h, pad) -> list[tuple[float, float]]`.

- [ ] **Step 1: Write failing tests**

Append to `test_sync.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest test_sync.py -k "project_track or project_single" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'minimap'`.

- [ ] **Step 3: Implement `minimap.py`**

```python
from __future__ import annotations

import math


def compute_bounds(coords):
    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]
    return (min(lats), max(lats), min(lons), max(lons), sum(lats) / len(lats))


def project(lat, lon, bounds, w, h, pad):
    """Project a lat/lon to pixel coords in a w×h box (north up, aspect preserved)."""
    min_lat, max_lat, min_lon, max_lon, mean_lat = bounds
    # equirectangular: scale longitude by cos(mean latitude)
    cos_lat = math.cos(math.radians(mean_lat))
    span_x = (max_lon - min_lon) * cos_lat
    span_y = (max_lat - min_lat)
    inner_w = w - 2 * pad
    inner_h = h - 2 * pad
    if span_x < 1e-12 and span_y < 1e-12:
        return (w / 2.0, h / 2.0)   # single point / degenerate -> centre
    scale = min(inner_w / span_x if span_x > 1e-12 else float("inf"),
                inner_h / span_y if span_y > 1e-12 else float("inf"))
    # centre the drawing within the box
    draw_w = span_x * scale
    draw_h = span_y * scale
    ox = pad + (inner_w - draw_w) / 2.0
    oy = pad + (inner_h - draw_h) / 2.0
    px = ox + ((lon - min_lon) * cos_lat) * scale
    py = oy + (max_lat - lat) * scale   # flip: north (max_lat) at top
    return (px, py)


def project_track(coords, w, h, pad):
    bounds = compute_bounds(coords)
    return [project(la, lo, bounds, w, h, pad) for la, lo in coords]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest test_sync.py -k "project_track or project_single" -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add minimap.py test_sync.py
git commit -m "feat: minimap lat/lon projection"
```

---

## Task 4: hud_renderer.py — Pillow HUD frame

**Files:**
- Create: `hud_renderer.py`
- Test: `test_sync.py`

**Interfaces:**
- Consumes: `TelemetrySample`, `minimap.compute_bounds`/`project`, `Config` (`overlay_font_path`, `speedo_max_kmh`).
- Produces:
  - `render_hud_frame(sample, segment_name, seg_coords, size, cfg, date_str) -> PIL.Image.Image` — a transparent RGBA image of `size=(W,H)` with all four HUD corners drawn (segment minimap uses `seg_coords` for the polyline + a dot at `sample.lat/lon`).

The drawing is adapted from the approved prototype `scratchpad/hud_proto.py`. Corners: top-left name+HR; top-right ELEVATION then SLOPE; bottom-right speedometer (full-scale `cfg.speedo_max_kmh`); bottom-left minimap + `seg_distance_km` + `date_str`.

- [ ] **Step 1: Write failing tests**

Append to `test_sync.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest test_sync.py -k "render_hud_frame" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hud_renderer'`.

- [ ] **Step 3: Implement `hud_renderer.py`**

```python
from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFont

from minimap import compute_bounds, project

_RED = (232, 65, 44, 255)
_DARK = (18, 18, 22, 180)
_WHITE = (255, 255, 255, 255)
_GREY = (200, 200, 200, 255)


def _font(cfg, size, bold=True):
    path = cfg.overlay_font_path
    if bold:
        # try a Bold sibling next to the configured font; fall back to the base font
        bold_path = path.replace(".ttf", " Bold.ttf")
        try:
            return ImageFont.truetype(bold_path, size)
        except OSError:
            pass
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def render_hud_frame(sample, segment_name, seg_coords, size, cfg, date_str):
    """Return a transparent RGBA HUD frame for one telemetry sample."""
    W, H = size
    # scale UI to 1080p baseline so it looks right at any resolution
    k = H / 1080.0
    def s(v): return int(round(v * k))

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # ---- top-left: segment name tag + HR ----
    name_font = _font(cfg, s(34))
    name = segment_name.upper()
    tb = d.textbbox((0, 0), name, font=name_font)
    d.rectangle([s(40), s(40), s(40) + (tb[2] - tb[0]) + s(28), s(40) + (tb[3] - tb[1]) + s(16)], fill=_RED)
    d.text((s(54), s(48) - tb[1]), name, font=name_font, fill=_WHITE)
    if sample.hr_bpm is not None:
        hr_font = _font(cfg, s(40)); unit_font = _font(cfg, s(22))
        d.rounded_rectangle([s(40), s(100), s(300), s(158)], radius=s(10), fill=_DARK)
        hr_txt = f"{int(sample.hr_bpm)}"
        d.text((s(56), s(110)), hr_txt, font=hr_font, fill=_WHITE)
        d.text((s(56) + d.textlength(hr_txt, font=hr_font) + s(12), s(122)),
               "BPM", font=unit_font, fill=_GREY)

    # ---- top-right: ELEVATION (left) then SLOPE (right) ----
    def stat(x, label, value):
        lf = _font(cfg, s(22)); vf = _font(cfg, s(40))
        d.rectangle([x, s(40), x + s(150), s(74)], fill=_RED)
        d.text((x + s(14), s(46)), label, font=lf, fill=_WHITE)
        d.rounded_rectangle([x, s(74), x + s(150), s(140)], radius=s(8), fill=_DARK)
        tbv = d.textbbox((0, 0), value, font=vf)
        d.text((x + (s(150) - (tbv[2] - tbv[0])) / 2, s(84)), value, font=vf, fill=_WHITE)

    slope_x = W - s(40) - s(150)
    elev_x = slope_x - s(170)
    stat(elev_x, "ELEVATION", f"{sample.elevation_m:.0f} m")
    stat(slope_x, "SLOPE", f"{sample.slope_pct:+.0f}%")

    # ---- bottom-right: speedometer ----
    cx, cy, r = W - s(170), H - s(170), s(120)
    maxv = cfg.speedo_max_kmh
    a0, a1 = 140, 400
    tickf = _font(cfg, s(22)); numf = _font(cfg, s(72))
    step = max(1, int(maxv // 9))
    v = 0
    while v <= maxv:
        ang = math.radians(a0 + (a1 - a0) * (v / maxv))
        x1 = cx + (r - s(4)) * math.cos(ang); y1 = cy + (r - s(4)) * math.sin(ang)
        x2 = cx + (r - s(20)) * math.cos(ang); y2 = cy + (r - s(20)) * math.sin(ang)
        d.line([x1, y1, x2, y2], fill=_WHITE, width=s(3))
        v += step
    frac = max(0.0, min(1.0, sample.speed_kmh / maxv))
    d.arc([cx - r, cy - r, cx + r, cy + r], a0, a0 + (a1 - a0) * frac, fill=_RED, width=s(10))
    sv = f"{sample.speed_kmh:.0f}"
    tbv = d.textbbox((0, 0), sv, font=numf)
    d.text((cx - (tbv[2] - tbv[0]) / 2, cy - s(50)), sv, font=numf, fill=_WHITE)
    d.text((cx - s(28), cy + s(28)), "KM/U", font=tickf, fill=_GREY)

    # ---- bottom-left: minimap + distance + date ----
    mw, mh, pad = s(240), s(200), s(24)
    mx, my = s(40), H - s(40) - mh
    d.rounded_rectangle([mx, my, mx + mw, my + mh], radius=s(10), fill=_DARK)
    if seg_coords and len(seg_coords) >= 2:
        bounds = compute_bounds(seg_coords)
        poly = [(mx + px, my + py) for px, py in
                (project(la, lo, bounds, mw, mh, pad) for la, lo in seg_coords)]
        d.line(poly, fill=_WHITE, width=s(4), joint="curve")
        dpx, dpy = project(sample.lat, sample.lon, bounds, mw, mh, pad)
        dx, dy = mx + dpx, my + dpy
        d.ellipse([dx - s(8), dy - s(8), dx + s(8), dy + s(8)], fill=_RED, outline=_WHITE, width=s(2))
    df = _font(cfg, s(24))
    d.text((mx, my + mh + s(8)), f"{sample.seg_distance_km:.2f} km   {date_str}", font=df, fill=_WHITE)

    return img
```

Remove the stray `_fmt_time` placeholder before committing (it exists only to flag that no such helper is needed — do not ship a `NotImplementedError`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest test_sync.py -k "render_hud_frame" -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest test_sync.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add hud_renderer.py test_sync.py
git commit -m "feat: Pillow HUD frame renderer (name/HR, elevation/slope, speedo, minimap)"
```

---

## Task 5: Wire HUD into build_segment_reel + config + README

**Files:**
- Modify: `config.py`, `video_editor.py`, `README.md`
- Test: `test_sync.py` (real-ffmpeg HUD overlay smoke)

**Interfaces:**
- Consumes: `render_hud_frame`, `sample_telemetry`, `resolve`/offset already available to `main`, `SegmentClip`, `_lower_third_filter`, `_concat_and_music`, `format_segment_stats`.
- Produces:
  - `config.py`: `hud_enabled: bool = True`, `hud_fps: int = 15`, `speedo_max_kmh: float = 45.0`.
  - `video_editor.py`: `_render_hud_pngs(video_path, clip, gpx, offset_seconds, workdir, cfg) -> str` (renders the PNG sequence for a clip, returns the printf pattern), and `build_segment_reel` gains an optional `gpx`/`offset_seconds` HUD path. Signature becomes `build_segment_reel(video_path, clips, music_path, cfg, gpx=None, offset_seconds=0.0)`. When `cfg.hud_enabled and gpx is not None`: cut each clip and overlay its HUD PNG sequence; else the existing lower-third band.
  - `main.py`: pass `gpx=gpx, offset_seconds=r.offset_used` into the `build_segment_reel` call.

- [ ] **Step 1: Add config fields**

In `config.py` `Config`:

```python
    # HUD (segment-mode telemetry overlay)
    hud_enabled: bool = True
    hud_fps: int = 15
    speedo_max_kmh: float = 45.0
```

- [ ] **Step 2: Write the real-ffmpeg HUD overlay smoke test**

Append to `test_sync.py`:

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest test_sync.py -k hud_overlay_renders -v`
Expected: FAIL — `build_segment_reel()` got an unexpected keyword `gpx` (or overlay path not implemented).

- [ ] **Step 4: Implement the HUD path in `video_editor.py`**

Add imports at the top (with the others): `from telemetry import sample_telemetry` and `import hud_renderer`.

Add the PNG-sequence renderer:

```python
def _render_hud_pngs(video_path, clip, gpx, offset_seconds, workdir, cfg):
    """Render the HUD PNG sequence for one clip; return the printf pattern path."""
    import os
    import subprocess, json
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", video_path],
        capture_output=True, text=True, check=True)
    vs = next(s for s in json.loads(probe.stdout)["streams"] if s["codec_type"] == "video")
    W, H = int(vs["width"]), int(vs["height"])

    seg_start_activity = clip.start + offset_seconds
    seg_coords = _segment_coords(gpx, clip, offset_seconds)
    date_str = gpx.start_time.strftime("%d-%m-%Y")

    hud_dir = os.path.join(workdir, "hud")
    os.makedirs(hud_dir, exist_ok=True)
    dur = clip.end - clip.start
    n_frames = max(1, int(round(dur * cfg.hud_fps)))
    for i in range(n_frames):
        t_video = clip.start + (i / cfg.hud_fps)
        activity_t = t_video + offset_seconds
        sample = sample_telemetry(gpx, activity_t, seg_start_activity)
        frame = hud_renderer.render_hud_frame(
            sample, clip.name, seg_coords, (W, H), cfg, date_str)
        frame.save(os.path.join(hud_dir, f"hud_{i:06d}.png"))
    return os.path.join(hud_dir, "hud_%06d.png")


def _segment_coords(gpx, clip, offset_seconds):
    """The GPX coords covering the clip's activity-time window (for the minimap)."""
    a0 = int(max(0, clip.start + offset_seconds))
    a1 = int(min(len(gpx.coords) - 1, clip.end + offset_seconds))
    pts = gpx.coords[a0:a1 + 1]
    return pts if len(pts) >= 2 else gpx.coords[:2] or [(0.0, 0.0), (0.0, 0.0)]
```

Rewrite `build_segment_reel` to take the new params and branch:

```python
def build_segment_reel(video_path, clips, music_path, cfg,
                       gpx=None, offset_seconds=0.0):
    """Cut each segment clip with an overlay (HUD if gpx given, else lower-third),
    concat, and mix music."""
    check_ffmpeg()
    workdir = tempfile.mkdtemp(prefix="rhe_seg_")
    part_paths = []
    use_hud = cfg.hud_enabled and gpx is not None
    for i, clip in enumerate(clips):
        part = os.path.join(workdir, f"seg_{i:03d}.mp4")
        dur = clip.end - clip.start
        if use_hud:
            try:
                pattern = _render_hud_pngs(video_path, clip, gpx, offset_seconds, workdir, cfg)
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", str(clip.start), "-t", str(dur), "-i", video_path,
                     "-framerate", str(cfg.hud_fps), "-i", pattern,
                     "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=1[v]",
                     "-map", "[v]", "-map", "0:a?",
                     "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", part],
                    check=True, capture_output=True)
                part_paths.append(part)
                continue
            except Exception as e:
                print(f"[warn] HUD render failed ({e}); falling back to lower-third.")
        # fallback / hud disabled: lower-third band
        stats = format_segment_stats(clip)
        vf = _lower_third_filter(clip.name, stats, cfg)
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(clip.start), "-i", video_path,
             "-t", str(dur), "-vf", vf,
             "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", part],
            check=True, capture_output=True)
        part_paths.append(part)
    reel_duration = sum(c.end - c.start for c in clips)
    return _concat_and_music(part_paths, music_path, reel_duration, workdir, cfg)
```

Note: for HUD cuts, `-ss`/`-t` are placed before the video `-i` for a fast seek; the PNG sequence is the second input, overlaid at 0:0 with `shortest=1` so the overlay ends with the clip.

- [ ] **Step 5: Wire main.py to pass gpx + offset**

In `main.py`, the segment-mode render call becomes:

```python
        reel = build_segment_reel(args.video, clips, args.music, cfg,
                                  gpx=gpx, offset_seconds=r.offset_used)
```

(`r` is the `ResolvedOffset` already computed in the segment path; `gpx` is already loaded.)

- [ ] **Step 6: Run the HUD overlay smoke test + full suite**

Run: `.venv/bin/python -m pytest test_sync.py -k hud_overlay_renders -v`
Expected: PASS (not skipped — ffmpeg present; the HUD PNG sequence overlays successfully).
Then: `.venv/bin/python -m pytest test_sync.py -q`
Expected: all PASS.

- [ ] **Step 7: Update README**

Under the "Highlight modes" section in `README.md`, add:

````markdown
In segment mode each clip carries an animated telemetry **HUD** (segment name + heart
rate, elevation + slope, a speedometer, and a segment minimap with a live position
dot), drawn from the GPX per-second data. Disable it with `hud_enabled = False` in
`config.py` to fall back to a simple lower-third caption. HUD frames render at
`hud_fps` (default 15) and the speedometer scales to `speedo_max_kmh` (default 45).
````

- [ ] **Step 8: Commit**

```bash
git add config.py video_editor.py README.md test_sync.py
git commit -m "feat: animated telemetry HUD overlay in segment mode"
```

---

## Self-Review

**Spec coverage:**
- Per-second elevation/HR/cum-distance + `gpxtpx:hr` parsing → Task 1. ✓
- `TelemetrySample`, `sample_telemetry` (interpolation), `slope_pct`, seg-distance-from-start → Task 2. ✓
- Minimap projection (equirect, aspect, north-up, single-point safe) → Task 3. ✓
- HUD frame: name+HR (HR omitted when None), ELEVATION-left/SLOPE-right, speedo (45 full-scale), minimap + seg distance + date `DD-MM-YYYY` → Task 4. ✓
- Segment-mode wiring, PNG sequence at hud_fps, FFmpeg overlay, lower-third fallback on disable/error, config fields, README → Task 5. ✓
- Real-ffmpeg HUD smoke + region/dimension renderer tests → Tasks 4, 5. ✓
- Flow mode untouched; power/cadence excluded → design honored. ✓

**Placeholder scan:** The `_fmt_time` stub in Task 4 is explicitly flagged for removal before commit (it documents "no such helper needed"); no other TBD/TODO. All steps carry concrete code.

**Type consistency:** `TelemetrySample` fields (Task 2) are read by `render_hud_frame` (Task 4: `speed_kmh`, `elevation_m`, `slope_pct`, `hr_bpm`, `lat`, `lon`, `seg_distance_km`) and by `_render_hud_pngs` (Task 5). `render_hud_frame(sample, segment_name, seg_coords, size, cfg, date_str)` signature identical across Task 4 definition/tests and the Task 5 caller. `sample_telemetry(gpx, activity_time_s, segment_start_s)` identical in Task 2 and its Task 5 caller. `build_segment_reel(video_path, clips, music_path, cfg, gpx=None, offset_seconds=0.0)` — new optional params are backward-compatible with the existing Task-6 (prior plan) caller until main.py is updated in Task 5 Step 5. `minimap.compute_bounds`/`project` signatures identical in Task 3 and Task 4.
