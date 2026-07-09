from __future__ import annotations

import functools
import math

from PIL import Image, ImageDraw, ImageFont

from minimap import compute_bounds, project

_RED = (232, 65, 44, 255)
_RED_SOFT = (232, 65, 44, 235)
_GLASS = (22, 24, 28, 168)          # frosted card fill (translucent dark)
_EDGE = (255, 255, 255, 30)         # subtle top highlight on cards
_WHITE = (255, 255, 255, 255)
_GREY = (206, 210, 216, 255)
_FAINT = (206, 210, 216, 120)


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


def _text_center(d, box, text, font, fill):
    x0, y0, x1, y1 = box
    tb = d.textbbox((0, 0), text, font=font)
    tx = x0 + (x1 - x0 - (tb[2] - tb[0])) / 2 - tb[0]
    ty = y0 + (y1 - y0 - (tb[3] - tb[1])) / 2 - tb[1]
    d.text((tx, ty), text, font=font, fill=fill)


# ---------------------------------------------------------------- cards -----
def _glass(d, box, radius, corners=None):
    """A frosted rounded card: translucent dark fill + a faint top edge."""
    d.rounded_rectangle(box, radius=radius, fill=_GLASS, corners=corners)
    # hairline highlight along the top for a bit of depth
    d.rounded_rectangle(box, radius=radius, outline=_EDGE, width=1, corners=corners)


def _header_body(d, x, y, w, header_h, body_h, radius, label, font, sw):
    """A card with a red rounded header (label centred) over a glass body.
    Returns the body box (x0, y0, x1, y1)."""
    top = (True, True, False, False)
    bottom = (False, False, True, True)
    d.rounded_rectangle([x, y, x + w, y + header_h], radius=radius, fill=_RED, corners=top)
    _text_center(d, (x, y, x + w, y + header_h), label, font, _WHITE)
    body = [x, y + header_h, x + w, y + header_h + body_h]
    d.rounded_rectangle(body, radius=radius, fill=_GLASS, corners=bottom)
    d.rounded_rectangle(body, radius=radius, outline=_EDGE, width=1, corners=bottom)
    return body


# ---------------------------------------------------------------- icons -----
# Simple vector glyphs drawn with ImageDraw (no external assets). Each takes a
# centre (cx, cy) and a half-size r.
def _icon_heart(d, cx, cy, r, color):
    lobe = r * 0.62
    d.ellipse([cx - r, cy - lobe, cx, cy + lobe * 0.25], fill=color)
    d.ellipse([cx, cy - lobe, cx + r, cy + lobe * 0.25], fill=color)
    d.polygon([(cx - r * 0.98, cy - lobe * 0.15), (cx + r * 0.98, cy - lobe * 0.15),
               (cx, cy + r)], fill=color)


def _icon_mountain(d, cx, cy, r, color):
    d.polygon([(cx - r, cy + r * 0.72), (cx - r * 0.28, cy - r * 0.45),
               (cx + r * 0.08, cy + r * 0.15), (cx + r * 0.5, cy - r * 0.8),
               (cx + r, cy + r * 0.72)], fill=color)


def _icon_slope(d, cx, cy, r, color):
    # rising ramp = grade
    d.polygon([(cx - r, cy + r * 0.7), (cx + r, cy + r * 0.7), (cx + r, cy - r * 0.7)],
              fill=color)


def _icon_pin(d, cx, cy, r, color):
    d.ellipse([cx - r * 0.72, cy - r, cx + r * 0.72, cy + r * 0.44], fill=color)
    d.polygon([(cx - r * 0.5, cy + r * 0.12), (cx + r * 0.5, cy + r * 0.12),
               (cx, cy + r)], fill=color)
    d.ellipse([cx - r * 0.26, cy - r * 0.55, cx + r * 0.26, cy - r * 0.03], fill=_RED)


def _icon_calendar(d, cx, cy, r, color, w):
    box = [cx - r, cy - r * 0.72, cx + r, cy + r]
    d.rounded_rectangle(box, radius=r * 0.24, outline=color, width=w)
    d.line([cx - r, cy - r * 0.22, cx + r, cy - r * 0.22], fill=color, width=w)
    d.line([cx - r * 0.5, cy - r, cx - r * 0.5, cy - r * 0.55], fill=color, width=w)
    d.line([cx + r * 0.5, cy - r, cx + r * 0.5, cy - r * 0.55], fill=color, width=w)


