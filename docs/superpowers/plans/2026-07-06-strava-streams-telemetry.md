# Strava streams telemetry + power Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use Strava's accurate per-second streams (velocity_smooth, heartrate, watts, altitude, grade_smooth, latlng, distance) as the HUD telemetry source in segment mode, add a power (W) readout, and fall back per-field to the GPX-derived data when a stream is absent.

**Architecture:** A source-neutral `TelemetrySeries` per-second container feeds the existing sampler. `telemetry_from_streams(streams, gpx)` resamples streams onto the GPX's per-second timeline (length `len(gpx.speeds_kmh)`), filling any missing stream from the GPX equivalent. `sample_telemetry` reads the source uniformly (optional `watts`/`slopes_pct` via getattr, so a bare GpxData still works). The HUD gains a power panel; segment-mode wiring fetches streams once and passes the built source.

**Tech Stack:** Python 3.11–3.14, numpy, requests, Pillow, FFmpeg, pytest.

## Global Constraints

- Source = Strava per-second streams when available; per-field fallback to GPX.
- Streams apply in segment mode only (HUD); flow mode unchanged.
- Power shown only when watts data exists; placement top-left under HR (approved).
- Slope from `grade_smooth` when present, else the computed `slope_pct`.
- `TelemetrySeries` length == `len(gpx.speeds_kmh)`, aligned by activity-second, so streams and GPX fallback are always the same length.
- `sample_telemetry` must keep working when passed a plain `GpxData` (existing tests) — read `watts`/`slopes_pct` via `getattr(..., None)`.
- Streams fetch failure / not configured → full GPX source (unchanged HUD).
- Tests use pytest; run via `.venv/bin/python`. Venv at `.venv` (Python 3.14.5) has all deps; do NOT recreate it.
- All commits end with: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## Existing interfaces

- `strava_client.py`: `is_configured()`, `_load_token()`, `_refresh_if_needed(token, now_epoch)`, `_save_token(token)`, `get_segment_efforts(activity_id)`, consts `_API`, `TOKEN_FILE`; `time`, `requests` imported. Auth pattern: refresh, then `headers={"Authorization": f"Bearer {token['access_token']}"}`.
- `telemetry.py`: `TelemetrySample(speed_kmh, elevation_m, slope_pct, hr_bpm, lat, lon, seg_distance_km)`, `_lerp_series(series, t)`, `slope_pct(gpx, activity_time_s, window_s=3)`, `sample_telemetry(gpx, activity_time_s, segment_start_s)`.
- `highlight_detector.py`: `GpxData` with per-second `speeds_kmh`, `elevations_m`, `hr_bpm`, `cum_distance_m`, `coords`.
- `hud_renderer.py`: `render_hud_frame(sample, segment_name, seg_coords, size, cfg, date_str)`; HR panel drawn at `[s(40), s(100), s(300), s(158)]` (1080p baseline, `s()` scales by `H/1080`).
- `video_editor.py`: `build_segment_reel(video_path, clips, music_path, cfg, gpx=None, offset_seconds=0.0)`, `_render_hud_pngs(video_path, clip, gpx, offset_seconds, workdir, cfg)` (samples `sample_telemetry(gpx, ...)`, coords via `_segment_coords(gpx, clip, offset_seconds)`).
- `main.py`: segment path — `select_segment_clips(...)`, then `build_segment_reel(args.video, clips, args.music, cfg, gpx=gpx, offset_seconds=r.offset_used)`.

---

## Task 1: strava_client.get_activity_streams

**Files:** Modify `strava_client.py`; Test `test_sync.py`.

**Interfaces:**
- Produces `get_activity_streams(activity_id: str) -> dict` — returns the `key_by_type` streams dict (e.g. `{"velocity_smooth": {"data": [...]}, "time": {"data": [...]}, ...}`); raises `RuntimeError` when unconfigured.

- [ ] **Step 1: Failing tests** — append to `test_sync.py`:

```python
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
```

- [ ] **Step 2: Run — FAIL** (`AttributeError: ... get_activity_streams`):
`.venv/bin/python -m pytest test_sync.py -k get_activity_streams -v`

