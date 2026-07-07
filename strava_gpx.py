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
    n = int(max(times)) + 1

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

    start = activity.get("start_date")
    if not start:
        raise RuntimeError(f"Strava activity {activity_id} has no start_date.")

    return GpxData(
        start_time=_parse_dt(start),
        speeds_kmh=speeds, coords=coords, elevations_m=elevations, hr_bpm=hr_bpm,
        cum_distance_m=cum_distance,
        total_distance_km=(activity.get("distance") or 0.0) / 1000.0,
        elevation_gain_m=activity.get("total_elevation_gain") or 0.0,
        moving_time_s=activity.get("moving_time") or 0.0,
        first_coord=coords[0], name=activity.get("name"))
