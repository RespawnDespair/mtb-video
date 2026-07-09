# Timeline scrub-preview + live map dot — Design

**Date:** 2026-07-09
**Status:** Approved for planning (mockup approved: artifact 9448e997)
**Context:** Aligning clips to the ride is still trial-and-error on the offset. Today the
alignment UI (section 03) shows only a per-segment **start-frame** and a static segment
minimap. The user aligns on distinctive terrain (right-angle corners, climbs) and wants
to (1) **scrub the timeline** to see the video frame at any ride-moment, shown in a panel
between the timeline and the segment cards, and (2) see the segment **minimap with a dot**
that moves with the scrub position, so a corner in the frame can be matched to the dot on
the track. This extends the GUI's section 03 (the "Uitlijnen op de track" card). The v1
GUI spec explicitly deferred "video scrubbing"; this is that follow-up.

## Decisions (from brainstorming)

- **One playhead over the whole timeline stack** (speed chart + segment lanes + clip lane).
  Click places it, drag scrubs it; a vertical line with a top handle and a ride-time label.
  **Keyboard:** when the handle has focus, `←/→` move ±1 s, `Shift+←/→` ±10 s
  (`role="slider"`, `aria-valuemin/max/now`).
- **Preview panel between the timeline and the segment cards.** Left: the video frame at
  the playhead. Right: a minimap with the moving dot. Plus a readout bar (ride-moment ·
  `video @ mm:ss` · a plain guidance line — see note below).
- **The dot = the GPS position at the playhead's ride-moment.** It is fixed; the frame next
  to it shifts with the offset. Alignment = get the corner/climb in the frame to coincide
  with the dot. **One dot only** (no second "video-position" ghost dot).
- **Preview minimap crops to the active segment** (the segment the playhead is inside);
  between segments it shows the whole ride.
- **Outside clip coverage:** the frame shows "geen beeld"; the minimap + dot keep working
  (you can still read the ride-moment).
- **Minimaps move from server-PNG to client-SVG.** A new `GET /api/track` returns the ride
  coordinates as JSON; the client projects them (a JS port of `minimap.project`) and draws
  an SVG track + a cheaply-movable dot. `/api/segment-map` is removed. This is required for
  a smooth per-scrub dot without a server round-trip per tick.

> **Note — no correctness verdict.** The mockup faked a known "true offset" to show an
> "uitgelijnd ✓" badge. The real GUI does not know the true offset, so the readout is
> **informational** ("lijn de bocht in beeld uit op de stip"), not a pass/fail judgement.
> The frame fetch (only network cost while scrubbing) is throttled ~150 ms, like the
> existing `updateFramesThrottled`.

## Architecture / files

- `gui/server.py` — add `GET /api/track`; remove `GET /api/segment-map`.
- `gui/static/app.js` — playhead controller, preview panel, client-side projection +
  SVG minimap rendering (preview map + the per-segment cards), track fetch/cache.
- `gui/static/app.css` — playhead (`.ph`), scrub preview panel (`.scrub`), segment-card
  minimap-as-SVG + dot styling; keep the existing `.frame`/`.frames` layout.
- `gui/static/index.html` — insert the preview-panel container between `.timeline` and the
  `frames-head`/`frames`.
- No change to `/api/frame`, `/api/timeline`, or the render path.

## Backend

- `GET /api/track?activity_id=|gpx=` → `{ coords: [[lat, lon], ...] }`, indexed by
  **activity-second** (same list as `GpxData.coords`, i.e. index `i` = second `i` of the
  ride — the same indexing `/api/segment-map` used for `a0/a1`). Built via the existing
  `_gpx_cached(activity_id, gpx)`; cached per source so a scrub does no Strava/GPX work.
  Coordinates are returned in full (per-second); a ride is at most a few thousand points,
  which is a small JSON payload fetched **once** per source.
- Remove `GET /api/segment-map` and the PIL/`ImageDraw` rendering it used. Projection now
  lives in the client; `minimap.project` (Python) stays for the HUD renderer.

## Frontend

**Projection (pure, ported from `minimap.py`).** A small JS module: `computeBounds(coords)`,
`project(lat, lon, bounds, w, h, pad)` — equirectangular, longitude scaled by
`cos(mean_lat)`, north up, aspect preserved — matching `minimap.py` exactly. Given a
coord-slice it returns pixel points for a `w×h` box.

