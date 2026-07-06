from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import gpxpy


@dataclass
class GpxData:
    start_time: datetime                       # tz-aware UTC (first trackpoint)
    speeds_kmh: list[float] = field(default_factory=list)  # per-second series
    coords: list[tuple[float, float]] = field(default_factory=list)  # per-second lat,lon
    total_distance_km: float = 0.0
    elevation_gain_m: float = 0.0
    moving_time_s: float = 0.0
    first_coord: tuple[float, float] = (0.0, 0.0)


def compute_offset_seconds(video_start: datetime, activity_start: datetime) -> float:
    """Seconds to add to a video timestamp to reach activity time."""
    return (video_start - activity_start).total_seconds()


def video_time_to_activity_index(video_t: float, offset_seconds: float, series_len: int) -> int:
    """Map a video-relative time (seconds) to an index into the per-second GPS series."""
    idx = int(round(video_t + offset_seconds))
    if idx < 0:
        return 0
    if idx > series_len - 1:
        return series_len - 1
    return idx


def speed_at_video_time(video_t: float, offset_seconds: float, speeds_kmh: list[float]) -> float:
    idx = video_time_to_activity_index(video_t, offset_seconds, len(speeds_kmh))
    return speeds_kmh[idx]


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_gpx(path: str) -> GpxData:
    """Parse a GPX file into a per-second GpxData series."""
    with open(path, "r") as f:
        gpx = gpxpy.parse(f)

    points = [p for track in gpx.tracks for seg in track.segments for p in seg.points]
    if not points:
        raise ValueError(f"No trackpoints found in GPX file: {path}")

    start = _to_utc(points[0].time)
    duration = int((_to_utc(points[-1].time) - start).total_seconds())

    # Per-second series. Seconds with no trackpoint start as None and are
    # forward-filled below from the nearest earlier known value.
    speeds: list[float | None] = [None] * (duration + 1)
    coords: list[tuple[float, float] | None] = [None] * (duration + 1)

    total_distance_m = 0.0
    elevation_gain_m = 0.0
    moving_time_s = 0.0
    prev = None
    for p in points:
        t = _to_utc(p.time)
        sec = int((t - start).total_seconds())
        sec = max(0, min(duration, sec))
        if prev is not None:
            dist = p.distance_3d(prev) or 0.0
            dt = (t - _to_utc(prev.time)).total_seconds()
            speed_kmh = (dist / dt) * 3.6 if dt > 0 else 0.0
            speeds[sec] = speed_kmh
            coords[sec] = (p.latitude, p.longitude)
            total_distance_m += dist
            if speed_kmh > 3.0:
                moving_time_s += dt
            if p.elevation is not None and prev.elevation is not None:
                gain = p.elevation - prev.elevation
                if gain > 0:
                    elevation_gain_m += gain
        else:
            speeds[sec] = 0.0
            coords[sec] = (p.latitude, p.longitude)
        prev = p

    # Forward-fill seconds with no trackpoint from the last known value,
    # seeded with speed 0.0 and the first point's coord so index 0 is defined.
    last_speed = 0.0
    last_coord = (points[0].latitude, points[0].longitude)
    for i in range(len(speeds)):
        if speeds[i] is None:
            speeds[i] = last_speed
        else:
            last_speed = speeds[i]
        if coords[i] is None:
            coords[i] = last_coord
        else:
            last_coord = coords[i]

    return GpxData(
        start_time=start,
        speeds_kmh=speeds,
        coords=coords,
        total_distance_km=total_distance_m / 1000.0,
        elevation_gain_m=elevation_gain_m,
        moving_time_s=moving_time_s,
        first_coord=(points[0].latitude, points[0].longitude),
    )
