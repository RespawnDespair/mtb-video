# Timeline Scrub-Preview + Live Map Dot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a draggable/keyboard playhead over the alignment timeline that shows the video frame at any ride-moment plus a live GPS dot on a minimap, so clips can be aligned on corners/climbs instead of trial-and-error.

**Architecture:** A new `GET /api/track` returns the ride's per-second coordinates as JSON. The frontend fetches them once per source, projects them client-side (a line-for-line JS port of `minimap.project`), and draws SVG minimaps whose dot moves cheaply while scrubbing. A playhead on the timeline drives a preview panel (frame via the existing `/api/frame`, throttled) inserted between the timeline and the segment cards. The old server-rendered `/api/segment-map` PNG is removed.

**Tech Stack:** Python 3, FastAPI (backend), vanilla JS + SVG + Canvas (frontend), pytest with `fastapi.testclient.TestClient`, ffmpeg (frame extraction, already in use).

## Global Constraints

- Backend binds `127.0.0.1` only (unchanged); reuse existing modules, don't duplicate.
- Track source is a Strava activity (auto-GPX) **or** a chosen `--gpx` file — both go through `_gpx_cached(activity_id, gpx)`.
- Coordinates are indexed by **activity-second**: index `i` = second `i` of the ride, the same `GpxData.coords` list `/api/segment-map` indexed with `a0/a1`.
- Frame fetches while scrubbing are throttled ~150 ms (match the existing `updateFramesThrottled`).
- The JS `project()` must be a line-for-line port of `minimap.project` (equirectangular, longitude scaled by `cos(mean_lat)`, north up, aspect preserved) — no behavioural divergence.
- No pass/fail alignment verdict in the real UI (the true offset is unknown); the readout is informational only.
- Frontend has no JS test runner; frontend tasks are verified by running `python gui.py` and observing the described behaviour. Backend is covered by pytest.

---

### Task 1: Backend — `/api/track` endpoint, remove `/api/segment-map`

**Files:**
- Modify: `gui/server.py` (add `track`, delete `segment_map` at lines 131-156)
- Test: `test_sync.py` (replace `test_api_segment_map_returns_png` at lines 2033-2047)

**Interfaces:**
- Consumes: `_gpx_cached(activity_id, gpx)` (existing, `gui/server.py:122`) → `GpxData` with `.coords` = `list[(lat, lon)]` indexed per second.
- Produces: `GET /api/track?activity_id=&gpx=` → JSON `{"coords": [[lat, lon], ...]}` (per-second, full ride). Consumed by the frontend in Task 2.

- [ ] **Step 1: Write the failing test**

Replace `test_api_segment_map_returns_png` (lines 2033-2047) with:

```python
def test_api_track_returns_coords(monkeypatch):
    from datetime import datetime, timezone
    from fastapi.testclient import TestClient
    import gui.server as srv
    from highlight_detector import GpxData
    coords = [(52.0 + i * 1e-4, 4.0 + i * 1.5e-4) for i in range(400)]
    g = GpxData(start_time=datetime(2026, 7, 5, tzinfo=timezone.utc), speeds_kmh=[10.0] * 400,
                coords=coords, elevations_m=[5] * 400, hr_bpm=[100] * 400,
                cum_distance_m=list(range(400)), total_distance_km=5.0, elevation_gain_m=20.0,
                moving_time_s=400.0, first_coord=coords[0], name="Rit")
    calls = {"n": 0}

    def fake(i):
        calls["n"] += 1
        return g
    monkeypatch.setattr("strava_gpx.gpx_from_strava", fake)
    srv._gpx_cache.clear()
    c = TestClient(srv.app)
    r = c.get("/api/track", params={"activity_id": "88"})
    assert r.status_code == 200
    j = r.json()
    assert len(j["coords"]) == 400
    assert j["coords"][0] == [52.0, 4.0]
    # second call hits the gpx cache (no rebuild)
    c.get("/api/track", params={"activity_id": "88"})
    assert calls["n"] == 1
    # segment-map endpoint is gone
    assert c.get("/api/segment-map", params={"activity_id": "88", "a0": 0, "a1": 9}).status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest test_sync.py::test_api_track_returns_coords -v`
