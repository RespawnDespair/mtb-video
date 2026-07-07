from __future__ import annotations

import functools
import math

from PIL import Image, ImageDraw, ImageFont

from minimap import compute_bounds, project

_RED = (232, 65, 44, 255)
_DARK = (18, 18, 22, 180)
_WHITE = (255, 255, 255, 255)
_GREY = (200, 200, 200, 255)


@functools.lru_cache(maxsize=None)
def _load_font(path, size, bold):
    if bold:
        # try a Bold sibling next to the configured font; fall back to the base font
        bold_path = path.replace(".ttf", " Bold.ttf")
        try:
            return ImageFont.truetype(bold_path, size)
        except OSError:
            pass
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _font(cfg, size, bold=True):
    return _load_font(cfg.overlay_font_path, size, bold)


def render_hud_frame(sample, segment_name, seg_coords, size, cfg, date_str):
    """Return a transparent RGBA HUD frame for one telemetry sample."""
    W, H = size
    # scale UI to 1080p baseline so it looks right at any resolution
    k = H / 1080.0
    def s(v): return int(round(v * k))

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # ---- top-left: segment name tag + HR ----
    name_font = _font(cfg, s(34))
    name = segment_name.upper()
    tb = d.textbbox((0, 0), name, font=name_font)
    d.rectangle([s(40), s(40), s(40) + (tb[2] - tb[0]) + s(28), s(40) + (tb[3] - tb[1]) + s(16)], fill=_RED)
    d.text((s(54), s(48) - tb[1]), name, font=name_font, fill=_WHITE)
    if sample.hr_bpm is not None:
        hr_font = _font(cfg, s(40)); unit_font = _font(cfg, s(22))
        d.rounded_rectangle([s(40), s(100), s(300), s(158)], radius=s(10), fill=_DARK)
        hr_txt = f"{int(sample.hr_bpm)}"
        d.text((s(56), s(110)), hr_txt, font=hr_font, fill=_WHITE)
        d.text((s(56) + d.textlength(hr_txt, font=hr_font) + s(12), s(122)),
               "BPM", font=unit_font, fill=_GREY)
    if sample.power_w is not None:
        pf = _font(cfg, s(40)); puf = _font(cfg, s(22))
        d.rounded_rectangle([s(40), s(168), s(300), s(226)], radius=s(10), fill=_DARK)
        pw = f"{int(round(sample.power_w))}"
        d.text((s(56), s(178)), pw, font=pf, fill=_WHITE)
        d.text((s(56) + d.textlength(pw, font=pf) + s(12), s(190)), "W", font=puf, fill=_GREY)

    # ---- top-right: ELEVATION (left) then SLOPE (right) ----
    def stat(x, label, value):
        lf = _font(cfg, s(22)); vf = _font(cfg, s(40))
        d.rectangle([x, s(40), x + s(150), s(74)], fill=_RED)
        d.text((x + s(14), s(46)), label, font=lf, fill=_WHITE)
        d.rounded_rectangle([x, s(74), x + s(150), s(140)], radius=s(8), fill=_DARK)
        tbv = d.textbbox((0, 0), value, font=vf)
        d.text((x + (s(150) - (tbv[2] - tbv[0])) / 2, s(84)), value, font=vf, fill=_WHITE)

    slope_x = W - s(40) - s(150)
    elev_x = slope_x - s(170)
    stat(elev_x, "ELEVATION", f"{sample.elevation_m:.0f} m")
    stat(slope_x, "SLOPE", f"{sample.slope_pct:+.0f}%")

    # ---- bottom-right: speedometer ----
    cx, cy, r = W - s(170), H - s(170), s(120)
    maxv = getattr(cfg, "speedo_max_kmh", 45.0)
    a0, a1 = 140, 400
    tickf = _font(cfg, s(22)); numf = _font(cfg, s(72))
    step = max(1, int(maxv // 9))
    v = 0
    while v <= maxv:
        ang = math.radians(a0 + (a1 - a0) * (v / maxv))
        x1 = cx + (r - s(4)) * math.cos(ang); y1 = cy + (r - s(4)) * math.sin(ang)
        x2 = cx + (r - s(20)) * math.cos(ang); y2 = cy + (r - s(20)) * math.sin(ang)
        d.line([x1, y1, x2, y2], fill=_WHITE, width=s(3))
        v += step
    frac = max(0.0, min(1.0, sample.speed_kmh / maxv))
    d.arc([cx - r, cy - r, cx + r, cy + r], a0, a0 + (a1 - a0) * frac, fill=_RED, width=s(10))
    sv = f"{sample.speed_kmh:.0f}"
    tbv = d.textbbox((0, 0), sv, font=numf)
    d.text((cx - (tbv[2] - tbv[0]) / 2, cy - s(50)), sv, font=numf, fill=_WHITE)
    d.text((cx - s(28), cy + s(28)), "KM/U", font=tickf, fill=_GREY)

    # ---- bottom-left: minimap + distance + date ----
    mw, mh, pad = s(360), s(300), s(36)   # ~150% of the original 240x200
    mx, my = s(40), H - s(40) - mh
    d.rounded_rectangle([mx, my, mx + mw, my + mh], radius=s(10), fill=_DARK)
    if seg_coords and len(seg_coords) >= 2:
        bounds = compute_bounds(seg_coords)
        poly = [(mx + px, my + py) for px, py in
                (project(la, lo, bounds, mw, mh, pad) for la, lo in seg_coords)]
        d.line(poly, fill=_WHITE, width=s(4), joint="curve")
        dpx, dpy = project(sample.lat, sample.lon, bounds, mw, mh, pad)
        dx, dy = mx + dpx, my + dpy
        d.ellipse([dx - s(8), dy - s(8), dx + s(8), dy + s(8)], fill=_RED, outline=_WHITE, width=s(2))
    df = _font(cfg, s(24))
    d.text((mx, my + mh + s(8)), f"{sample.seg_distance_km:.2f} km   {date_str}", font=df, fill=_WHITE)

    return img