- [ ] **Step 3: Implement** — append to `strava_client.py`:

```python
_STREAM_KEYS = "time,latlng,distance,altitude,velocity_smooth,heartrate,watts,grade_smooth"


def get_activity_streams(activity_id: str) -> dict:
    """Return the activity's per-second streams keyed by type."""
    if not is_configured():
        raise RuntimeError("Strava not configured (.strava_token.json + env vars).")
    token = _refresh_if_needed(_load_token(), now_epoch=time.time())
    _save_token(token)
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    resp = requests.get(f"{_API}/activities/{activity_id}/streams", headers=headers,
                        params={"keys": _STREAM_KEYS, "key_by_type": True}, timeout=30)
    resp.raise_for_status()
    return resp.json() or {}
```

- [ ] **Step 4: Run — PASS.** **Step 5: Full suite** (`.venv/bin/python -m pytest test_sync.py -q`, 67 + 2). **Step 6: Commit** `feat: fetch Strava per-second activity streams`.

---

## Task 2: TelemetrySeries + telemetry_from_streams + sample_telemetry (power/slope/fallback)

**Files:** Modify `telemetry.py`; Test `test_sync.py`.

**Interfaces:**
- `@dataclass TelemetrySeries`: `speeds_kmh: list`, `elevations_m: list`, `hr_bpm: list`, `watts: list`, `slopes_pct: "list | None"`, `coords: list`, `cum_distance_m: list`.
- `telemetry_from_streams(streams: dict, gpx) -> TelemetrySeries` — resamples present streams onto `n = len(gpx.speeds_kmh)` activity-seconds; missing stream → the GPX equivalent (watts → all-None; slopes_pct → None so slope is computed).
- `TelemetrySample` gains `power_w: float | None`.
- `sample_telemetry(source, activity_time_s, segment_start_s)` — reads `source.speeds_kmh/elevations_m/hr_bpm/coords/cum_distance_m` and, via `getattr`, optional `watts`/`slopes_pct`; returns a `TelemetrySample` incl. `power_w` and slope (from `slopes_pct` when present, else computed).

- [ ] **Step 1: Failing tests** — append to `test_sync.py`:

```python
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
    assert ts.speeds_kmh == [0.0, 18.0, 36.0]      # velocity_smooth * 3.6
    assert ts.watts == [100.0, 200.0, 300.0]
    assert ts.slopes_pct == [1.0, 2.0, 3.0]        # grade_smooth
    assert ts.hr_bpm == [120.0, 130.0, 140.0]


def test_telemetry_from_streams_missing_watts_falls_back():
    g = _gpx_n(3)
    s = _streams(3); del s["watts"]
    del s["velocity_smooth"]
    ts = telemetry_from_streams(s, g)
    assert all(w is None for w in ts.watts)         # no watts -> all None
    assert ts.speeds_kmh == g.speeds_kmh            # missing velocity -> GPX speed


def test_sample_telemetry_from_stream_source_has_power_and_stream_slope():
    from telemetry import sample_telemetry
    g = _gpx_n(3)
    ts = telemetry_from_streams(_streams(3), g)
    s = sample_telemetry(ts, activity_time_s=1.0, segment_start_s=0.0)
    assert abs(s.speed_kmh - 18.0) < 1e-6
    assert s.power_w == 200.0
    assert abs(s.slope_pct - 2.0) < 1e-6            # from grade_smooth, not computed


def test_sample_telemetry_still_works_on_bare_gpx():
    from telemetry import sample_telemetry
    g = _gpx_n(3)
    s = sample_telemetry(g, activity_time_s=1.0, segment_start_s=0.0)
    assert s.power_w is None                         # GpxData has no watts
```

- [ ] **Step 2: Run — FAIL** (`ImportError: TelemetrySeries`):
`.venv/bin/python -m pytest test_sync.py -k "from_streams or from_stream_source or bare_gpx" -v`

- [ ] **Step 3: Implement** — in `telemetry.py`:

Add `power_w: "float | None" = None` to the `TelemetrySample` dataclass (a trailing field with default keeps existing positional constructions valid).

Add the container, resampler, and builder:

```python
from dataclasses import dataclass, field
import numpy as np


@dataclass
class TelemetrySeries:
    speeds_kmh: list = field(default_factory=list)
    elevations_m: list = field(default_factory=list)
    hr_bpm: list = field(default_factory=list)
    watts: list = field(default_factory=list)
    slopes_pct: "list | None" = None
    coords: list = field(default_factory=list)
    cum_distance_m: list = field(default_factory=list)


def _resample(times, data, n):
    """Resample (times, data) onto integer seconds 0..n-1; None entries dropped.
    Returns a list of floats, or [None]*n if there is nothing to interpolate."""
    pairs = [(float(t), float(v)) for t, v in zip(times, data)
             if t is not None and v is not None]
    if not pairs:
        return [None] * n
    ts = [p[0] for p in pairs]
    vs = [p[1] for p in pairs]
    return [float(np.interp(x, ts, vs)) for x in range(n)]


def telemetry_from_streams(streams: dict, gpx) -> TelemetrySeries:
    """Build a per-second TelemetrySeries from Strava streams, GPX-filling gaps."""
    def data(key):
        d = (streams.get(key) or {}).get("data")
        return d if d else None

    n = len(gpx.speeds_kmh)
    times = data("time") or list(range(n))

    vel = data("velocity_smooth")
    speeds = [v * 3.6 for v in _resample(times, vel, n)] if vel else list(gpx.speeds_kmh)

    alt = data("altitude")
    elevations = _resample(times, alt, n) if alt else list(gpx.elevations_m)

    hr = data("heartrate")
    hr_bpm = _resample(times, hr, n) if hr else list(gpx.hr_bpm)

    watts_raw = data("watts")
    watts = _resample(times, watts_raw, n) if watts_raw else [None] * n

    grade = data("grade_smooth")
    slopes = _resample(times, grade, n) if grade else None

    dist = data("distance")
    cum_distance = _resample(times, dist, n) if dist else list(gpx.cum_distance_m)

    ll = data("latlng")
    if ll:
        lats = _resample(times, [p[0] for p in ll], n)
        lons = _resample(times, [p[1] for p in ll], n)
        coords = list(zip(lats, lons))
    else:
        coords = list(gpx.coords)

    return TelemetrySeries(
        speeds_kmh=speeds, elevations_m=elevations, hr_bpm=hr_bpm, watts=watts,
        slopes_pct=slopes, coords=coords, cum_distance_m=cum_distance)
```

Refactor `sample_telemetry` to be source-neutral (rename the parameter to `source`; read optional fields via `getattr`):

```python
def sample_telemetry(source, activity_time_s, segment_start_s) -> TelemetrySample:
    """Interpolate telemetry at an activity-relative time from any per-second source
    (TelemetrySeries or GpxData). power_w/slopes_pct are optional (getattr)."""
    speed = _lerp_series(source.speeds_kmh, activity_time_s)
    elev = _lerp_series(source.elevations_m, activity_time_s)
    hr_series = source.hr_bpm
    hr_val = (_lerp_series(hr_series, activity_time_s)
              if any(h is not None for h in hr_series) else None)

    watts_series = getattr(source, "watts", None)
    power = (_lerp_series(watts_series, activity_time_s)
             if watts_series and any(w is not None for w in watts_series) else None)

    slopes = getattr(source, "slopes_pct", None)
    if slopes and any(s is not None for s in slopes):
        slope = _lerp_series(slopes, activity_time_s)
    else:
        slope = slope_pct(source, activity_time_s)

    cum_now = _lerp_series(source.cum_distance_m, activity_time_s)
    cum_seg = _lerp_series(source.cum_distance_m, segment_start_s)
    seg_km = max(0.0, (cum_now - cum_seg) / 1000.0)

    idx = max(0, min(len(source.coords) - 1, int(round(activity_time_s))))
    lat, lon = source.coords[idx] if source.coords else (0.0, 0.0)

    return TelemetrySample(
        speed_kmh=speed, elevation_m=elev, slope_pct=slope, hr_bpm=hr_val,
        lat=lat, lon=lon, seg_distance_km=seg_km, power_w=power)
```