# ------------------------------------------------------------- sparkline ----
def _hr_range(vals):
    """Dynamic, nicely-rounded [lo, hi] for a heart-rate window."""
    lo_raw, hi_raw = min(vals), max(vals)
    pad = max(4.0, (hi_raw - lo_raw) * 0.15)
    lo = math.floor((lo_raw - pad) / 5.0) * 5
    hi = math.ceil((hi_raw + pad) / 5.0) * 5
    if hi - lo < 15:
        hi = lo + 15
    return lo, hi


def render_hud_frame(sample, segment_name, seg_coords, size, cfg, date_str, hr_window=None):
    """Return a transparent RGBA HUD frame for one telemetry sample.

    hr_window: optional list of per-second HR values across the segment up to the
    current moment; drives the sparkline (dynamic vertical range, high at the top).
    """
    W, H = size
    k = H / 1080.0                     # scale UI to a 1080p baseline
    def s(v): return int(round(v * k))

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    M = s(40)                          # screen margin

    # ---- top-left: segment name pill ----
    name = segment_name.upper()
    name_font = _font(cfg, s(32))
    tb = d.textbbox((0, 0), name, font=name_font)
    pill_w = (tb[2] - tb[0]) + s(36)
    pill_h = s(52)
    d.rounded_rectangle([M, M, M + pill_w, M + pill_h], radius=pill_h // 2, fill=_RED)
    _text_center(d, (M, M, M + pill_w, M + pill_h), name, name_font, _WHITE)

    # ---- top-left: heart-rate card (number + heart + sparkline) ----
    if sample.hr_bpm is not None:
        hy = M + pill_h + s(12)
        hw, hh, rad = s(430), s(120), s(18)
        _glass(d, [M, hy, M + hw, hy + hh], rad)
        num_font = _font(cfg, s(60)); unit_font = _font(cfg, s(24))
        hr_txt = f"{int(sample.hr_bpm)}"
        d.text((M + s(20), hy + s(14)), hr_txt, font=num_font, fill=_WHITE)
        num_w = d.textlength(hr_txt, font=num_font)
        d.text((M + s(20) + num_w + s(12), hy + s(40)), "BPM", font=unit_font, fill=_GREY)
        _icon_heart(d, M + s(30), hy + hh - s(28), s(15), _RED)

        # sparkline (right portion), dynamic range, high at top
        vals = [v for v in (hr_window or []) if v is not None]
        sp_x0, sp_x1 = M + s(150), M + hw - s(70)
        sp_y0, sp_y1 = hy + s(20), hy + hh - s(20)
        if len(vals) >= 2:
            lo, hi = _hr_range(vals)
            n = len(vals)
            def px(i): return sp_x0 + (sp_x1 - sp_x0) * (i / (n - 1))
            def py(v): return sp_y1 - (sp_y1 - sp_y0) * ((v - lo) / (hi - lo))
            # faint baseline grid
            d.line([sp_x0, sp_y0, sp_x1, sp_y0], fill=_FAINT, width=1)
            d.line([sp_x0, sp_y1, sp_x1, sp_y1], fill=_FAINT, width=1)
            pts = [(px(i), py(v)) for i, v in enumerate(vals)]
            d.line(pts, fill=_RED_SOFT, width=s(3), joint="curve")
            ex, ey = pts[-1]
            d.ellipse([ex - s(4), ey - s(4), ex + s(4), ey + s(4)], fill=_WHITE)
            ax_font = _font(cfg, s(20))
            d.text((sp_x1 + s(8), sp_y0 - s(2)), f"{hi}", font=ax_font, fill=_GREY)
            d.text((sp_x1 + s(8), sp_y1 - s(18)), f"{lo}", font=ax_font, fill=_GREY)

    # ---- top-left: power card (only when present) ----
    if sample.power_w is not None:
        py0 = M + pill_h + s(12) + (s(120) if sample.hr_bpm is not None else 0) + s(10)
        pw_txt = f"{int(round(sample.power_w))}"
        _glass(d, [M, py0, M + s(210), py0 + s(64)], s(14))
        pf = _font(cfg, s(40)); puf = _font(cfg, s(22))
        d.text((M + s(18), py0 + s(12)), pw_txt, font=pf, fill=_WHITE)
        d.text((M + s(18) + d.textlength(pw_txt, font=pf) + s(10), py0 + s(24)),
               "W", font=puf, fill=_GREY)

    # ---- top-right: ELEVATION then SLOPE ----
    lbl_font = _font(cfg, s(22)); val_font = _font(cfg, s(40))
    cw, chh, cbh, crad = s(160), s(38), s(74), s(16)
    slope_x = W - M - cw
    elev_x = slope_x - cw - s(18)

    def stat(x, label, value, icon):
        body = _header_body(d, x, M, cw, chh, cbh, crad, label, lbl_font, s(2))
        by0 = body[1]
        icon(d, x + s(30), by0 + cbh / 2, s(15))
        tb2 = d.textbbox((0, 0), value, font=val_font)
        d.text((x + s(56), by0 + (cbh - (tb2[3] - tb2[1])) / 2 - tb2[1]),
               value, font=val_font, fill=_WHITE)

    stat(elev_x, "ELEVATION", f"{sample.elevation_m:.0f} m",
         lambda dd, cx, cy, r: _icon_mountain(dd, cx, cy, r, _WHITE))
    stat(slope_x, "SLOPE", f"{sample.slope_pct:+.0f}%",
         lambda dd, cx, cy, r: _icon_slope(dd, cx, cy, r, _WHITE))

    # ---- bottom-right: speedometer ----
    r = s(115)
    cx, cy = W - M - r, H - M - r
    maxv = getattr(cfg, "speedo_max_kmh", 45.0)
    a0, a1 = 135, 405                  # 270° sweep, gap at the bottom
    # frosted disc
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(22, 24, 28, 120), outline=_EDGE, width=1)
    # fine ticks (majors longer)
    n_ticks = 27
    for i in range(n_ticks + 1):
        ang = math.radians(a0 + (a1 - a0) * (i / n_ticks))
        major = (i % 3 == 0)
        r1 = r - s(6)
        r2 = r - (s(20) if major else s(13))
        col = _WHITE if major else _GREY
        d.line([cx + r1 * math.cos(ang), cy + r1 * math.sin(ang),
                cx + r2 * math.cos(ang), cy + r2 * math.sin(ang)],
               fill=col, width=s(3) if major else s(2))
    frac = max(0.0, min(1.0, sample.speed_kmh / maxv))
    d.arc([cx - r + s(3), cy - r + s(3), cx + r - s(3), cy + r - s(3)],
          a0, a0 + (a1 - a0) * frac, fill=_RED, width=s(9))
    num_font = _font(cfg, s(70)); u_font = _font(cfg, s(22))
    sv = f"{sample.speed_kmh:.0f}"
    tbv = d.textbbox((0, 0), sv, font=num_font)
    d.text((cx - (tbv[2] - tbv[0]) / 2 - tbv[0], cy - s(52)), sv, font=num_font, fill=_WHITE)
    _text_center(d, (cx - r, cy + s(22), cx + r, cy + s(52)), "KM/U", u_font, _GREY)

    # ---- bottom-left: minimap card + distance/date row ----
    mw, mh, pad, rad = s(300), s(210), s(30), s(16)
    row_h = s(40)
    my = H - M - row_h - s(12) - mh
    mx = M
    _glass(d, [mx, my, mx + mw, my + mh], rad)
    if seg_coords and len(seg_coords) >= 2:
        bounds = compute_bounds(seg_coords)
        poly = [(mx + px2, my + py2) for px2, py2 in
                (project(la, lo, bounds, mw, mh, pad) for la, lo in seg_coords)]
        d.line(poly, fill=_WHITE, width=s(4), joint="curve")
        dpx, dpy = project(sample.lat, sample.lon, bounds, mw, mh, pad)
        dx, dy = mx + dpx, my + dpy
        d.ellipse([dx - s(8), dy - s(8), dx + s(8), dy + s(8)], fill=_RED, outline=_WHITE, width=s(2))

    # distance | date row (icons + divider), directly below the minimap card
    ry = my + mh + s(12)
    rf = _font(cfg, s(26))
    ic = s(13)
    _icon_pin(d, mx + s(14), ry + row_h / 2, ic, _WHITE)
    dist_txt = f"{sample.seg_distance_km:.2f} km"
    d.text((mx + s(34), ry + (row_h - s(30)) / 2), dist_txt, font=rf, fill=_WHITE)
    div_x = mx + s(34) + d.textlength(dist_txt, font=rf) + s(20)
    d.line([div_x, ry + s(6), div_x, ry + row_h - s(6)], fill=_FAINT, width=s(2))
    cal_x = div_x + s(20)
    _icon_calendar(d, cal_x + s(2), ry + row_h / 2, ic, _WHITE, s(2))
    d.text((cal_x + s(24), ry + (row_h - s(30)) / 2), date_str, font=rf, fill=_WHITE)

    return img
