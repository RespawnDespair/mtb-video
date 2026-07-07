from __future__ import annotations

import sys
from dataclasses import dataclass

import highlight_detector
from highlight_detector import (get_video_duration, get_video_resolution)

_EPS = 1e-6


@dataclass
class ClipSource:
    path: str
    base_offset: float          # ride-seconds at this file's local t=0
    duration: float
    width: int
    height: int
    creation_time: object
    offset_source: str


@dataclass
class RenderPart:
    source_path: str
    local_start: float
    local_end: float
    base_offset: float          # ride_t = local_t + base_offset (for HUD/telemetry)
    name: "str | None" = None
    stats: "dict | None" = None


def build_clip_sources(video_paths, gpx, cfg, offset_override=None,
                       use_auto=False) -> list:
    """Place each file on the ride timeline.

    Each file gets its own recording-time offset (creation_time / filename / mtime) via
    resolve_sync_offset. The relative spacing between files comes from those offsets and
    is reliable (same camera). When --sync-offset is given it ANCHORS the earliest-
    recorded file at that value — exactly the absolute meaning it has for a single file —
    and the other files are placed by their recording-time deltas relative to it (so a
    single shared correction fixes a constant camera-clock skew across all files). With
    no --sync-offset the raw per-file recording offsets are used as-is."""
    probed = []
    for path in video_paths:
        r = highlight_detector.resolve_sync_offset(path, gpx, cfg, offset_override=None,
                                                   use_auto=use_auto)
        w, h = get_video_resolution(path)
        probed.append((path, r, w, h, get_video_duration(path)))

    ref = min(r.offset_used for _, r, _, _, _ in probed)  # earliest-recorded file
    sources = []
    for path, r, w, h, dur in probed:
        if offset_override is not None:
            base = float(offset_override) + (r.offset_used - ref)
        else:
            base = r.offset_used
        sources.append(ClipSource(
            path=path, base_offset=base, duration=dur, width=w, height=h,
            creation_time=r.video_creation_time, offset_source=r.offset_source))
    sources.sort(key=lambda s: s.base_offset)

    res = {(s.width, s.height) for s in sources}
    if len(res) > 1:
        print(f"[warn] videobestanden hebben verschillende resoluties {sorted(res)}; "
              "ze worden naar de doelhoogte geschaald.", file=sys.stderr)
    for a, b in zip(sources, sources[1:]):
        if b.base_offset < a.base_offset + a.duration - _EPS:
            print(f"[warn] overlappende dekking tussen {a.path} en {b.path}; "
                  "overlap wordt eenmalig gerenderd.", file=sys.stderr)
    return sources


def resolve_render_parts(sources, ranges):
    """Map ride-time ranges onto source coverage. Returns (parts, dropped_names).
    Never renders a ride-second twice (earliest/longest source wins on overlap)."""
    parts = []
    dropped = []
    ordered = sorted(sources, key=lambda s: (s.base_offset, -s.duration))
    for (a_start, a_end, name, stats) in ranges:
        cursor = a_start          # earliest ride-second still needing coverage
        made = False
        for s in ordered:
            cov0, cov1 = s.base_offset, s.base_offset + s.duration
            ov0 = max(cursor, cov0)
            ov1 = min(a_end, cov1)
            if ov1 - ov0 > _EPS:
                parts.append(RenderPart(
                    source_path=s.path, local_start=ov0 - s.base_offset,
                    local_end=ov1 - s.base_offset, base_offset=s.base_offset,
                    name=name, stats=stats))
                made = True
                cursor = ov1      # advance past what we just covered
            if cursor >= a_end - _EPS:
                break
        if not made:
            dropped.append(name)
    parts.sort(key=lambda p: p.base_offset + p.local_start)
    return parts, dropped
