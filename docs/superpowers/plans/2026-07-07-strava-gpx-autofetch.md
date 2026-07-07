# Strava track-from-streams (auto-GPX) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user omit `--gpx` and have the ride track built from the Strava activity (summary + per-second streams) when a `--strava-activity-id` is given; `--gpx` still wins.

**Architecture:** A new `strava_gpx.gpx_from_strava` fetches the activity + streams and synthesises a full `GpxData` (per-second series via `telemetry._resample`, totals from the activity summary). `main` gains `resolve_gpx_source(args)` that picks `--gpx` (priority) → Strava → clear error.

**Tech Stack:** Python 3.11–3.14, requests, existing strava_client/telemetry/highlight_detector, pytest.

## Global Constraints

- `--gpx` becomes optional; if given it wins (`load_gpx`). Else, with `--strava-activity-id` + Strava configured → build from Strava. Neither → `SystemExit` with a clear message.
- Build a full `GpxData` from the activity summary (name, start_date, distance, moving_time, total_elevation_gain) + streams (latlng/altitude/time/heartrate/distance/velocity_smooth), resampled to per-second series.
- Speeds from `velocity_smooth × 3.6`; if absent, derive from `cum_distance_m` via `telemetry._speed_from_distance`.
- No `latlng` stream → `RuntimeError` "Strava activity {id} has no GPS track (latlng stream missing)."
- Tests use pytest; run via `.venv/bin/python`. Venv at `.venv` (Python 3.14.5) has all deps; do NOT recreate it.
- All commits end with: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## Existing interfaces

- `strava_client.py`: `is_configured()`, `_load_token()`, `_refresh_if_needed(token, now_epoch)`, `_save_token(token)`, `get_activity_stats(activity_id)`, `get_activity_streams(activity_id)`, consts `_API`, `TOKEN_FILE`; `time`, `requests` imported. Auth pattern: refresh → save → `headers={"Authorization": f"Bearer {token['access_token']}"}`.
- `telemetry.py`: `_resample(times, data, n) -> list` (interpolates onto integer seconds 0..n-1; drops None pairs; `[None]*n` when nothing to interpolate), `_speed_from_distance(cum_distance_m, n) -> list` (per-second km/h via ±1s central difference).
- `highlight_detector.py`: `GpxData` dataclass with keyword fields `start_time`, `speeds_kmh`, `coords`, `total_distance_km`, `elevation_gain_m`, `moving_time_s`, `first_coord`, `elevations_m`, `hr_bpm`, `cum_distance_m`, `name`; `load_gpx(path) -> GpxData`.
- `main.py`: `build_parser()` (has `--gpx` `required=True`, `--strava-activity-id`), `main()` does `gpx = load_gpx(args.gpx)` early then routes rendering.

---

## Task 1: get_activity + strava_gpx.gpx_from_strava

**Files:** Modify `strava_client.py`; Create `strava_gpx.py`; Test `test_sync.py`.

**Interfaces:**
- `strava_client.get_activity(activity_id: str) -> dict` — the raw activity JSON; raises `RuntimeError` when unconfigured.
- `strava_gpx.gpx_from_strava(activity_id: str) -> GpxData`.

- [ ] **Step 1: Failing tests** — append to `test_sync.py`:

