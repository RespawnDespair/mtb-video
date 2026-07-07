from __future__ import annotations

import math


def _bearing(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return math.degrees(math.atan2(x, y))


def heading_change_per_sec(coords, speeds_kmh, min_speed_kmh=8.0):
    """Per-second absolute heading change (deg), zeroed where speed <= min_speed."""
    n = len(coords)
    turn = [0.0] * n
    for i in range(2, n):
        delta = abs((_bearing(coords[i - 1], coords[i])
                     - _bearing(coords[i - 2], coords[i - 1]) + 180) % 360 - 180)
        moving = i < len(speeds_kmh) and speeds_kmh[i] > min_speed_kmh
        turn[i] = delta if moving else 0.0
    return turn


def curviest_window(turn, offset_seconds, video_duration, clip_len):
    """Video-time start of the curviest clip_len window inside the video's activity
    span [offset, offset + video_duration - clip_len]. 0.0 if nothing fits."""
    off = int(round(offset_seconds))
    clip = int(round(clip_len))
    lo = max(0, off)
    hi = off + int(video_duration) - clip
    if hi < lo:
        return 0.0
    best_a, best_s = None, -1.0
    for a in range(lo, hi + 1):
        if a + clip > len(turn):
            break
        s = sum(turn[a:a + clip])
        if s > best_s:
            best_s, best_a = s, a
    if best_a is None:
        return 0.0
    return max(0.0, float(best_a - off))


def curviest_window_across(sources, turn, clip_len):
    """Across all sources' covered ride spans, pick the (source_path, local_start) whose
    clip_len window has the highest summed heading change. Falls back to the first
    source at local 0.0 if nothing scores."""
    clip = int(round(clip_len))
    best = (None, 0.0, -1.0)          # (path, local_start, score)
    for s in sources:
        lo = int(round(s.base_offset))
        hi = int(round(s.base_offset + s.duration)) - clip
        for a in range(max(0, lo), hi + 1):
            if a + clip > len(turn):
                break
            score = sum(turn[a:a + clip])
            if score > best[2]:
                best = (s.path, float(a - s.base_offset), score)
    if best[0] is None:
        return (sources[0].path, 0.0) if sources else ("", 0.0)
    return best[0], max(0.0, best[1])
