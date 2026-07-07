from __future__ import annotations

import os

from PIL import Image, ImageDraw

from hud_renderer import _font, _RED, _WHITE
from minimap import compute_bounds, project

_NL_MONTHS = ["", "januari", "februari", "maart", "april", "mei", "juni", "juli",
              "augustus", "september", "oktober", "november", "december"]


def intro_date(gpx) -> str:
    dt = gpx.start_time.astimezone()
    return f"{dt.day} {_NL_MONTHS[dt.month]} {dt.year} · {dt.hour:02d}:{dt.minute:02d}"


def intro_title(gpx, gpx_path=None) -> str:
    name = getattr(gpx, "name", None)
    if name:
        return name
    if gpx_path:
        return os.path.splitext(os.path.basename(gpx_path))[0].replace("_", " ")
    return "Rit"


def intro_stats(gpx, extra_stats=None):
    mt = gpx.moving_time_s
    avg = gpx.total_distance_km / (mt / 3600) if mt > 0 else 0.0
    out = [
        ("AFSTAND", f"{gpx.total_distance_km:.1f} km"),
        ("TIJD", f"{int(mt // 60)}:{int(mt % 60):02d}"),
        ("GEM. SNELHEID", f"{avg:.1f} km/u"),
        ("HOOGTEMETERS", f"+{gpx.elevation_gain_m:.0f} m"),
    ]
    if extra_stats and extra_stats.get("avg_power"):
        out.append(("VERMOGEN", f"{int(extra_stats['avg_power'])} W"))
    return out


def _ease(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def render_intro_frame(t_norm, coords, title, date_str, stats, size, cfg):
    """A transparent RGBA intro overlay frame at animation progress t_norm in [0,1]."""
    W, H = size
    k = H / 1080.0
    def s(v): return max(1, int(round(v * k)))

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    scrim = int(255 * getattr(cfg, "intro_scrim_opacity", 0.55))
    d.rectangle([0, 0, W, H], fill=(0, 0, 0, scrim))

    title_a = _ease(t_norm / 0.10)
    route_p = _ease((t_norm - 0.05) / 0.45)
    stats_a = _ease((t_norm - 0.34) / 0.14)

    d.rectangle([0, 0, int(W * 0.34), s(8)], fill=_RED)

    if coords and len(coords) >= 2:
        mbw, mbh = int(W * 0.60), int(H * 0.50)
        mox, moy = (W - mbw) // 2, int(H * 0.25)
        bounds = compute_bounds(coords)
        pts = [(mox + px, moy + py)
               for px, py in (project(la, lo, bounds, mbw, mbh, s(30)) for la, lo in coords)]
        n = max(2, int(len(pts) * route_p))
        seg = pts[:n]
        if len(seg) >= 2:
            d.line(seg, fill=(232, 65, 44, 90), width=s(16), joint="curve")
            d.line(seg, fill=_RED, width=s(6), joint="curve")
            sx, sy = pts[0]
            d.ellipse([sx - s(9), sy - s(9), sx + s(9), sy + s(9)], fill=_WHITE)
            lx, ly = seg[-1]
            d.ellipse([lx - s(11), ly - s(11), lx + s(11), ly + s(11)], fill=_WHITE, outline=_RED, width=s(4))

    fa = lambda a: int(255 * a)
    d.rectangle([s(80), s(96), s(170), s(104)], fill=(232, 65, 44, fa(title_a)))
    d.text((s(80), s(112)), title.upper(), font=_font(cfg, s(60)), fill=(255, 255, 255, fa(title_a)))
    d.text((s(82), s(190)), date_str, font=_font(cfg, s(30)), fill=(190, 194, 204, fa(title_a)))

    if stats_a > 0 and stats:
        vy = H - s(150) + int((1 - stats_a) * s(40))
        cw = W // len(stats)
        vf, lf = _font(cfg, s(52)), _font(cfg, s(24))
        for i, (label, value) in enumerate(stats):
            cx = cw * i + cw // 2
            vb = d.textbbox((0, 0), value, font=vf)
            d.text((cx - (vb[2] - vb[0]) / 2, vy), value, font=vf, fill=(255, 255, 255, fa(stats_a)))
            lb = d.textbbox((0, 0), label, font=lf)
            d.text((cx - (lb[2] - lb[0]) / 2, vy + s(66)), label, font=lf, fill=(232, 65, 44, fa(stats_a)))

    return img