```python
def test_get_activity_fetches_and_raises(monkeypatch):
    import strava_client, pytest
    monkeypatch.setattr(strava_client, "is_configured", lambda: True)
    monkeypatch.setattr(strava_client, "_load_token",
                        lambda: {"access_token": "t", "refresh_token": "r", "expires_at": 9e12})
    monkeypatch.setattr(strava_client, "_refresh_if_needed", lambda tok, now_epoch: tok)
    monkeypatch.setattr(strava_client, "_save_token", lambda tok: None)

    class R:
        def raise_for_status(self): pass
        def json(self): return {"name": "Rit", "distance": 1000.0}
    cap = {}
    def fake_get(url, headers=None, params=None, timeout=None):
        cap["url"] = url
        return R()
    monkeypatch.setattr(strava_client.requests, "get", fake_get)
    assert strava_client.get_activity("42")["name"] == "Rit"
    assert cap["url"].endswith("/activities/42")

    monkeypatch.setattr(strava_client, "is_configured", lambda: False)
    with pytest.raises(RuntimeError):
        strava_client.get_activity("42")


def _fake_streams(n=3):
    return {
        "time": {"data": list(range(n))},
        "latlng": {"data": [[51.80, 4.0], [51.801, 4.0], [51.802, 4.001]][:n]},
        "altitude": {"data": [10.0, 12.0, 14.0][:n]},
        "heartrate": {"data": [120, 130, 140][:n]},
        "distance": {"data": [0.0, 10.0, 20.0][:n]},
        "velocity_smooth": {"data": [0.0, 5.0, 10.0][:n]},   # m/s
    }


def _fake_activity():
    return {"name": "Namiddagrit op mountainbike", "start_date": "2026-07-05T12:32:57Z",
            "distance": 15560.0, "total_elevation_gain": 166.0, "moving_time": 2933}


def test_gpx_from_strava_builds_track(monkeypatch):
    import strava_gpx, strava_client
    monkeypatch.setattr(strava_client, "get_activity", lambda aid: _fake_activity())
    monkeypatch.setattr(strava_client, "get_activity_streams", lambda aid: _fake_streams(3))
    g = strava_gpx.gpx_from_strava("42")
    from datetime import timezone
    assert g.name == "Namiddagrit op mountainbike"
    assert g.start_time.tzinfo is not None and g.start_time.year == 2026
    assert len(g.coords) == 3 and g.first_coord == g.coords[0]
    assert abs(g.coords[0][0] - 51.80) < 1e-6
    assert g.speeds_kmh == [0.0, 18.0, 36.0]          # velocity_smooth * 3.6
    assert g.elevations_m == [10.0, 12.0, 14.0]
    assert g.hr_bpm == [120.0, 130.0, 140.0]
    assert abs(g.total_distance_km - 15.56) < 1e-6    # from summary
    assert g.elevation_gain_m == 166.0 and g.moving_time_s == 2933


def test_gpx_from_strava_no_gps_raises(monkeypatch):
    import strava_gpx, strava_client, pytest
    monkeypatch.setattr(strava_client, "get_activity", lambda aid: _fake_activity())
    s = _fake_streams(3); del s["latlng"]
    monkeypatch.setattr(strava_client, "get_activity_streams", lambda aid: s)
    with pytest.raises(RuntimeError, match="no GPS track"):
        strava_gpx.gpx_from_strava("42")


def test_gpx_from_strava_speed_falls_back_to_distance(monkeypatch):
    import strava_gpx, strava_client
    monkeypatch.setattr(strava_client, "get_activity", lambda aid: _fake_activity())
    s = _fake_streams(3); del s["velocity_smooth"]
    monkeypatch.setattr(strava_client, "get_activity_streams", lambda aid: s)
    g = strava_gpx.gpx_from_strava("42")
    assert all(v is not None for v in g.speeds_kmh)   # derived from distance
```

- [ ] **Step 2: Run — FAIL** (`AttributeError: get_activity` / `ModuleNotFoundError: strava_gpx`):
`.venv/bin/python -m pytest test_sync.py -k "get_activity_fetches or gpx_from_strava" -v`

- [ ] **Step 3: Implement `get_activity` in `strava_client.py`** (append, mirroring get_activity_streams):

```python
def get_activity(activity_id: str) -> dict:
    """Return the raw Strava activity JSON (name, start_date, totals, ...)."""
    if not is_configured():
        raise RuntimeError("Strava not configured (.strava_token.json + env vars).")
    token = _refresh_if_needed(_load_token(), now_epoch=time.time())
    _save_token(token)
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    resp = requests.get(f"{_API}/activities/{activity_id}", headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json() or {}
```

- [ ] **Step 4: Implement `strava_gpx.py`:**

```python
from __future__ import annotations

from datetime import datetime, timezone

import strava_client
from highlight_detector import GpxData
from telemetry import _resample, _speed_from_distance


def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def gpx_from_strava(activity_id: str) -> GpxData:
    """Build a GpxData from a Strava activity's summary + per-second streams."""
    activity = strava_client.get_activity(activity_id)
    streams = strava_client.get_activity_streams(activity_id)

    def data(key):
        d = (streams.get(key) or {}).get("data")
        return d if d else None

    latlng = data("latlng")
    if not latlng:
        raise RuntimeError(
            f"Strava activity {activity_id} has no GPS track (latlng stream missing).")

    times = data("time") or list(range(len(latlng)))
    n = int(max(times)) + 1 if times else len(latlng)

    lats = _resample(times, [p[0] for p in latlng], n)
    lons = _resample(times, [p[1] for p in latlng], n)
    coords = list(zip(lats, lons))

    alt = data("altitude")
    elevations = _resample(times, alt, n) if alt else [0.0] * n
    hr = data("heartrate")
    hr_bpm = _resample(times, hr, n) if hr else [None] * n
    dist = data("distance")
    cum_distance = _resample(times, dist, n) if dist else [0.0] * n

    vel = data("velocity_smooth")
    if vel:
        rv = _resample(times, vel, n)
        speeds = ([v * 3.6 for v in rv] if any(v is not None for v in rv)
                  else _speed_from_distance(cum_distance, n))
    else:
        speeds = _speed_from_distance(cum_distance, n)

    return GpxData(
        start_time=_parse_dt(activity["start_date"]),
        speeds_kmh=speeds, coords=coords, elevations_m=elevations, hr_bpm=hr_bpm,
        cum_distance_m=cum_distance,
        total_distance_km=(activity.get("distance") or 0.0) / 1000.0,
        elevation_gain_m=activity.get("total_elevation_gain") or 0.0,
        moving_time_s=activity.get("moving_time") or 0.0,
        first_coord=coords[0], name=activity.get("name"))
```

- [ ] **Step 5: Run — PASS** (`-k "get_activity_fetches or gpx_from_strava"`). **Step 6: Full suite** (`.venv/bin/python -m pytest test_sync.py -q`, was 101 → 105). **Step 7: Commit** `feat: build GpxData from a Strava activity (summary + streams)`.