Expected: FAIL — `/api/track` returns 404 (route not defined).

- [ ] **Step 3: Implement the endpoint and delete the old one**

In `gui/server.py`, delete the entire `segment_map` route (lines 131-156, the `@app.get("/api/segment-map")` function). Remove the now-unused `io` / `PIL` / `minimap` imports **inside that function** (they are local imports, so just deleting the function removes them). Add in its place:

```python
@app.get("/api/track")
def track(activity_id: str = None, gpx: str = None):
    """Ride coordinates as JSON, indexed by activity-second (coords[i] = second i).
    The client projects these (a JS port of minimap.project) and draws the minimap
    SVGs + the scrub dot, so scrubbing needs no per-tick server round-trip."""
    g = _gpx_cached(activity_id, gpx)
    return {"coords": [[la, lo] for la, lo in (g.coords or [])]}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest test_sync.py::test_api_track_returns_coords -v`
Expected: PASS.

- [ ] **Step 5: Run the full GUI test group to catch regressions**

Run: `.venv/bin/python -m pytest test_sync.py -k "api_" -v`
Expected: PASS (no test still references `/api/segment-map`).

- [ ] **Step 6: Commit**

```bash
git add gui/server.py test_sync.py
git commit -m "feat(gui): add /api/track (coords JSON), drop /api/segment-map

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Frontend — client-side projection + track store + segment cards as SVG

**Files:**
- Modify: `gui/static/app.js` (add helpers near the top; add track fetch in `fetchTimeline`; rewrite the map half of `updateFrames`, lines 235-283)
- Modify: `gui/static/app.css` (segment-card `.segmap` now holds an SVG + a dot)

**Interfaces:**
- Consumes: `GET /api/track` from Task 1 → `{coords: [[lat, lon], ...]}`.
- Produces (used by Tasks 3-4):
  - `state.coords` — `Array<[lat, lon]>` for the current source (`[]` when none).
  - `computeBounds(coords) -> {mnla, mxla, mnlo, mxlo, mean}`
  - `project(lat, lon, bounds, w, h, pad) -> [x, y]`
  - `mapSVG(coords, w, h) -> string` — SVG markup for a track slice, with a hidden `.dot` + `.doth` (halo) circle.
  - `setDot(svgEl, coords, w, h, idx)` — move/show the dot to coord index `idx` (hide if out of range).

- [ ] **Step 1: Add the projection helpers (line-for-line port of `minimap.project`)**

In `gui/static/app.js`, just after the `throttle` helper (after line 49), add:

```javascript
  // ---------- minimap projection (port of minimap.project) ----------
  function computeBounds(coords) {
    let mnla = Infinity, mxla = -Infinity, mnlo = Infinity, mxlo = -Infinity, sum = 0;
    for (const [la, lo] of coords) {
      if (la < mnla) mnla = la; if (la > mxla) mxla = la;
      if (lo < mnlo) mnlo = lo; if (lo > mxlo) mxlo = lo; sum += la;
    }
    return { mnla, mxla, mnlo, mxlo, mean: sum / coords.length };
  }
  function project(la, lo, b, w, h, pad) {
    const cos = Math.cos(b.mean * Math.PI / 180);
    const spanx = (b.mxlo - b.mnlo) * cos, spany = (b.mxla - b.mnla);
    const iw = w - 2 * pad, ih = h - 2 * pad;
    if (spanx < 1e-12 && spany < 1e-12) return [w / 2, h / 2];
    const scale = Math.min(spanx > 1e-12 ? iw / spanx : Infinity,
                           spany > 1e-12 ? ih / spany : Infinity);
    const dw = spanx * scale, dh = spany * scale;
    const ox = pad + (iw - dw) / 2, oy = pad + (ih - dh) / 2;
    return [ox + ((lo - b.mnlo) * cos) * scale, oy + (b.mxla - la) * scale];
  }
  function projectAll(coords, w, h, pad) {
    if (coords.length < 2) return [];
    const b = computeBounds(coords);
    return coords.map(([la, lo]) => project(la, lo, b, w, h, pad));
  }
  // Track colours match minimap.py: track green line, orange start, green end.
  function mapSVG(coords, w, h) {
    const pad = 14;
    const P = projectAll(coords, w, h, pad);
    if (P.length < 2) return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid meet"></svg>`;
    const poly = P.map(p => p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' ');
    const s = P[0], e = P[P.length - 1];
    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid meet">` +
      `<polyline points="${poly}" fill="none" stroke="var(--track)" stroke-width="2.4" ` +
      `stroke-linejoin="round" stroke-linecap="round" opacity=".85"/>` +
      `<circle cx="${e[0].toFixed(1)}" cy="${e[1].toFixed(1)}" r="3.5" fill="var(--track)"/>` +
      `<circle cx="${s[0].toFixed(1)}" cy="${s[1].toFixed(1)}" r="4" fill="var(--accent)"/>` +
      `<circle class="doth" r="6.5" fill="none" stroke="var(--accent)" stroke-width="2" opacity=".5" style="display:none">` +
      `<animate attributeName="r" values="6.5;12;6.5" dur="1.6s" repeatCount="indefinite"/></circle>` +
      `<circle class="dot" r="6.5" fill="var(--accent)" stroke="#fff" stroke-width="2" style="display:none"/></svg>`;
  }
  function setDot(svgEl, coords, w, h, idx) {
    const dot = svgEl.querySelector('.dot'), halo = svgEl.querySelector('.doth');
    if (!dot) return;
    const i = Math.round(idx);
    if (i < 0 || i >= coords.length || coords.length < 2) {
      dot.style.display = 'none'; if (halo) halo.style.display = 'none'; return;
    }
    const [x, y] = project(coords[i][0], coords[i][1], computeBounds(coords), w, h, 14);
    dot.setAttribute('cx', x.toFixed(1)); dot.setAttribute('cy', y.toFixed(1)); dot.style.display = '';
    if (halo) { halo.setAttribute('cx', x.toFixed(1)); halo.setAttribute('cy', y.toFixed(1)); halo.style.display = ''; }
  }