**Track store.** On timeline load (when an activity/gpx is selected), fetch `/api/track`
once → `state.coords`. Any crop `[a0, a1]` is a slice `coords[a0..a1]`; the dot at
ride-moment `t` is `coords[round(t)]`.

**Playhead controller.** `state.playhead` = ride-seconds (null until first placed). The
`.ph` element is positioned `left = playhead / duration`. `pointerdown/move/up` on
`.timeline` with `setPointerCapture`: `t = (clientX − rect.left) / rect.width × duration`,
clamped to `[0, duration]`. Handle is focusable; arrow keys nudge as above.

**Preview panel.** On playhead change:
- **Frame:** find a clip covering `t` (`c.start ≤ t < c.end`); local video time
  `= t − c.base`; set the frame `<img>` to `/api/frame?video={c.path}&t={local}&w=…`
  (throttled). No covering clip → "geen beeld".
- **Minimap:** active segment = the one with `s.start ≤ t ≤ s.end`; crop = that segment,
  else the whole ride. Rebuild the SVG only when the crop changes; move the dot each tick
  (set `cx/cy` from the projected slice). One dot (accent), plus start/end markers.
- **Readout:** ride-moment `mmss(t)`, `video @ mmss(local)` (or "geen dekking"), and the
  informational guidance line.

**Segment cards.** Migrate `.segmap` from `<img>` (PNG) to an inline SVG built client-side
from `coords[a0..a1]` (the render part's window, already in the `/api/timeline` `parts`
payload). Add a dot shown when the playhead falls within `[a0, a1]`; highlight the card
(`.active`) while it does. The start-frame thumbnail and the rest of the card are unchanged.

## Data flow

```
browser (app.js)
  GET /api/track (once per activity/gpx) ──→ GpxData.coords  → state.coords
  scrub playhead (pointer / keys) ─────────→ ride-moment t
    ├─ project coords[crop]           (client SVG minimap + dot; no network)
    └─ GET /api/frame (t−clip.base)   (throttled ~150 ms; cached per (video,t,w))
```

## Error handling / edge cases

- **No track source** (no activity, no gpx) → no `/api/track`, no playhead/preview (nothing
  to align against); the timeline still shows clips.
- **Playhead outside all clip coverage** → frame "geen beeld"; minimap + dot stay.
- **Between segments** → preview minimap shows the whole ride with the dot.
- **`/api/frame` 404** (uncovered / missing file) → the `<img>` `onerror` shows "geen beeld"
  (existing behaviour).
- **Degenerate/short track** (single point) → projection centres the point (as `minimap.py`
  already handles); a 1-point crop hides the dot rather than erroring.
- **Dot index vs. crop:** `round(t)` clamped to the slice bounds; out-of-range → dot hidden.

## Testing

- **Backend (pytest, FastAPI `TestClient`):** `/api/track` with a mocked `GpxData` →
  `{coords: [...]}` of the expected length and `[lat, lon]` shape; cached (second call does
  not rebuild). Assert `/api/segment-map` is gone (404).
- **Projection parity:** a small pytest that runs the reference `minimap.project` on a few
  coords and asserts the JS port produces the same pixels — implement as a tiny Node/JS
  check or by mirroring the numeric cases in a comment + a Python golden the JS is written
  against. (If no JS test runner exists, keep the JS `project` a line-for-line port and
  cover the Python `minimap.project` with the golden; note the parity is manual.)
- **Manual (verify skill / `python gui.py`):** pick clips + a Strava activity; scrub the
  playhead → frame updates and the dot moves; enter a segment → minimap crops to it and the
  card highlights with its dot; scrub past coverage → "geen beeld", dot stays; keyboard
  arrows nudge the playhead ±1/±10 s; check light + dark theme.

## Out of scope

- A second "video-position" dot on the map (explicitly declined).
- Any pass/fail alignment verdict (the true offset is unknown).
- Dragging individual clips / per-clip offsets (offset stays shared, per the v1 GUI spec).
- Playing video (frames only), audio scrubbing, or a moving elevation profile.
