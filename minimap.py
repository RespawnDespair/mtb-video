from __future__ import annotations

import math


def compute_bounds(coords):
    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]
    return (min(lats), max(lats), min(lons), max(lons), sum(lats) / len(lats))


def project(lat, lon, bounds, w, h, pad):
    """Project a lat/lon to pixel coords in a w×h box (north up, aspect preserved)."""
    min_lat, max_lat, min_lon, max_lon, mean_lat = bounds
    # equirectangular: scale longitude by cos(mean latitude)
    cos_lat = math.cos(math.radians(mean_lat))
    span_x = (max_lon - min_lon) * cos_lat
    span_y = (max_lat - min_lat)
    inner_w = w - 2 * pad
    inner_h = h - 2 * pad
    if span_x < 1e-12 and span_y < 1e-12:
        return (w / 2.0, h / 2.0)   # single point / degenerate -> centre
    scale = min(inner_w / span_x if span_x > 1e-12 else float("inf"),
                inner_h / span_y if span_y > 1e-12 else float("inf"))
    # centre the drawing within the box
    draw_w = span_x * scale
    draw_h = span_y * scale
    ox = pad + (inner_w - draw_w) / 2.0
    oy = pad + (inner_h - draw_h) / 2.0
    px = ox + ((lon - min_lon) * cos_lat) * scale
    py = oy + (max_lat - lat) * scale   # flip: north (max_lat) at top
    return (px, py)


def project_track(coords, w, h, pad):
    bounds = compute_bounds(coords)
    return [project(la, lo, bounds, w, h, pad) for la, lo in coords]
