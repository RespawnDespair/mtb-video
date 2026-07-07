from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone

import cv2
import gpxpy
import numpy as np

from config import Config

_MIN_CORR_OVERLAP = 10
_FILENAME_TS_RE = re.compile(r"VID_(\d{8})_(\d{6})")


@dataclass
class GpxData:
    start_time: datetime                       # tz-aware UTC (first trackpoint)
    speeds_kmh: list[float] = field(default_factory=list)  # per-second series
    coords: list[tuple[float, float]] = field(default_factory=list)  # per-second lat,lon
    total_distance_km: float = 0.0
    elevation_gain_m: float = 0.0
    moving_time_s: float = 0.0
    first_coord: tuple[float, float] = (0.0, 0.0)
    elevations_m: list = field(default_factory=list)      # per-second, forward-filled
    hr_bpm: list = field(default_factory=list)             # per-second, None where absent
    cum_distance_m: list = field(default_factory=list)     # per-second cumulative distance
    name: "str | None" = None


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


def parse_filename_datetime(name: str) -> "datetime | None":
    """Parse VID_YYYYMMDD_HHMMSS from a filename into a naive local datetime."""
    m = _FILENAME_TS_RE.search(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def get_filename_timestamp(path: str) -> "datetime | None":
    """Filename timestamp localized to tz-aware UTC via the system timezone."""
    naive = parse_filename_datetime(os.path.basename(path))
    if naive is None:
        return None
    # Interpret as local wall-clock time, then convert to UTC.
    local = naive.astimezone()  # attaches system local tz to a naive datetime
    return local.astimezone(timezone.utc)


def load_gpx(path: str) -> GpxData:
    """Parse a GPX file into a per-second GpxData series."""
    with open(path, "r") as f:
        gpx = gpxpy.parse(f)

    gpx_name = None
    for track in gpx.tracks:
        if track.name:
            gpx_name = track.name
            break
    if gpx_name is None:
        gpx_name = getattr(gpx, "name", None)

    points = [p for track in gpx.tracks for seg in track.segments for p in seg.points]
    if not points:
        raise ValueError(f"No trackpoints found in GPX file: {path}")

    start = _to_utc(points[0].time)
    duration = int((_to_utc(points[-1].time) - start).total_seconds())

    # Per-second series. Seconds with no trackpoint start as None and are
    # forward-filled below from the nearest earlier known value.
    speeds: list[float | None] = [None] * (duration + 1)
    coords: list[tuple[float, float] | None] = [None] * (duration + 1)
    elevations: list[float | None] = [None] * (duration + 1)
    hrs: list[int | None] = [None] * (duration + 1)
    cumdist: list[float | None] = [None] * (duration + 1)

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
            elevations[sec] = p.elevation if p.elevation is not None else 0.0
            hrs[sec] = _parse_hr(p)
            cumdist[sec] = total_distance_m  # running 2D/3D distance already accumulated
        else:
            speeds[sec] = 0.0
            coords[sec] = (p.latitude, p.longitude)
            elevations[sec] = p.elevation if p.elevation is not None else 0.0
            hrs[sec] = _parse_hr(p)
            cumdist[sec] = 0.0
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

    last_elevation = elevations[0] if elevations[0] is not None else 0.0
    last_hr = hrs[0]
    last_cumdist = 0.0
    for i in range(len(elevations)):
        if elevations[i] is None:
            elevations[i] = last_elevation
        else:
            last_elevation = elevations[i]
        if hrs[i] is None:
            hrs[i] = last_hr
        else:
            last_hr = hrs[i]
        if cumdist[i] is None:
            cumdist[i] = last_cumdist
        else:
            last_cumdist = cumdist[i]

    return GpxData(
        start_time=start,
        speeds_kmh=speeds,
        coords=coords,
        total_distance_km=total_distance_m / 1000.0,
        elevation_gain_m=elevation_gain_m,
        moving_time_s=moving_time_s,
        first_coord=(points[0].latitude, points[0].longitude),
        elevations_m=elevations,
        hr_bpm=hrs,
        cum_distance_m=cumdist,
        name=gpx_name,
    )


def _ffprobe_json(path: str) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def get_video_creation_time(path: str) -> tuple[datetime, bool]:
    """Return (utc creation time, from_metadata). Falls back to file mtime."""
    info = _ffprobe_json(path)
    tag = info.get("format", {}).get("tags", {}).get("creation_time")
    if tag:
        # ffprobe emits ISO 8601, usually ending in 'Z'
        dt = datetime.fromisoformat(tag.replace("Z", "+00:00"))
        return _to_utc(dt), True
    mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
    return mtime, False


def get_video_duration(path: str) -> float:
    info = _ffprobe_json(path)
    return float(info.get("format", {}).get("duration", 0.0))


def get_video_resolution(path: str) -> tuple[int, int]:
    info = _ffprobe_json(path)
    vs = next(s for s in info.get("streams", []) if s.get("codec_type") == "video")
    return int(vs["width"]), int(vs["height"])


@dataclass
class Segment:
    start: float
    end: float
    score: float


def score_seconds(speeds_at_video_secs, flow_per_sec, cfg: Config) -> list[float]:
    """Combine per-second GPS speed and optical flow into interest scores [0,1].

    Uses absolute reference scaling (not whole-ride min/max) so scores are
    comparable across rides and well-defined for any series length.
    """
    n = min(len(speeds_at_video_secs), len(flow_per_sec))
    speeds = list(speeds_at_video_secs[:n])
    flows = list(flow_per_sec[:n])
    scores = []
    for i in range(n):
        if speeds[i] < cfg.min_speed_kmh:
            scores.append(0.0)  # standstill dropped outright
            continue
        speed_norm = min(1.0, speeds[i] / cfg.speed_reference_kmh)
        flow_norm = min(1.0, flows[i] / cfg.flow_reference)
        s = cfg.gps_weight * speed_norm + cfg.flow_weight * flow_norm
        scores.append(max(0.0, min(1.0, s)))
    return scores


def merge_segments(scores: list[float], cfg: Config) -> list[Segment]:
    """Turn per-second scores into kept segments, bridging small gaps."""
    kept = [i for i, s in enumerate(scores) if s >= cfg.score_cutoff]
    if not kept:
        return []
    bridge = int(round(cfg.gap_bridge_seconds))
    segments: list[Segment] = []
    run_start = kept[0]
    prev = kept[0]
    for idx in kept[1:]:
        if idx - prev <= bridge + 1:
            prev = idx
            continue
        segments.append((run_start, prev))
        run_start = idx
        prev = idx
    segments.append((run_start, prev))

    result: list[Segment] = []
    for a, b in segments:
        start = float(a)
        end = float(b + 1)  # inclusive second -> exclusive end
        if end - start < cfg.min_segment_seconds:
            continue
        avg = float(np.mean(scores[a:b + 1]))
        result.append(Segment(start=start, end=end, score=avg))
    return result


def compute_optical_flow_per_second(video_path: str, cfg: Config) -> list[float]:
    """Farneback optical flow sampled per second -> mean motion magnitude per second."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV could not open video: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(fps / cfg.flow_sample_fps)))

    per_second: dict[int, list[float]] = {}
    prev_gray = None
    frame_idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_idx % step == 0:
            gray = cv2.cvtColor(cv2.resize(frame, (320, 180)), cv2.COLOR_BGR2GRAY)
            if prev_gray is not None:
                flow = cv2.calcOpticalFlowFarneback(
                    prev_gray, gray, None, 0.5, 3, 15, 3, 5, 1.2, 0
                )
                mag = float(np.mean(np.linalg.norm(flow, axis=2)))
                sec = int(frame_idx / fps)
                per_second.setdefault(sec, []).append(mag)
            prev_gray = gray
        frame_idx += 1
    cap.release()

    if not per_second:
        return []
    duration_secs = max(per_second.keys()) + 1
    return [float(np.mean(per_second.get(s, [0.0]))) for s in range(duration_secs)]


def detect_highlights(video_path: str, gpx: GpxData, cfg: Config):
    """Sync GPS to video, run optical flow, score, and merge into segments."""
    a = analyze_video(video_path, gpx, cfg)
    return a.segments, a.scores  # thin wrapper over analyze_video


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson correlation of two equal-length arrays; 0.0 if either is flat."""
    if a.size < 2:
        return 0.0
    a_sd = a.std()
    b_sd = b.std()
    if a_sd < 1e-9 or b_sd < 1e-9:
        return 0.0
    return float(np.mean((a - a.mean()) * (b - b.mean())) / (a_sd * b_sd))


def estimate_offset_by_motion(flow_per_sec, gpx: GpxData, cfg: Config):
    """Find the offset (video_t -> activity index = t + offset) that best aligns
    the video's per-second motion with GPS speed, via max Pearson correlation.

    Returns (offset_seconds, correlation). offset is an integer lag as float.
    """
    flow = np.asarray(flow_per_sec, dtype=float)
    speeds = np.asarray(gpx.speeds_kmh, dtype=float)
    n_flow = flow.size
    n_speed = speeds.size
    if n_flow < 2 or n_speed < 2:
        return 0.0, 0.0

    # Candidate lags: video may start before the GPX (negative) or anywhere within it.
    min_overlap = max(2, min(_MIN_CORR_OVERLAP, len(flow)))
    best_offset = 0.0
    best_corr = 0.0
    found = False
    for lag in range(-n_flow + 1, n_speed):
        # Overlap where both flow[t] and speeds[t + lag] are valid.
        t_start = max(0, -lag)
        t_end = min(n_flow, n_speed - lag)
        if t_end - t_start < min_overlap:
            continue
        fseg = flow[t_start:t_end]
        sseg = speeds[t_start + lag:t_end + lag]
        corr = _pearson(fseg, sseg)
        if not found or corr > best_corr:
            best_corr = corr
            best_offset = float(lag)
            found = True
    return best_offset, best_corr


@dataclass
class Analysis:
    flow_per_sec: list[float]
    speeds_at_video: list[float]
    scores: list[float]
    segments: list[Segment]
    offset_used: float
    offset_source: str            # "manual" | "auto" | "metadata" | "filename" | "mtime"
    metadata_offset: float
    filename_offset: "float | None"
    auto_offset: float
    auto_correlation: float
    video_creation_time: datetime
    from_metadata: bool
    video_duration: float
    fps: float


def _resolve_offset(metadata_offset, from_metadata, filename_offset,
                    auto_offset, offset_override, use_auto):
    """Pick the offset by precedence: manual > auto > metadata > filename > mtime."""
    if offset_override is not None:
        return float(offset_override), "manual"
    if use_auto:
        return float(auto_offset), "auto"
    if from_metadata:
        return float(metadata_offset), "metadata"
    if filename_offset is not None:
        return float(filename_offset), "filename"
    return float(metadata_offset), "mtime"


@dataclass
class ResolvedOffset:
    offset_used: float
    offset_source: str
    metadata_offset: float
    filename_offset: "float | None"
    auto_offset: float
    auto_correlation: float
    video_creation_time: datetime
    from_metadata: bool


def resolve_sync_offset(video_path, gpx, cfg, offset_override=None,
                        use_auto=False, flow=None) -> ResolvedOffset:
    """Resolve the sync offset without computing optical flow unless auto is used."""
    video_start, from_metadata = get_video_creation_time(video_path)
    metadata_offset = compute_offset_seconds(video_start, gpx.start_time)

    fname_dt = get_filename_timestamp(video_path)
    filename_offset = (compute_offset_seconds(fname_dt, gpx.start_time)
                       if fname_dt is not None else None)

    auto_offset, auto_corr = 0.0, 0.0
    if use_auto:
        f = flow if flow is not None else compute_optical_flow_per_second(video_path, cfg)
        auto_offset, auto_corr = estimate_offset_by_motion(f, gpx, cfg)

    offset_used, source = _resolve_offset(
        metadata_offset, from_metadata, filename_offset,
        auto_offset, offset_override, use_auto,
    )
    return ResolvedOffset(
        offset_used=offset_used, offset_source=source,
        metadata_offset=metadata_offset, filename_offset=filename_offset,
        auto_offset=auto_offset, auto_correlation=auto_corr,
        video_creation_time=video_start, from_metadata=from_metadata,
    )


def analyze_video(video_path, gpx: GpxData, cfg: Config,
                  offset_override=None, use_auto=False) -> Analysis:
    """Compute flow once, resolve the sync offset, score, and merge (flow mode)."""
    duration = int(round(get_video_duration(video_path)))
    flow = compute_optical_flow_per_second(video_path, cfg)
    if not flow:
        flow = [0.0] * duration

    r = resolve_sync_offset(video_path, gpx, cfg, offset_override, use_auto, flow=flow)

    speeds_at_video = [
        speed_at_video_time(float(t), r.offset_used, gpx.speeds_kmh) for t in range(duration)
    ]
    scores = score_seconds(speeds_at_video, flow, cfg)
    segments = merge_segments(scores, cfg)

    # fps for reporting (best-effort; 0.0 if unavailable)
    fps = 0.0
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        cap.release()
    except Exception:
        pass

    return Analysis(
        flow_per_sec=flow, speeds_at_video=speeds_at_video, scores=scores,
        segments=segments, offset_used=r.offset_used, offset_source=r.offset_source,
        metadata_offset=r.metadata_offset, filename_offset=r.filename_offset,
        auto_offset=r.auto_offset, auto_correlation=r.auto_correlation,
        video_creation_time=r.video_creation_time, from_metadata=r.from_metadata,
        video_duration=float(duration), fps=fps,
    )
