from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class SegmentEffort:
    name: str
    start_date: datetime
    elapsed_time: float
    distance_m: float
    average_watts: "float | None" = None
    average_heartrate: "float | None" = None
    pr_rank: "int | None" = None
    has_achievement: bool = False
    starred: bool = False


@dataclass
class SegmentClip:
    start: float
    end: float
    name: str
    stats: dict = field(default_factory=dict)


def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_efforts(raw: list) -> list:
    """Normalize raw Strava segment_effort dicts into SegmentEffort objects."""
    out = []
    for e in raw:
        seg = e.get("segment") or {}
        out.append(SegmentEffort(
            name=e.get("name") or seg.get("name") or "Segment",
            start_date=_parse_dt(e["start_date"]),
            elapsed_time=float(e.get("elapsed_time") or 0.0),
            distance_m=float(e.get("distance") or 0.0),
            average_watts=e.get("average_watts"),
            average_heartrate=e.get("average_heartrate"),
            pr_rank=e.get("pr_rank"),
            has_achievement=bool(e.get("achievements")),
            starred=bool(seg.get("starred")),
        ))
    return out


def is_noteworthy(effort: SegmentEffort) -> bool:
    """Keep efforts with an achievement, a PR rank, or a starred segment."""
    return effort.has_achievement or effort.pr_rank is not None or effort.starred


def efforts_to_clips(efforts, gpx_start: datetime, offset_seconds: float,
                     video_duration: float, cfg) -> list:
    """Map noteworthy segment efforts to video-time clips within the video window."""
    clips = []
    for e in efforts:
        if not is_noteworthy(e):
            continue
        activity_start_s = (e.start_date - gpx_start).total_seconds()
        video_start = activity_start_s - offset_seconds
        start = max(0.0, video_start)
        end = min(video_duration, video_start + e.elapsed_time)
        if end - start < cfg.min_segment_seconds:
            continue
        speed_kmh = (e.distance_m / e.elapsed_time * 3.6) if e.elapsed_time > 0 else 0.0
        stats = {
            "elapsed_s": e.elapsed_time,
            "speed_kmh": speed_kmh,
            "power_w": round(e.average_watts) if e.average_watts is not None else None,
            "hr_bpm": round(e.average_heartrate) if e.average_heartrate is not None else None,
        }
        clips.append(SegmentClip(start=start, end=end, name=e.name, stats=stats))
    clips.sort(key=lambda c: c.start)
    return clips


def efforts_to_activity_ranges(efforts, gpx_start: datetime, cfg) -> list:
    """Noteworthy efforts as ride-time ranges (activity_start, activity_end, name, stats).
    No offset/duration clamping here — coverage clamping happens in resolve_render_parts."""
    ranges = []
    for e in efforts:
        if not is_noteworthy(e):
            continue
        if e.elapsed_time < cfg.min_segment_seconds:
            continue
        a0 = (e.start_date - gpx_start).total_seconds()
        speed_kmh = (e.distance_m / e.elapsed_time * 3.6) if e.elapsed_time > 0 else 0.0
        stats = {
            "elapsed_s": e.elapsed_time,
            "speed_kmh": speed_kmh,
            "power_w": round(e.average_watts) if e.average_watts is not None else None,
            "hr_bpm": round(e.average_heartrate) if e.average_heartrate is not None else None,
        }
        ranges.append((a0, a0 + e.elapsed_time, e.name, stats))
    ranges.sort(key=lambda r: r[0])
    return ranges


def format_segment_stats(clip: SegmentClip) -> str:
    """A stats line like '4:10 · 16.3 km/u · 175 W · 153 bpm' (missing fields omitted)."""
    s = clip.stats
    parts = []
    elapsed = int(s.get("elapsed_s") or 0)
    parts.append(f"{elapsed // 60}:{elapsed % 60:02d}")
    if s.get("speed_kmh") is not None:
        parts.append(f"{s['speed_kmh']:.1f} km/u")
    if s.get("power_w") is not None:
        parts.append(f"{s['power_w']} W")
    if s.get("hr_bpm") is not None:
        parts.append(f"{s['hr_bpm']} bpm")
    return " · ".join(parts)