(`slope_pct` already reads `.elevations_m`/`.cum_distance_m`, which both sources have, so it works unchanged on a TelemetrySeries. `_lerp_series` handles None entries per its existing contract.)

- [ ] **Step 4: Run — PASS** (`-k "from_streams or from_stream_source or bare_gpx"`). **Step 5: Full suite** — the prior telemetry tests (Task-2 plan-4) still pass because `sample_telemetry` accepts GpxData. Expect 69 + 4 = 73. **Step 6: Commit** `feat: TelemetrySeries + Strava-stream telemetry with power and per-field GPX fallback`.

---

## Task 3: HUD power panel

**Files:** Modify `hud_renderer.py`; Test `test_sync.py`.

**Interfaces:** `render_hud_frame` draws a power panel (top-left, under the HR panel, same style) when `sample.power_w is not None`; nothing when None.

- [ ] **Step 1: Failing tests** — append to `test_sync.py`:

```python
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
```

- [ ] **Step 2: Run — FAIL** (power panel not drawn):
`.venv/bin/python -m pytest test_sync.py -k "draws_power or no_power_panel" -v`

- [ ] **Step 3: Implement** — in `hud_renderer.render_hud_frame`, right after the HR block (the `if sample.hr_bpm is not None:` panel drawn at `[s(40), s(100), s(300), s(158)]`), add:

```python
    if sample.power_w is not None:
        pf = _font(cfg, s(40)); puf = _font(cfg, s(22))
        d.rounded_rectangle([s(40), s(168), s(300), s(226)], radius=s(10), fill=_DARK)
        pw = f"{int(round(sample.power_w))}"
        d.text((s(56), s(178)), pw, font=pf, fill=_WHITE)
        d.text((s(56) + d.textlength(pw, font=pf) + s(12), s(190)), "W", font=puf, fill=_GREY)
```

(Uses the same `_DARK`/`_WHITE`/`_GREY`, `_font`, and `s()` helpers already in the module.)

- [ ] **Step 4: Run — PASS.** **Step 5: Full suite** (75). **Step 6: Commit** `feat: HUD power (W) panel under HR`.

---

## Task 4: Wire the stream source into segment rendering

**Files:** Modify `video_editor.py`, `main.py`; Test `test_sync.py`.

**Interfaces:**
- `_render_hud_pngs(video_path, clip, source, gpx, offset_seconds, workdir, cfg)` — samples `sample_telemetry(source, ...)`; minimap coords still from `gpx` via `_segment_coords`.
- `build_segment_reel(video_path, clips, music_path, cfg, gpx=None, offset_seconds=0.0, telemetry_source=None)` — HUD sampling uses `telemetry_source or gpx`.
- `main.py` segment path: fetch streams once (best-effort) → `telemetry_from_streams(streams, gpx)`; pass as `telemetry_source`.

- [ ] **Step 1: Failing test (real-ffmpeg, stream source)** — append to `test_sync.py`:

```python
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
```

- [ ] **Step 2: Run — FAIL** (`build_segment_reel` has no `telemetry_source`):
`.venv/bin/python -m pytest test_sync.py -k segment_reel_with_stream_source -v`

- [ ] **Step 3: Implement in `video_editor.py`.**

Change `_render_hud_pngs` to take a `source` for sampling (keep `gpx` for coords/date):

```python
def _render_hud_pngs(video_path, clip, source, gpx, offset_seconds, workdir, cfg):
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
        sample = sample_telemetry(source, activity_t, seg_start_activity)
        frame = hud_renderer.render_hud_frame(
            sample, clip.name, seg_coords, (W, H), cfg, date_str)
        frame.save(os.path.join(hud_dir, f"hud_{i:06d}.png"))
        if i % 5 == 0 or i == n_frames - 1:
            _log(f"\r        frames {i + 1}/{n_frames} ({(i + 1) * 100 // n_frames}%)", end="")
    _log("")
    return os.path.join(hud_dir, "hud_%06d.png")
```

Update `build_segment_reel`'s signature and the HUD call site:

```python
def build_segment_reel(video_path, clips, music_path, cfg: Config,
                       gpx=None, offset_seconds=0.0, telemetry_source=None) -> str:
    ...
    use_hud = cfg.hud_enabled and gpx is not None
    source = telemetry_source if telemetry_source is not None else gpx
    ...
        if use_hud:
            _log(f"[{i + 1}/{n}] {clip.name} ({dur:.1f}s) — HUD-frames renderen…")
            try:
                pattern = _render_hud_pngs(video_path, clip, source, gpx, offset_seconds, workdir, cfg)
                ...
```

(Only the `_render_hud_pngs` call changes — pass `source, gpx`. Everything else — the ffmpeg overlay, fallback, `_concat_and_music` — is unchanged.)

- [ ] **Step 4: Wire `main.py`.** In the segment render path, build the source before rendering:

```python
        telemetry_source = None
        try:
            import strava_client
            from telemetry import telemetry_from_streams
            if strava_client.is_configured() and args.strava_activity_id:
                streams = strava_client.get_activity_streams(args.strava_activity_id)
                if streams:
                    telemetry_source = telemetry_from_streams(streams, gpx)
                    print("Telemetrie: Strava-streams", file=sys.stderr)
        except Exception as e:
            print(f"[warn] Strava streams unavailable ({e}); using GPX telemetry.",
                  file=sys.stderr)
        reel = build_segment_reel(args.video, clips, args.music, cfg,
                                  gpx=gpx, offset_seconds=r.offset_used,
                                  telemetry_source=telemetry_source)
```

(Place this immediately before the existing `reel = build_segment_reel(...)` call and replace that call with the one above; keep the surrounding `try/finally` cleanup and `print("Intro + eindmontage renderen…")`.)

- [ ] **Step 5: Run the stream-source smoke — PASS** (not skipped):
`.venv/bin/python -m pytest test_sync.py -k segment_reel_with_stream_source -v`

- [ ] **Step 6: Full suite + import check.**
`.venv/bin/python -m pytest test_sync.py -q` (expect 76) and `.venv/bin/python -c "import main"`.

- [ ] **Step 7: README** — under the HUD paragraph add:

```markdown
When Strava is configured and an activity id is given, the HUD is fed by Strava's
per-second **streams** (smoothed speed, heart rate, power, grade) rather than
values computed from the GPX — more accurate, and it adds a power (W) readout.
Missing streams fall back to the GPX data per field.
```

- [ ] **Step 8: Commit** `feat: feed the HUD from Strava streams in segment mode (with power)`.

---

## Self-Review

**Spec coverage:**
- `get_activity_streams` (streams fetch, keys, key_by_type, unconfigured raise) → Task 1. ✓
- `TelemetrySeries`, `telemetry_from_streams` (resample + scale + per-field GPX fallback), `TelemetrySample.power_w`, source-neutral `sample_telemetry` with getattr, slope from grade_smooth → Task 2. ✓
- HUD power panel (shown only when power present) → Task 3. ✓
- Wiring: streams fetched once in segment mode, source threaded through `build_segment_reel`/`_render_hud_pngs`, GPX fallback, README → Task 4. ✓
- Backward-compat: `sample_telemetry` still accepts a bare `GpxData` (getattr) → Task 2 test `test_sample_telemetry_still_works_on_bare_gpx`. ✓
- Real-ffmpeg smoke with a stream source → Task 4. ✓
- Flow mode untouched; segment-only → design honored (no flow changes). ✓

**Placeholder scan:** No TBD/TODO; each code step is complete.

**Type consistency:** `TelemetrySeries` fields (Task 2) are read by `sample_telemetry` (Task 2) and produced by `telemetry_from_streams` (Task 2). `TelemetrySample.power_w` (Task 2) is read by `render_hud_frame` (Task 3). `_render_hud_pngs(video_path, clip, source, gpx, offset_seconds, workdir, cfg)` (Task 4) matches its `build_segment_reel` caller (Task 4). `build_segment_reel(..., telemetry_source=None)` (Task 4) matches the `main.py` caller (Task 4). `get_activity_streams` (Task 1) return shape (`{type: {"data": [...]}}`) is what `telemetry_from_streams` (Task 2) reads via `(streams.get(key) or {}).get("data")`.