```

- [ ] **Step 2: Add a projection-parity pytest (golden values from `minimap.project`)**

In `test_sync.py`, add (this guards the Python side the JS is ported from; the values were produced by `minimap.project` on a 240×150 box with pad 14):

```python
def test_minimap_project_golden():
    from minimap import compute_bounds, project
    coords = [(52.0, 4.0), (52.01, 4.02), (52.005, 4.03)]
    b = compute_bounds(coords)
    got = [[round(v, 3) for v in project(la, lo, b, 240, 150, 14)] for la, lo in coords]
    assert got == [[14.0, 132.397], [155.333, 17.603], [226.0, 75.0]]
```

Run: `.venv/bin/python -m pytest test_sync.py::test_minimap_project_golden -v`
Expected: PASS. (The JS `project` in Step 1 uses the identical formula, so the same inputs yield the same pixels — verified visually in Step 6.)

- [ ] **Step 3: Fetch the track once per source into `state.coords`**

In `app.js`, add `coords: []` and `coordsKey: ''` to the `state` object (after `timeline:` at line 10). Then add a fetch function after `fetchTimeline`'s definition (after line 308) — but define it above `fetchTimeline` so it can be called inside. Place this **before** `fetchTimeline` (before line 286):

```javascript
  async function loadTrack() {
    const key = state.activity ? 'a:' + state.activity.id : (state.gpx ? 'g:' + state.gpx : '');
    if (!key || key === state.coordsKey) return;
    const params = new URLSearchParams();
    if (state.activity) params.set('activity_id', state.activity.id);
    if (state.gpx) params.set('gpx', state.gpx);
    try {
      const r = await fetch('/api/track?' + params.toString());
      if (!r.ok) return;
      state.coords = (await r.json()).coords || [];
      state.coordsKey = key;
    } catch (e) { /* leave coords empty; minimaps just won't render */ }
  }
