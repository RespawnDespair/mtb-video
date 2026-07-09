(function () {
  'use strict';

  // ---------- state ----------
  const state = {
    videos: [],          // [{path,name,duration,width,height}]
    activity: null,       // {id,name,date}
    gpx: null,             // path string when using a GPX file instead of Strava
    offset: 0,
    playhead: null,
    timeline: { duration: 0, speed: [], segments: [], clips: [] },
    coords: [],            // Array<[lat, lon]> for the current source ([] when none)
    coordsKey: '',
    selectedSegs: new Set(),
    music: '',             // selected music folder path ('' = geen muziek)
    mode: 'segments',
    outputHeight: 'source',
    outputDir: 'video_output',
    correlation: null,
    renderRunning: false,
  };

  // ---------- dom refs ----------
  const el = (id) => document.getElementById(id);
  const filelistEl = el('filelist'), videodirEl = el('videodir');
  const actsEl = el('acts'), noticeEl = el('notice'), noticetextEl = el('noticetext');
  const offEl = el('off'), offvEl = el('offv');
  const chartEl = el('chart'), segsEl = el('segs'), sparkEl = el('spark'), laneEl = el('lane'), ticksEl = el('ticks');
  const timelineEl = el('timeline');
  const phEl = el('ph'), phtimeEl = el('phtime'), phheadEl = el('phhead');
  const scrubEl = el('scrub'), povimgEl = el('povimg'), povtcEl = el('povtc'), scrubmapEl = el('scrubmap'), scrubrtEl = el('scrubrt'), scrubsubEl = el('scrubsub'), scrubcamEl = el('scrubcam');
  const framesEl = el('frames');
  const corrEl = el('corr'), corrvalEl = el('corrval'), corrlabelEl = el('corrlabel');
  const musicchipsEl = el('musicchips');
  const outdirEl = el('outdir'), outnameEl = el('outname');
  const modechipsEl = el('modechips'), reschipsEl = el('reschips'), segchipsEl = el('segchips');
  const cmdEl = el('cmd'), progressEl = el('progress'), statuslineEl = el('statusline');
  const startBtn = el('startbtn'), stopBtn = el('stopbtn');

  function slug(s) { return (s || 'rit').replace(/\s+/g, '_').replace(/[^A-Za-z0-9_-]/g, ''); }
  function mmss(t) { t = Math.max(0, Math.round(t || 0)); return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`; }
  function fmtDur(s) { s = Math.round(s || 0); const h = Math.floor(s / 3600); const m = Math.floor((s % 3600) / 60); const ss = s % 60; return h ? `${h}:${String(m).padStart(2, '0')}:${String(ss).padStart(2, '0')}` : `${m}:${String(ss).padStart(2, '0')}`; }

  function debounce(fn, ms) {
    let t = null;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  }
  function throttle(fn, ms) {
    let last = 0, pending = null;
    return (...args) => {
      const now = Date.now();
      if (now - last >= ms) { last = now; fn(...args); }
      else { clearTimeout(pending); pending = setTimeout(() => { last = Date.now(); fn(...args); }, ms - (now - last)); }
    };
  }

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

  // ---------- 1. videos ----------
  function renderVideos() {
    videodirEl.textContent = state.videos.length ? `${state.videos.length} bestand(en)` : 'nog geen video’s gekozen';
    filelistEl.innerHTML = state.videos.map(v => `
      <div class="file"><svg class="i ico" viewBox="0 0 24 24"><path d="M4 5h16v14H4zM10 9l5 3-5 3z"/></svg><span class="fn">${v.name}</span><span class="meta">${mmss(v.duration)} · ${v.width}x${v.height}</span></div>
    `).join('');
    refreshTimeline();
  }

  async function loadVideos(body) {
    const r = await fetch('/api/videos', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!r.ok) return;
    state.videos = await r.json();
    renderVideos();
  }

  el('pickfolder').addEventListener('click', async () => {
    const r = await fetch('/api/pick', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode: 'folder' }) });
    const j = await r.json();
    if (j.cancelled || !j.path) return;
    await loadVideos({ dir: j.path });
  });
  el('pickfiles').addEventListener('click', async () => {
    const r = await fetch('/api/pick', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode: 'files' }) });
    const j = await r.json();
    if (j.cancelled || !j.paths) return;
    await loadVideos({ paths: j.paths });
  });

  // ---------- 2. strava activities ----------
  function renderActs(list) {
    actsEl.innerHTML = list.map((a, i) => {
      const date = (a.start_date || '').slice(0, 10);
      const dist = (a.distance_km != null) ? `${a.distance_km} km` : '–';
      const mt = a.moving_time_s != null ? fmtDur(a.moving_time_s) : '–';
      const el0 = a.elevation_gain_m != null ? `+${a.elevation_gain_m} m` : '';
      return `<div class="act" role="checkbox" aria-checked="${i === 0 ? 'true' : 'false'}" tabindex="0"
                data-id="${a.id}" data-name="${(a.name || '').replace(/"/g, '&quot;')}" data-date="${date}">
        <span class="dot"></span>
        <div><div class="name">${a.name || 'Activiteit'}</div><div class="sub">${date}${a.type ? ' · ' + a.type : ''}</div></div>
        <div class="stats"><b>${dist}</b><span>${mt}${el0 ? ' · ' + el0 : ''}</span></div>
      </div>`;
    }).join('');
    document.querySelectorAll('#acts .act').forEach(row => {
      const pick = () => {
        document.querySelectorAll('#acts .act').forEach(r => r.setAttribute('aria-checked', 'false'));
        row.setAttribute('aria-checked', 'true');
        state.gpx = null;
        state.activity = { id: row.dataset.id, name: row.dataset.name, date: row.dataset.date };
        onActivityChanged();
      };
      row.addEventListener('click', pick);
      row.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } });
    });
    if (list.length) {
      state.gpx = null;
      state.activity = { id: String(list[0].id), name: list[0].name, date: (list[0].start_date || '').slice(0, 10) };
    }
  }

  async function loadActivities() {
    try {
      const r = await fetch('/api/activities');
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        noticeEl.style.display = '';
        noticetextEl.textContent = (j.detail || 'Strava niet beschikbaar') + ' — draai strava_auth.py om te koppelen, of kies handmatig een GPX-bestand.';
        actsEl.innerHTML = '';
        return;
      }
      noticeEl.style.display = 'none';
      const list = await r.json();
      renderActs(list);
      onActivityChanged();
    } catch (e) {
      noticeEl.style.display = '';
      noticetextEl.textContent = 'Strava niet beschikbaar — kies handmatig een GPX-bestand.';
    }
  }
  el('refreshacts').addEventListener('click', loadActivities);
  el('pickgpx').addEventListener('click', async () => {
    const r = await fetch('/api/pick', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode: 'gpx' }) });
    const j = await r.json();
    if (j.cancelled || !j.path) return;
    state.gpx = j.path;
    state.activity = null;
    document.querySelectorAll('#acts .act').forEach(r2 => r2.setAttribute('aria-checked', 'false'));
    onActivityChanged();
  });

  // ---------- 3. alignment / timeline ----------
  function drawSpark(speed) {
    const W = 1000, H = 96;
    if (!speed.length) { sparkEl.innerHTML = ''; return; }
    const max = Math.max(1, ...speed);
    const pts = speed.map((v, i) => [i / (speed.length - 1 || 1) * W, H - (v / max) * (H - 10) - 4]);
    let d = `M0 ${H} L${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`;
    for (const [x, y] of pts) d += ` L${x.toFixed(1)} ${y.toFixed(1)}`;
    const area = d + ` L${W} ${H} Z`;
    const end = pts[pts.length - 1];
    sparkEl.innerHTML =
      `<path d="${area}" fill="var(--track-soft)"/>` +
      `<path d="${d.replace(/^M0 96 /, 'M')}" fill="none" stroke="var(--track)" stroke-width="2" vector-effect="non-scaling-stroke"/>` +
      `<circle cx="${end[0].toFixed(1)}" cy="${end[1].toFixed(1)}" r="3.5" fill="var(--track)"/>`;
  }

  function drawSegs(segments, duration) {
    if (!segments.length || !duration) { segsEl.innerHTML = ''; segsEl.style.height = '0px'; return; }
    // Pack overlapping/nested segments into stacked lanes: sort by start, put each in the
    // first lane whose last segment ended before this one starts, else open a new lane.
    const ROW = 20, GAP = 4;
    const laneEnds = [];               // ride-time end per lane
    const placed = [...segments].sort((a, b) => a.start - b.start).map(s => {
      let lane = laneEnds.findIndex(end => s.start >= end);
      if (lane === -1) { lane = laneEnds.length; laneEnds.push(0); }
      laneEnds[lane] = s.end;
      return { s, lane };
    });
    segsEl.style.height = (laneEnds.length * (ROW + GAP) - GAP) + 'px';
    segsEl.innerHTML = placed.map(({ s, lane }) => {
      const left = 100 * s.start / duration;
      const width = Math.max(0.6, 100 * (s.end - s.start) / duration);
      return `<div class="seg" title="${s.n} · ${s.name}" style="left:${left.toFixed(2)}%;` +
             `width:${width.toFixed(2)}%;top:${lane * (ROW + GAP)}px;height:${ROW}px">` +
             `<span>${s.n} · ${s.name}</span></div>`;
    }).join('');
  }

  function drawClips(clips, duration) {
    laneEl.innerHTML = clips.map((c, i) => {
      const left = duration ? 100 * c.start / duration : 0;
      const width = duration ? Math.max(3, 100 * (c.end - c.start) / duration) : 0;
      return `<div class="clip" data-i="${i}" style="left:${left.toFixed(2)}%;width:${width.toFixed(2)}%" title="${c.file}"><span class="grip">⠿</span><span class="cn">${c.file}</span></div>`;
    }).join('');
  }

  function drawTicks(duration) {
    const n = 5;
    ticksEl.innerHTML = Array.from({ length: n }, (_, i) => `<span>${mmss(duration * i / (n - 1))}</span>`).join('');
  }

  // segment numbers currently covered by at least one clip (depends on the offset)
  function coveredNs() {
    const { segments = [], clips = [] } = state.timeline || {};
    const set = new Set();
    segments.forEach(s => {
      if (clips.some(c => c.end > s.start && c.start < s.end)) set.add(s.n);
    });
    return set;
  }

  function segChipsHtml(segments, covered) {
    return segments.map(s => {
      const cov = covered.has(s.n);
      const pressed = cov && state.selectedSegs.has(s.n);
      return `<span class="chip${cov ? '' : ' novideo'}" data-n="${s.n}" ` +
        `aria-pressed="${pressed ? 'true' : 'false'}"${cov ? '' : ' aria-disabled="true"'}>` +
        `${s.n} · ${s.name}${cov ? '' : ' · geen video'}</span>`;
    }).join('');
  }

  function drawSegChips(segments) {
    if (!segments.length) { segchipsEl.innerHTML = ''; segchipsEl.dataset.set = ''; return; }
    const covered = coveredNs();
    // On a genuinely NEW segment set, default-select only the covered segments (uncovered
    // ones start off — they have no footage). A plain drag/offset change keeps the user's
    // choices; coverage is re-applied via the effective pick (buildConfig) each time.
    const sig = JSON.stringify(segments.map(s => s.n).sort((a, b) => a - b));
    if (segchipsEl.dataset.set !== sig) {
      state.selectedSegs = new Set([...covered]);
      segchipsEl.dataset.set = sig;
    }
    segchipsEl.innerHTML = segChipsHtml(segments, covered);
    segchipsEl.querySelectorAll('.chip').forEach(c => {
      if (c.classList.contains('novideo')) return;   // uncovered: not selectable
      c.addEventListener('click', () => {
        const n = +c.dataset.n;
        if (state.selectedSegs.has(n)) state.selectedSegs.delete(n); else state.selectedSegs.add(n);
        c.setAttribute('aria-pressed', state.selectedSegs.has(n) ? 'true' : 'false');
        updateCommandPreview();
      });
    });
  }

  function updateFrames() {
    const { segments = [], parts = [] } = state.timeline || {};
    if (!segments.length) { framesEl.innerHTML = ''; framesEl.dataset.built = ''; return; }
    // One card per render PART (a segment spanning two files → two cards, with the gap
    // between them), plus a 'geen beeld' card for segments no clip covers.
    const coveredN = new Set(parts.map(p => p.n));
    const cards = [
      ...parts.map(p => ({ empty: false, n: p.n, name: p.name, file: p.file, path: p.path,
                           local: p.local, a0: p.a0, a1: p.a1, key: p.a0 })),
      ...segments.filter(s => !coveredN.has(s.n)).map(s => ({ empty: true, n: s.n, name: s.name, key: s.start })),
    // sort by ride time (a0 for a part, segment start for an uncovered one) so the cards
    // are in the SAME chronological order the reel is rendered in.
    ].sort((a, b) => (a.key - b.key) || (a.n - b.n));
    // Rebuild the DOM only when the card STRUCTURE changes (kind/segment/file), not on
    // every offset tick — image sources update in place below.
    const sig = JSON.stringify(cards.map(c => [c.empty ? 'x' : c.file, c.n]));
    if (framesEl.dataset.built !== sig) {
      framesEl.innerHTML = cards.map((c, i) => `
        <div class="frame${c.empty ? ' empty' : ''}" id="fr${i}">
          <div class="thumb"><img class="tcimg" style="width:100%;height:100%;object-fit:cover;display:none"><span class="tc">${c.empty ? 'geen beeld' : '–'}</span></div>
          <div class="segmap"></div>
          <div class="cap"><b>${c.n} · ${c.name}</b><span class="file">${c.empty ? 'geen beeld — clip dekt dit segment niet' : '—'}</span></div>
        </div>`).join('');
      framesEl.dataset.built = sig;
    }
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
    updateCardDots();
  }
  const updateFramesThrottled = throttle(updateFrames, 150);

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

  async function fetchTimeline() {
    if (!state.videos.length && !state.activity && !state.gpx) return;
    const params = new URLSearchParams();
    if (state.activity) params.set('activity_id', state.activity.id);
    if (state.gpx) params.set('gpx', state.gpx);
    params.set('offset', String(state.offset));
    params.set('videos', state.videos.map(v => v.path).join('|'));
    if (!state.activity && !state.gpx) return;
    try {
      await loadTrack();
      const r = await fetch('/api/timeline?' + params.toString());
      if (!r.ok) return;
      const j = await r.json();
      state.timeline = j;
      drawSpark(j.speed || []);
      drawSegs(j.segments || [], j.duration || 0);
      drawClips(j.clips || [], j.duration || 0);
      drawTicks(j.duration || 0);
      if (state.playhead != null) setPlayhead(state.playhead); else phEl.style.display = 'none';
      drawSegChips(j.segments || []);
      updateFramesThrottled();
      updateCommandPreview();
    } catch (e) { /* ignore transient errors while typing/dragging */ }
  }
  const refreshTimeline = debounce(fetchTimeline, 150);

  function setOffset(v, doFetch) {
    v = Math.max(+offEl.min, Math.min(+offEl.max, Math.round(v || 0)));
    state.offset = v;
    offEl.value = v;
    offvEl.value = v;
    updateCommandPreview();
    if (doFetch) fetchTimeline();
  }
  // Dragging the slider only updates the value live (cheap); the timeline/frames refresh
  // on release (change) — no debounce, which feels snappier than mid-drag fetching.
  offEl.addEventListener('input', () => setOffset(+offEl.value, false));
  offEl.addEventListener('change', () => fetchTimeline());
  // Editable value: sync while typing, fetch on commit (blur/Enter).
  offvEl.addEventListener('input', () => setOffset(+offvEl.value, false));
  offvEl.addEventListener('change', () => setOffset(+offvEl.value, true));
  document.querySelectorAll('.nudge [data-d]').forEach(b =>
    b.addEventListener('click', () => setOffset(state.offset + (+b.dataset.d), true)));

  el('autobtn').addEventListener('click', async () => {
    if (!state.videos.length) return;
    const btn = el('autobtn');
    const prevHtml = btn.innerHTML;
    btn.innerHTML = 'bezig…'; btn.disabled = true;
    try {
      const body = { video: state.videos[0].path };
      if (state.activity) body.activity_id = state.activity.id; else if (state.gpx) body.gpx = state.gpx;
      const r = await fetch('/api/auto-align', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if (r.ok) {
        const j = await r.json();
        state.correlation = j.correlation;
        corrEl.style.display = '';
        corrvalEl.textContent = j.correlation.toFixed(2);
        corrlabelEl.textContent = j.correlation >= 0.7 ? 'goed' : (j.correlation >= 0.4 ? 'matig' : 'zwak');
        setOffset(j.offset, true);
      }
    } finally {
      btn.innerHTML = prevHtml; btn.disabled = false;
    }
  });

  // ---------- playhead (scrub the timeline) ----------
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

  function onActivityChanged() {
    outnameEl.placeholder = state.activity ? `${slug(state.activity.name)}_${state.activity.date}.mp4` : 'output.mp4';
    state.selectedSegs = new Set();
    refreshTimeline();
    updateCommandPreview();
  }

  // ---------- 4. music ----------
  function renderMusicChips(data) {
    const chips = (data.subfolders || []).map(s => `<span class="chip" aria-pressed="${state.music === s.path ? 'true' : 'false'}" data-path="${s.path}">${s.name} <span style="opacity:.7">· ${s.count}</span></span>`);
    chips.push(`<span class="chip" aria-pressed="${state.music === '' ? 'true' : 'false'}" data-path="">geen muziek</span>`);
    musicchipsEl.innerHTML = chips.join('');
    musicchipsEl.querySelectorAll('.chip').forEach(c => c.addEventListener('click', () => {
      musicchipsEl.querySelectorAll('.chip').forEach(x => x.setAttribute('aria-pressed', 'false'));
      c.setAttribute('aria-pressed', 'true');
      state.music = c.dataset.path;
      updateCommandPreview();
    }));
  }

  async function loadMusic(dir) {
    try {
      const r = await fetch('/api/music?dir=' + encodeURIComponent(dir));
      if (!r.ok) return;
      renderMusicChips(await r.json());
    } catch (e) { /* no music dir yet */ }
  }
  el('pickmusic').addEventListener('click', async () => {
    const r = await fetch('/api/pick', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode: 'folder' }) });
    const j = await r.json();
    if (j.cancelled || !j.path) return;
    await loadMusic(j.path);
  });
  loadMusic('music');

  // ---------- 5. output & mode/resolution chips ----------
  outdirEl.textContent = state.outputDir + '/';
  el('pickoutdir').addEventListener('click', async () => {
    const r = await fetch('/api/pick', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode: 'folder' }) });
    const j = await r.json();
    if (j.cancelled || !j.path) return;
    state.outputDir = j.path;
    outdirEl.textContent = j.path + '/';
    updateCommandPreview();
  });
  outnameEl.addEventListener('input', updateCommandPreview);

  function wireSingleSelectChips(container, onPick) {
    container.querySelectorAll('.chip').forEach(c => c.addEventListener('click', () => {
      container.querySelectorAll('.chip').forEach(x => x.setAttribute('aria-pressed', 'false'));
      c.setAttribute('aria-pressed', 'true');
      onPick(c.dataset.value);
      updateCommandPreview();
    }));
  }
  wireSingleSelectChips(modechipsEl, (v) => { state.mode = v; });
  wireSingleSelectChips(reschipsEl, (v) => { state.outputHeight = v; });

  // ---------- command preview + render ----------
  function buildConfig() {
    const cfg = {
      videos: state.videos.map(v => v.path),
      mode: state.mode,
      output_height: state.outputHeight,
      output_dir: state.outputDir,
    };
    if (state.activity) cfg.strava_activity_id = state.activity.id;
    else if (state.gpx) cfg.gpx = state.gpx;
    if (state.offset) cfg.sync_offset = state.offset;
    if (state.mode === 'segments') {
      // effective pick = selected AND actually covered by footage
      const covered = coveredNs();
      const pick = [...state.selectedSegs].filter(n => covered.has(n)).sort((a, b) => a - b);
      if (pick.length) cfg.pick = pick.join(',');
    }
    if (state.music) cfg.music = state.music;
    const name = outnameEl.value.trim() || outnameEl.placeholder;
    if (name) cfg.output = name;
    return cfg;
  }

  function updateCommandPreview() {
    const argv = ['python', 'main.py'];
    const cfg = buildConfig();
    if (cfg.videos && cfg.videos.length) argv.push('--video', ...cfg.videos);
    if (cfg.gpx) argv.push('--gpx', cfg.gpx);
    if (cfg.strava_activity_id) argv.push('--strava', '--strava-activity-id', cfg.strava_activity_id);
    if (cfg.sync_offset != null) argv.push('--sync-offset', String(cfg.sync_offset));
    if (cfg.mode) argv.push('--mode', cfg.mode);
    if (cfg.pick) argv.push('--pick', cfg.pick);
    if (cfg.music) argv.push('--music', cfg.music);
    if (cfg.output_height) argv.push('--output-height', cfg.output_height);
    if (cfg.output_dir) argv.push('--output-dir', cfg.output_dir);
    if (cfg.output) argv.push('--output', cfg.output);
    cmdEl.innerHTML = '<b>' + argv[0] + ' ' + argv[1] + '</b> ' +
      argv.slice(2).map(a => String(a).startsWith('--') ? `<span class="flag">${a}</span>` : a).join(' ');
  }

  function appendProgress(text) {
    progressEl.style.display = '';
    progressEl.textContent += text;
    progressEl.scrollTop = progressEl.scrollHeight;
  }

  async function startRender() {
    if (state.renderRunning) return;
    const cfg = buildConfig();
    progressEl.textContent = '';
    progressEl.style.display = '';
    state.renderRunning = true;
    startBtn.disabled = true;
    stopBtn.style.display = '';
    statuslineEl.textContent = 'render loopt…';
    try {
      const resp = await fetch('/api/render', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cfg) });
      if (!resp.ok) {
        const j = await resp.json().catch(() => ({}));
        appendProgress((j.detail || 'fout bij starten van render') + '\n');
        return;
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let full = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        full += chunk;
        appendProgress(chunk);
      }
      const m = full.match(/\[exit (-?\d+)\]/);
      statuslineEl.textContent = m ? `render klaar — exit ${m[1]}` : 'render klaar';
    } catch (e) {
      appendProgress('\n[fout: ' + e + ']\n');
      statuslineEl.textContent = 'render mislukt';
    } finally {
      state.renderRunning = false;
      startBtn.disabled = false;
      stopBtn.style.display = 'none';
    }
  }

  startBtn.addEventListener('click', startRender);
  stopBtn.addEventListener('click', async () => {
    try { await fetch('/api/render/stop', { method: 'POST' }); } catch (e) { /* ignore */ }
  });

  // ---------- init ----------
  state.offset = +offEl.value;          // keep state in sync with the slider's initial value
  offvEl.value = state.offset;
  renderVideos();
  loadActivities();
  updateCommandPreview();
})();
