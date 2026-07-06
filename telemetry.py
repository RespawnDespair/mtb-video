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