```

Then, inside `fetchTimeline` (line 294, right after the `try {`), await the track before drawing:

```javascript
    try {
      await loadTrack();
      const r = await fetch('/api/timeline?' + params.toString());
```

- [ ] **Step 4: Rewrite the map half of `updateFrames` to use client SVG**

In `updateFrames` (lines 235-283): the card DOM template currently has `<div class="segmap"><img class="mapimg" alt="segment-track"></div>` (line 254). Change that to `<div class="segmap"></div>`. Then **delete** the `trackParam` block (lines 260-261) and, in the per-card loop (lines 262-282), replace the `mapimg` / `msrc` block (lines 267, 276-281) so the segmap renders from `state.coords[a0..a1]`. The card loop body becomes:

```javascript
    cards.forEach((c, i) => {
      if (c.empty) return;
      const wrap = el('fr' + i);
      if (!wrap) return;
      const tc = wrap.querySelector('.tc'), img = wrap.querySelector('.tcimg');
      const segmap = wrap.querySelector('.segmap'), file = wrap.querySelector('.file');
      file.textContent = `${c.file} @ ${mmss(c.local)}`;
      const src = `/api/frame?video=${encodeURIComponent(c.path)}&t=${c.local}&w=320`;
      if (img.dataset.src !== src) {
        img.dataset.src = src;
        img.onload = () => { img.style.display = ''; tc.style.display = 'none'; };
        img.onerror = () => { img.style.display = 'none'; tc.style.display = ''; tc.textContent = 'geen beeld'; };
        img.src = src;
      }
      // GPS track of this part's window [a0, a1], rendered client-side (same projection as the HUD).
      const a0 = Math.round(c.a0), a1 = Math.round(c.a1);
      const sub = state.coords.slice(a0, a1 + 1);
      if (segmap.dataset.range !== a0 + '-' + a1) {
        segmap.innerHTML = sub.length >= 2 ? mapSVG(sub, 230, 150) : '';
        segmap.dataset.range = a0 + '-' + a1;
      }
    });
```

(The `.mapimg` reference is gone; the SVG replaces it. The dot on these cards is wired in Task 4.)

- [ ] **Step 5: Update `.segmap` CSS for SVG**

In `gui/static/app.css`, change the `.frame .segmap img` rule (line 236) to target the SVG, and keep the layout. Replace line 236:

```css
  .frame .segmap svg { width:100%; height:100%; display:block; }
```

- [ ] **Step 6: Verify visually**

Run: `.venv/bin/python gui.py`
Then in the browser: pick a folder of clips + a Strava activity (or a GPX). Expected: each segment card shows a **green track** minimap (orange start dot, green end dot) that looks the same as the old PNG — but now inline SVG (inspect: `<svg>` inside `.segmap`, no `<img>`). No console errors.

- [ ] **Step 7: Commit**

```bash
git add gui/static/app.js gui/static/app.css test_sync.py
git commit -m "feat(gui): client-side minimap projection; segment cards render SVG tracks

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Frontend — the scrubbable playhead (pointer + keyboard)

**Files:**
- Modify: `gui/static/index.html` (add the playhead element inside `.timeline`)
- Modify: `gui/static/app.css` (playhead styling)
- Modify: `gui/static/app.js` (playhead state + controller)

**Interfaces:**
- Consumes: `state.timeline.duration`, the `#timeline` element.
- Produces (used by Task 4): `state.playhead` (ride-seconds, `null` until first placed); `setPlayhead(t)` which positions `#ph`, updates the label, and calls `onPlayheadChange()` (a hook Task 4 fills in — define it as an empty function here).

- [ ] **Step 1: Add the playhead element**

In `gui/static/index.html`, inside `<div class="timeline">` (after the `<div class="ticks" id="ticks">…</div>` at line 77), add:

```html
          <div class="ph" id="ph" style="display:none">
            <div class="ttime" id="phtime">0:00</div>
            <div class="line"></div>
            <div class="head" id="phhead" tabindex="0" role="slider"
                 aria-label="playhead ritmoment" aria-valuemin="0" aria-valuenow="0"></div>
          </div>
```

- [ ] **Step 2: Add playhead CSS**

In `gui/static/app.css`, after the `.ticks` rule (line 220) add:

```css
  .timeline { cursor: crosshair; touch-action: none; }
  .ph { position:absolute; top:0; width:0; pointer-events:none; z-index:5; }
  .ph .line { position:absolute; top:0; bottom:26px; left:-1px; width:2px; background:var(--accent);
    box-shadow:0 0 0 1px color-mix(in srgb, var(--surface) 60%, transparent); }
  .ph .head { position:absolute; top:-7px; left:-7px; width:14px; height:14px; border-radius:50%;
    background:var(--accent); border:2px solid var(--surface); box-shadow:0 1px 4px rgba(0,0,0,.35);
    cursor:grab; pointer-events:auto; }
  .ph .head:active { cursor:grabbing; }
  .ph .head:focus-visible { outline:2px solid var(--accent); outline-offset:3px; }
  .ph .ttime { position:absolute; top:-24px; left:0; transform:translateX(-50%);
    font-family:var(--font-mono); font-size:10.5px; font-weight:600; color:var(--accent-ink);
    background:var(--accent); padding:1px 6px; border-radius:5px; white-space:nowrap;
    font-variant-numeric:tabular-nums; }
```

(The `.line` stops 26px above the bottom so it doesn't overlap the ticks row.)

- [ ] **Step 3: Add the playhead controller**

In `gui/static/app.js`, add `playhead: null` to `state` (after `offset: 0,` at line 9). Add DOM refs after line 26: `const phEl = el('ph'), phtimeEl = el('phtime'), phheadEl = el('phhead');`. Then add this block after the offset handlers (after line 326):

```javascript
  // ---------- playhead (scrub the timeline) ----------
  function onPlayheadChange() { /* filled in Task 4 */ }

  function setPlayhead(t) {
    const dur = state.timeline.duration || 0;
    if (!dur) { phEl.style.display = 'none'; state.playhead = null; return; }
    t = Math.max(0, Math.min(dur, Math.round(t)));
    state.playhead = t;
    phEl.style.display = '';
    phEl.style.left = (100 * t / dur) + '%';
    phtimeEl.textContent = mmss(t);
    phheadEl.setAttribute('aria-valuenow', t);
    phheadEl.setAttribute('aria-valuemax', dur);
    onPlayheadChange();
  }

  (function wirePlayhead() {
    let dragging = false;
    const tToClient = (clientX) => {
      const r = timelineEl.getBoundingClientRect();
      return (clientX - r.left) / r.width * (state.timeline.duration || 0);
    };
    timelineEl.addEventListener('pointerdown', (e) => {
      if (!state.timeline.duration) return;
      dragging = true; timelineEl.setPointerCapture(e.pointerId);
      setPlayhead(tToClient(e.clientX));
    });
    timelineEl.addEventListener('pointermove', (e) => { if (dragging) setPlayhead(tToClient(e.clientX)); });
    timelineEl.addEventListener('pointerup', () => { dragging = false; });
    timelineEl.addEventListener('pointercancel', () => { dragging = false; });
    phheadEl.addEventListener('keydown', (e) => {
      if (state.playhead == null) return;
      const step = e.shiftKey ? 10 : 1;
      if (e.key === 'ArrowLeft') { e.preventDefault(); setPlayhead(state.playhead - step); }
      else if (e.key === 'ArrowRight') { e.preventDefault(); setPlayhead(state.playhead + step); }
      else if (e.key === 'Home') { e.preventDefault(); setPlayhead(0); }
      else if (e.key === 'End') { e.preventDefault(); setPlayhead(state.timeline.duration); }
    });
  })();
```

Add the `timelineEl` ref near the other refs (line 25): change that line to also grab the timeline, e.g. add `const timelineEl = el('timeline');`.

- [ ] **Step 4: Keep the playhead valid when the duration changes**

In `fetchTimeline`, after `drawTicks(j.duration || 0);` (line 302), add:

```javascript
      if (state.playhead != null) setPlayhead(state.playhead); else phEl.style.display = 'none';
```

(Re-clamps the playhead to the new duration, or leaves it hidden until first placed.)

- [ ] **Step 5: Verify visually**

Run: `.venv/bin/python gui.py`. Pick clips + activity. Expected:
- Click anywhere on the timeline → an accent playhead line appears with a time label; dragging scrubs it smoothly; releasing keeps it.
- Tab to the round handle, press `←/→` → moves 1 s; `Shift+←/→` → 10 s; `Home`/`End` → jump to ends.
- With no activity/gpx selected, clicking the timeline does nothing (no playhead).

- [ ] **Step 6: Commit**

```bash
git add gui/static/index.html gui/static/app.css gui/static/app.js
git commit -m "feat(gui): scrubbable timeline playhead (pointer + keyboard)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Frontend — the scrub preview panel (frame + cropped minimap + dot + card dots)

**Files:**
- Modify: `gui/static/index.html` (insert the `.scrub` panel between `.timeline` and `.frames-head`)
- Modify: `gui/static/app.css` (`.scrub` panel styles)
- Modify: `gui/static/app.js` (fill in `onPlayheadChange`; move dots on cards)

**Interfaces:**
- Consumes: `state.playhead`, `state.coords`, `state.offset`, `state.timeline` (`duration`, `segments`, `clips`, `parts`), `mapSVG`, `setDot`, `mmss`.
- Produces: the completed feature (no downstream tasks).

- [ ] **Step 1: Add the preview panel markup**

In `gui/static/index.html`, between the closing `</div>` of `.timeline` (after line 78) and the `<p class="tl-note">` (line 80), insert:

```html
          <div class="scrub" id="scrub" style="display:none">
            <div class="pane"><span class="cam" id="scrubcam">POV</span>
              <img class="povimg" id="povimg" alt="frame op de playhead">
              <span class="tc" id="povtc">–</span></div>
            <div class="map" id="scrubmap"></div>
            <div class="read">
              <div><span class="label" style="text-transform:none;font-size:11px">Ritmoment</span>
                <div class="big" id="scrubrt">–</div></div>
              <div class="sub" id="scrubsub">scrub of klik in de tijdlijn</div>
              <div class="hintline" id="scrubhint">lijn de bocht in beeld uit op de stip</div>
            </div>
          </div>
```

- [ ] **Step 2: Add the `.scrub` CSS**

In `gui/static/app.css`, after the `.tl-note` rules (line 222) add:

```css
  .scrub { display:flex; align-items:stretch; border:1.5px solid var(--accent); border-radius:11px;
    overflow:hidden; background:var(--surface-2); margin-top:14px;
    box-shadow:0 0 0 3px color-mix(in srgb, var(--accent) 12%, transparent); }
  .scrub .pane { position:relative; width:min(46vw,380px); aspect-ratio:16/9; flex:none; background:#111;
    display:flex; align-items:center; justify-content:center; }
  .scrub .pane .povimg { width:100%; height:100%; object-fit:cover; display:none; }
  .scrub .pane.empty::after { content:'geen beeld'; color:var(--text-faint); font-size:12px;
    font-family:var(--font-mono); }
  .scrub .pane .cam { position:absolute; top:7px; left:7px; font-family:var(--font-mono); font-size:10px;
    letter-spacing:.04em; background:rgba(0,0,0,.5); color:#fff; padding:2px 7px; border-radius:5px; }
  .scrub .pane .tc { position:absolute; bottom:7px; right:7px; font-family:var(--font-mono); font-size:11px;
    background:rgba(0,0,0,.6); color:#fff; padding:2px 7px; border-radius:5px; font-variant-numeric:tabular-nums; }
  .scrub .map { width:min(34vw,280px); flex:none; border-left:1px solid var(--border);
    background:color-mix(in srgb, var(--track) 7%, var(--surface)); }
  .scrub .map svg { width:100%; height:100%; display:block; }
  .scrub .read { flex:1; min-width:0; padding:13px 15px; display:flex; flex-direction:column; gap:8px;
    justify-content:center; }
  .scrub .read .big { font-family:var(--font-mono); font-size:22px; font-weight:680;
    font-variant-numeric:tabular-nums; letter-spacing:-.01em; }
  .scrub .read .sub { font-size:12px; color:var(--text-dim); }
  .scrub .read .sub b { color:var(--text); font-weight:600; font-family:var(--font-mono); }
  .scrub .read .hintline { font-size:12px; color:var(--track); }
  @media (max-width:800px){
    .scrub { flex-wrap:wrap; }
    .scrub .pane { width:60%; } .scrub .map { width:40%; }
    .scrub .read { width:100%; border-top:1px solid var(--border); }
  }
  @media (max-width:560px){
    .scrub .pane, .scrub .map { width:100%; }
    .scrub .map { border-left:none; border-top:1px solid var(--border); aspect-ratio:2/1; }
  }
```

- [ ] **Step 3: Implement `onPlayheadChange` (frame + minimap + readout)**

In `gui/static/app.js`, add DOM refs (near line 26): `const scrubEl = el('scrub'), povimgEl = el('povimg'), povtcEl = el('povtc'), scrubmapEl = el('scrubmap'), scrubrtEl = el('scrubrt'), scrubsubEl = el('scrubsub'), scrubcamEl = el('scrubcam');`

Replace the placeholder `function onPlayheadChange() { /* filled in Task 4 */ }` (from Task 3) with:

```javascript
  let _scrubMapRange = '';
  function fetchPovFrame(video, tLocal) {
    const src = `/api/frame?video=${encodeURIComponent(video)}&t=${tLocal}&w=480`;
    if (povimgEl.dataset.src === src) return;
    povimgEl.dataset.src = src;
    povimgEl.onload = () => { povimgEl.style.display = ''; povimgEl.parentElement.classList.remove('empty'); };
    povimgEl.onerror = () => { povimgEl.style.display = 'none'; povimgEl.parentElement.classList.add('empty'); };
    povimgEl.src = src;
  }
  const fetchPovFrameThrottled = throttle(fetchPovFrame, 150);

  function renderScrub() {
    const t = state.playhead;
    const { duration = 0, segments = [], clips = [], parts = [] } = state.timeline || {};
    if (t == null || !duration) { scrubEl.style.display = 'none'; return; }
    scrubEl.style.display = '';
    scrubrtEl.textContent = mmss(t);
    // frame: first clip covering ride-time t; local video time = t - clip.base
    const clip = clips.find(c => t >= c.start && t < c.end);
    const pane = povimgEl.parentElement;
    if (clip) {
      const local = t - clip.start;   // clip.start == base for the shared offset
      fetchPovFrameThrottled(clip.path, local);
      povtcEl.textContent = mmss(local);
      scrubcamEl.textContent = clip.file;
      scrubsubEl.innerHTML = `frame <b>${mmss(local)}</b> uit <b>${clip.file}</b>`;
    } else {
      povimgEl.style.display = 'none'; pane.classList.add('empty');
      povimgEl.dataset.src = ''; povtcEl.textContent = '–';
      scrubsubEl.textContent = 'geen clip dekt dit ritmoment';
    }
    // minimap: crop to the active segment, else whole ride
    const seg = segments.find(s => t >= s.start && t <= s.end);
    const a0 = seg ? Math.round(seg.start) : 0;
    const a1 = seg ? Math.round(seg.end) : (state.coords.length - 1);
    const key = a0 + '-' + a1;
    const sub = state.coords.slice(a0, a1 + 1);
    if (_scrubMapRange !== key) {
      scrubmapEl.innerHTML = sub.length >= 2 ? mapSVG(sub, 280, 200) : '';
      _scrubMapRange = key;
    }
    const svg = scrubmapEl.querySelector('svg');
    if (svg && sub.length >= 2) setDot(svg, sub, 280, 200, t - a0);
    // dots on the segment cards + active highlight
    parts.forEach((p, i) => { /* handled by updateCardDots below */ });
    updateCardDots();
  }
  function onPlayheadChange() { renderScrub(); }
```

- [ ] **Step 4: Move the dot on the segment cards as the playhead crosses them**

Cards are rebuilt by `updateFrames` keyed on structure; the dot moves independently. Add this function next to `renderScrub`:

```javascript
  function updateCardDots() {
    const t = state.playhead;
    const { parts = [] } = state.timeline || {};
    // Cards are ordered by the same sort updateFrames uses; re-derive each card's range from its DOM.
    document.querySelectorAll('#frames .frame').forEach(fr => {
      const segmap = fr.querySelector('.segmap');
      const svg = segmap && segmap.querySelector('svg');
      const range = segmap && segmap.dataset.range;
      if (!svg || !range) return;
      const [a0, a1] = range.split('-').map(Number);
      const sub = state.coords.slice(a0, a1 + 1);
      const inside = t != null && t >= a0 && t <= a1 && sub.length >= 2;
      fr.classList.toggle('active', !!inside);
      if (inside) setDot(svg, sub, 230, 150, t - a0);
      else { const d = svg.querySelector('.dot'), h = svg.querySelector('.doth');
             if (d) d.style.display = 'none'; if (h) h.style.display = 'none'; }
    });
  }
```

Then call `updateCardDots()` at the end of `updateFrames` (after the `cards.forEach(...)` loop, before the function closes, ~line 282) so a freshly rebuilt card immediately gets its dot if the playhead is inside it.

- [ ] **Step 5: Add the `.frame.active` + card-dot CSS**

In `gui/static/app.css`, after the `.frame` rule (line 231) add:

```css
  .frame { transition: border-color .12s ease; }
  .frame.active { border-color:var(--accent);
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 40%, transparent); }
```

- [ ] **Step 6: Verify visually (the whole feature)**

Run: `.venv/bin/python gui.py`. Pick clips + a Strava activity with segments. Expected:
- Scrubbing the playhead shows the preview panel between the timeline and the cards: the frame updates (throttled) and the minimap dot moves.
- Entering a segment → the preview minimap crops to that segment and the matching card below gets the accent border with a moving dot; leaving it → back to the whole-ride map, card dot hidden.
- Scrub past clip coverage → frame shows "geen beeld", the minimap + dot stay.
- Nudging the offset shifts which frame shows at the same ride-moment (align a corner in the frame to the dot).
- Check dark theme (system or `data-theme`): accent line, dot, panel border all legible.

- [ ] **Step 7: Commit**

```bash
git add gui/static/index.html gui/static/app.css gui/static/app.js
git commit -m "feat(gui): scrub preview panel — frame + live map dot for alignment

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review notes

- **Spec coverage:** playhead pointer+keyboard (Task 3); preview panel between timeline and cards (Task 4); one GPS dot, offset shifts frame (Task 4); preview minimap crops to active segment else whole ride (Task 4 Step 3); outside coverage → empty frame, map+dot stay (Task 4 Step 3); minimaps client-SVG via `/api/track`, `/api/segment-map` removed (Tasks 1-2); no correctness verdict — `scrubhint` is informational (Task 4); frame throttled 150 ms (Task 4 Step 3); projection line-for-line port + golden (Task 2). All covered.
- **Type consistency:** `mapSVG(coords, w, h)` and `setDot(svgEl, coords, w, h, idx)` used identically in Tasks 2 and 4; `state.coords`, `state.playhead`, `setPlayhead`, `onPlayheadChange` names consistent across Tasks 3-4; `clip.start`/`clip.path`/`clip.file` and `parts`/`segments` shapes match the existing `/api/timeline` payload (`gui/server.py:200-218`).
- **`clip.start == base`:** the timeline payload sets `start = offset + (own - ref)` which equals `base`, so `local = t - clip.start` is the correct video-local time (matches how `updateFrames` computes `c.local`).
