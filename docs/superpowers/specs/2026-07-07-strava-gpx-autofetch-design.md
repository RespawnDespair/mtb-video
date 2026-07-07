# Build the track from Strava when no GPX is given — Design

**Date:** 2026-07-07
**Status:** Approved for planning
**Context:** Today `--gpx` is required. When the user passes a `--strava-activity-id`
they should be able to omit `--gpx` and have the ride track built automatically. The
Strava API has no GPX-export endpoint, but it exposes the activity summary + per-second
**streams** — enough to synthesise a full `GpxData`. This is feature #1 of three
(auto-GPX → auto-music → multi-clip); each ships as its own spec/plan/build.

## Decisions (from brainstorming)

- `--gpx` becomes optional. If given, it wins (`load_gpx`). Otherwise, with a
  `--strava-activity-id` and Strava configured, build the track from Strava. Neither →
  a clear error.
- Build a full `GpxData` from the activity summary (name, start time, totals) + the
  streams (latlng/altitude/time/heartrate/distance/velocity_smooth), resampled to
  per-second series (reuse `telemetry._resample`).
- Speed series from `velocity_smooth × 3.6` (fall back to a distance derivative if
  velocity_smooth is absent).
- No `latlng` (indoor / no GPS) → a clear error; the whole path is best-effort with
  clear messages, and `--gpx` remains the offline route.

## Components

- `strava_client.get_activity(activity_id: str) -> dict` — the raw activity JSON
  (`GET /activities/{id}`). Raises `RuntimeError` if not configured. (The existing
  `get_activity_stats` keeps working; this returns the full object for name/start/totals.)
- `strava_gpx.py` (new) — `gpx_from_strava(activity_id: str) -> GpxData`:
  1. `activity = strava_client.get_activity(activity_id)`; `streams =
     strava_client.get_activity_streams(activity_id)`.
  2. Require `latlng` in the streams, else `raise RuntimeError("Strava activity
     {id} has no GPS track (latlng stream missing).")`.
  3. `start_time` = parse `activity["start_date"]` (ISO → tz-aware UTC).
  4. `n` = number of activity-seconds from the `time` stream (`int(max(time)) + 1`),
     else `len(latlng)`.
  5. Per-second series via `telemetry._resample(times, data, n)`:
     - `coords` from `latlng` (resample lat and lon separately → list of `(lat, lon)`).
     - `elevations_m` from `altitude` (else `[0.0]*n`).
     - `hr_bpm` from `heartrate` (else `[None]*n`).
     - `cum_distance_m` from `distance` (else `[0.0]*n`).
     - `speeds_kmh` from `velocity_smooth × 3.6`; if absent, derive from
       `cum_distance_m` (per-second central difference, like the HUD's
       `_speed_from_distance`).
  6. Totals from the summary (authoritative): `total_distance_km =
     activity["distance"]/1000`, `elevation_gain_m = activity.get(
     "total_elevation_gain", 0.0)`, `moving_time_s = activity.get("moving_time", 0.0)`.
  7. `name = activity.get("name")`; `first_coord = coords[0]`.
  Returns a `GpxData` with all fields populated (same shape as `load_gpx`).
- `main.py`:
  - `--gpx` no longer `required` (`--strava-activity-id` already exists).
  - New helper `resolve_gpx_source(args) -> GpxData`: if `args.gpx` →
    `load_gpx(args.gpx)`; elif `args.strava_activity_id` and
    `strava_client.is_configured()` → `strava_gpx.gpx_from_strava(args.strava_activity_id)`
    (and print "GPX: opgebouwd uit Strava-activity {id}" to stderr); else →
    `raise SystemExit("Geef --gpx of --strava-activity-id (met Strava geconfigureerd).")`.
  - `main()` calls `gpx = resolve_gpx_source(args)` where it currently does
    `gpx = load_gpx(args.gpx)`. The rest of the pipeline is unchanged (it takes a
    `GpxData`).

## Reuse / imports

`strava_gpx.py` imports `strava_client`, `highlight_detector.GpxData`, and
`telemetry._resample` (and reuses the distance-derivative idea). No circular import:
`strava_client` and `telemetry` do not import `strava_gpx`; `highlight_detector` does
not import `strava_gpx`.

## Error handling

- Strava not configured / no activity id (and no `--gpx`) → clear message, exit code 2.
- `get_activity`/`get_activity_streams` HTTP failure → the exception propagates with a
  clear message (the user asked for Strava; we don't silently continue).
- `latlng` missing → `RuntimeError` as above.
- `time` stream missing → fall back to `len(latlng)` for `n`.

## Testing (pytest)

- `get_activity`: mocked requests → returns the activity dict; raises when unconfigured.
- `gpx_from_strava` (mocked `get_activity` + `get_activity_streams`): builds a `GpxData`
  with the activity name, tz-aware UTC start_time, per-second coords/elevations/hr/
  cum_distance of length n, speeds from velocity_smooth×3.6, and totals from the
  summary (distance/elev/moving_time). first_coord == coords[0].
- `gpx_from_strava` with no `latlng` → raises RuntimeError mentioning "no GPS track".
- `gpx_from_strava` with velocity_smooth absent → speeds derived from distance (non-None).
- `resolve_gpx_source` (tested in isolation, no full CLI run): with `--gpx` present it
  calls load_gpx and does NOT touch Strava; with only `--strava-activity-id` (+ Strava
  configured, both monkeypatched) it calls gpx_from_strava; with neither it raises
  SystemExit with the error message.

## Out of scope

- Auto-music (feature #2) and multiple clips (feature #3) — separate specs.
- Caching the fetched track to disk.