---

## Task 2: main — optional --gpx + resolve_gpx_source

**Files:** Modify `main.py`, `README.md`; Test `test_sync.py`.

**Interfaces:** `main.resolve_gpx_source(args) -> GpxData` — `--gpx` wins, else Strava, else `SystemExit`. `--gpx` becomes optional.

- [ ] **Step 1: Failing tests** — append to `test_sync.py`:

```python
def test_resolve_gpx_source_gpx_wins(monkeypatch):
    import main
    called = {}
    monkeypatch.setattr(main, "load_gpx", lambda p: called.setdefault("gpx", p) or "GPXOBJ")
    import strava_client
    monkeypatch.setattr(strava_client, "is_configured", lambda: (_ for _ in ()).throw(AssertionError("strava must not be consulted")))

    class A:
        gpx = "ride.gpx"; strava_activity_id = "42"
    assert main.resolve_gpx_source(A()) == "GPXOBJ"
    assert called["gpx"] == "ride.gpx"


def test_resolve_gpx_source_uses_strava(monkeypatch):
    import main, strava_client, strava_gpx
    monkeypatch.setattr(strava_client, "is_configured", lambda: True)
    monkeypatch.setattr(strava_gpx, "gpx_from_strava", lambda aid: f"FROM_STRAVA:{aid}")

    class A:
        gpx = None; strava_activity_id = "42"
    assert main.resolve_gpx_source(A()) == "FROM_STRAVA:42"


def test_resolve_gpx_source_neither_errors(monkeypatch):
    import main, strava_client, pytest
    monkeypatch.setattr(strava_client, "is_configured", lambda: False)

    class A:
        gpx = None; strava_activity_id = None
    with pytest.raises(SystemExit):
        main.resolve_gpx_source(A())
```

- [ ] **Step 2: Run — FAIL** (`AttributeError: resolve_gpx_source`):
`.venv/bin/python -m pytest test_sync.py -k resolve_gpx_source -v`

- [ ] **Step 3: Implement in `main.py`.** Make `--gpx` optional in `build_parser()`:

```python
    p.add_argument("--gpx", help="Path to the Strava/Garmin GPX export. If omitted, "
                                 "the track is built from --strava-activity-id.")
```

(Remove `required=True` / the `required` kwarg from the existing `--gpx` argument.)

Add the resolver:

```python
def resolve_gpx_source(args):
    """Pick the GPX source: --gpx wins; else build from Strava; else error."""
    if args.gpx:
        return load_gpx(args.gpx)
    import strava_client
    if getattr(args, "strava_activity_id", None) and strava_client.is_configured():
        import strava_gpx
        print(f"GPX: opgebouwd uit Strava-activity {args.strava_activity_id}", file=sys.stderr)
        return strava_gpx.gpx_from_strava(args.strava_activity_id)
    raise SystemExit("Geef --gpx of --strava-activity-id (met Strava geconfigureerd).")
```

In `main()`, replace `gpx = load_gpx(args.gpx)` with `gpx = resolve_gpx_source(args)`.

- [ ] **Step 4: Run — PASS** (`-k resolve_gpx_source`). **Step 5: Full suite** (108) + `.venv/bin/python main.py --help` (shows `--gpx` no longer marked required) + `.venv/bin/python -c "import main, strava_gpx"`.

- [ ] **Step 6: README** — update the usage: note `--gpx` is optional when `--strava-activity-id` is given, e.g.:

```markdown
`--gpx` is optional: with `--strava --strava-activity-id <id>` the ride track is
built automatically from the Strava activity (its GPS/altitude/HR streams), so you can
render from Strava alone. A supplied `--gpx` always takes priority.
```

- [ ] **Step 7: Commit** `feat: optional --gpx — build the track from Strava when omitted`.

---

## Self-Review

**Spec coverage:**
- `get_activity` (raw activity, raises unconfigured) → Task 1. ✓
- `gpx_from_strava` (build GpxData from summary + streams; velocity→km/h with distance fallback; totals from summary; name; no-latlng error) → Task 1. ✓
- `--gpx` optional + `resolve_gpx_source` (gpx wins → Strava → SystemExit) + main wiring + README → Task 2. ✓
- Tests: get_activity, gpx_from_strava (build/no-gps/speed-fallback), resolver (gpx-wins/strava/neither) → Tasks 1–2. ✓

**Placeholder scan:** No TBD/TODO; each code step is complete.

**Type consistency:** `get_activity(activity_id) -> dict` (Task 1) consumed by `gpx_from_strava` (Task 1). `gpx_from_strava(activity_id) -> GpxData` (Task 1) called by `resolve_gpx_source` (Task 2). `GpxData(...)` keyword fields match the dataclass (start_time/speeds_kmh/coords/elevations_m/hr_bpm/cum_distance_m/total_distance_km/elevation_gain_m/moving_time_s/first_coord/name). `_resample`/`_speed_from_distance` signatures match telemetry.py. `resolve_gpx_source(args)` (Task 2) called by `main()`; `sys` is already imported in main.py.
