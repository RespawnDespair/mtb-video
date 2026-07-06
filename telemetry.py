from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class TelemetrySample:
    speed_kmh: float
    elevation_m: float
    slope_pct: float
    hr_bpm: "float | None"
    lat: float
    lon: float
    seg_distance_km: float
    power_w: "float | None" = None


@dataclass
class TelemetrySeries:
    speeds_kmh: list = field(default_factory=list)
    elevations_m: list = field(default_factory=list)
    hr_bpm: list = field(default_factory=list)
    watts: list = field(default_factory=list)
    slopes_pct: "list | None" = None
    coords: list = field(default_factory=list)
    cum_distance_m: list = field(default_factory=list)


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
