# Strava streams as HUD telemetry source (+ power) — Design

**Date:** 2026-07-06
**Status:** Approved for planning
**Context:** The HUD's speed is computed from GPX position deltas (~1 Hz, no speed
field), so it's noisy and lags. Strava exposes accurate per-second **streams**
(`velocity_smooth`, `heartrate`, `watts`, `altitude`, `grade_smooth`, `latlng`,
`distance`) — the same data Strava derives its segment stats from. Use those as the
HUD telemetry source when available, and add a power readout (watts are not in the
GPX at all).

## Decisions (from brainstorming)

- Source = Strava **per-second streams** (not segment averages, which would be
  static). Per-field fallback to GPX when a stream is absent.
- Add a **power (W)** readout to the HUD, top-left under HR, shown only when watts
  data exists (placement approved via a rendered preview).
- Slope uses Strava **`grade_smooth`** (fallback: our computed slope).
- Streams apply in **segment mode only** (where the HUD lives). Flow mode unchanged.

## Data flow

```
segment mode + Strava configured + activity id
   → strava_client.get_activity_streams(activity_id)  (once)
   → telemetry.telemetry_from_streams(streams) → per-second TelemetrySeries
        (speeds_kmh, hr_bpm, watts, elevations_m, slopes_pct, coords, cum_distance_m)
   fallback: GpxData (current) when streams unavailable / per field when a key is missing
   → sample_telemetry(source, activity_t, segment_start_s) → TelemetrySample(+power_w, slope)
   → hud_renderer.render_hud_frame (adds power panel when power_w is not None)
```

## Components

### strava_client.py
- `get_activity_streams(activity_id: str) -> dict` — calls
  `GET /activities/{id}/streams?keys=time,latlng,distance,altitude,velocity_smooth,heartrate,watts,grade_smooth&key_by_type=true`,
  returns `{type: {"data": [...]}, ...}` (Strava's `key_by_type` shape). Raises
  `RuntimeError` if not configured (caller catches). Missing types are simply absent.

### telemetry.py
- `@dataclass TelemetrySeries` — the source-neutral per-second container the sampler
  reads: `speeds_kmh`, `elevations_m`, `hr_bpm`, `watts`, `slopes_pct` (per-second or
  `None`), `coords`, `cum_distance_m`. (GpxData already provides all but `watts`/
  `slopes_pct`; a thin adapter wraps a GpxData into a TelemetrySeries with
  `watts=[None]*n` and `slopes_pct=None` so slope falls back to computed.)
- `telemetry_from_streams(streams) -> TelemetrySeries` — resample each present stream
  onto integer seconds using the `time` array (nearest/interp); absent streams →
  a `None`-filled or empty series (triggering per-field fallback):
  - `speeds_kmh` = `velocity_smooth` × 3.6
  - `hr_bpm` = `heartrate`; `watts` = `watts`
  - `elevations_m` = `altitude`; `slopes_pct` = `grade_smooth`
  - `coords` = `latlng`; `cum_distance_m` = `distance`
- `TelemetrySample` gains `power_w: float | None`.
- `sample_telemetry(source, activity_time_s, segment_start_s)` accepts a
  TelemetrySeries (or a plain `GpxData`). It reads optional fields via
  `getattr(source, "watts", None)` / `getattr(source, "slopes_pct", None)` so a bare
  `GpxData` (no watts/slopes) keeps working and the existing telemetry tests stay
  green. It interpolates each field; **per-field fallback**: when the source's series
  for a field is empty/all-None, fall back to the GPX-derived value (speed computed,
  slope computed) so a partial stream set still works. `slope_pct` comes from
  `slopes_pct` when present, else the computed `slope_pct(...)`; `power_w` from
  `watts` when present, else `None`.

### hud_renderer.py
- Add a power panel top-left under the HR panel (same styling), drawn only when
  `sample.power_w is not None`. Everything else unchanged.

### video_editor.py / main.py
- Segment mode: attempt `get_activity_streams` once; on success build a
  `TelemetrySeries` via `telemetry_from_streams` and pass it as the HUD telemetry
  source; on any failure / not configured, pass the GpxData adapter (current
  behavior). `build_segment_reel` / `_render_hud_pngs` take a `telemetry_source`
  instead of assuming `gpx` (the GPX is still needed for the minimap coords when
  streams lack `latlng` — keep both available).

## Error handling

- Streams fetch fails / Strava not configured → full GPX source (unchanged HUD).
- A stream key absent (e.g. no power meter → no `watts`) → that field falls back
  (power omitted; speed/slope computed from GPX).
- Stream arrays shorter/longer than the ride → resampled on the `time` array; indices
  clamped; never crash.
- `time` array missing → treat streams as unavailable, use GPX.

## Testing (pytest)

- `telemetry_from_streams`: resamples velocity_smooth→km/h, heartrate, watts,
  altitude, grade_smooth, latlng, distance onto per-second arrays; a stream set
  missing `watts` yields `watts` all-None.
- `sample_telemetry` with a stream source: returns stream speed (not computed),
  `power_w` from watts, slope from grade_smooth; and per-field fallback to GPX when a
  field's series is empty.
- `get_activity_streams`: mocked requests — correct keys param + `key_by_type`, raises
  when unconfigured.
- `hud_renderer`: a sample with `power_w` set draws a non-transparent power panel in
  the top-left region; `power_w=None` draws none.
- Existing real-ffmpeg HUD smoke stays green.

## Out of scope

- Streams in flow mode (segment-only).
- Segment averages (we use accurate per-second streams).
- Caching streams to disk between runs.
