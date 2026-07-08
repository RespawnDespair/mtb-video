(function () {
  'use strict';

  // ---------- state ----------
  const state = {
    videos: [],          // [{path,name,duration,width,height}]
    activity: null,       // {id,name,date}
    gpx: null,             // path string when using a GPX file instead of Strava
    offset: 0,
    timeline: { duration: 0, speed: [], segments: [], clips: [] },
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
    segsEl.innerHTML = segments.map(s => {
      const left = duration ? 100 * s.start / duration : 0;
      const width = duration ? Math.max(0.3, 100 * (s.end - s.start) / duration) : 0;
      return `<div class="seg" style="left:${left.toFixed(2)}%;width:${width.toFixed(2)}%"><span>${s.name}</span></div>`;
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

  function segChipsHtml(segments) {
    return segments.map(s => `<span class="chip" aria-pressed="${state.selectedSegs.has(s.n) ? 'true' : 'false'}" data-n="${s.n}">${s.n} · ${s.name}</span>`).join('');
  }

  function drawSegChips(segments) {
    if (!segments.length) { segchipsEl.innerHTML = ''; return; }
    // default: select all on first render of a new segment set
    const known = new Set(segments.map(s => s.n));
    if ([...state.selectedSegs].some(n => !known.has(n)) || state.selectedSegs.size === 0) {
      state.selectedSegs = new Set(segments.map(s => s.n));
    }
    segchipsEl.innerHTML = segChipsHtml(segments);
    segchipsEl.querySelectorAll('.chip').forEach(c => c.addEventListener('click', () => {
      const n = +c.dataset.n;
      if (state.selectedSegs.has(n)) state.selectedSegs.delete(n); else state.selectedSegs.add(n);
      c.setAttribute('aria-pressed', state.selectedSegs.has(n) ? 'true' : 'false');
      updateCommandPreview();
    }));
  }

  function updateFrames() {
    const { segments, clips, duration } = state.timeline;
    if (!segments.length) { framesEl.innerHTML = ''; return; }
    if (!framesEl.dataset.built || framesEl.dataset.built !== JSON.stringify(segments.map(s => s.n))) {
      framesEl.innerHTML = segments.map((s, i) => `
        <div class="frame" id="fr${i}">
          <div class="thumb"><img class="tcimg" style="width:100%;height:100%;object-fit:cover;display:none"><span class="tc">–</span></div>
          <div class="cap"><b>${s.name}</b><span class="file">—</span></div>
        </div>`).join('');
      framesEl.dataset.built = JSON.stringify(segments.map(s => s.n));
    }
    segments.forEach((s, i) => {
      const wrap = el('fr' + i);
      if (!wrap) return;
      const tc = wrap.querySelector('.tc'), img = wrap.querySelector('.tcimg'), file = wrap.querySelector('.file');
      const cov = clips.filter(c => c.end > s.start && c.start < s.end).sort((a, b) => a.base - b.base)[0];
      if (!cov) {
        wrap.classList.add('empty'); tc.style.display = ''; tc.textContent = 'geen beeld';
        img.style.display = 'none'; file.textContent = 'geen beeld — clip dekt dit segment niet';
        return;
      }
      wrap.classList.remove('empty');
      const local = Math.max(s.start, cov.start) - cov.base;
      file.textContent = `${cov.file} @ ${mmss(local)}`;
      const src = `/api/frame?video=${encodeURIComponent(cov.path)}&t=${local}&w=320`;
      if (img.dataset.src !== src) {
        img.dataset.src = src;
        img.onload = () => { img.style.display = ''; tc.style.display = 'none'; };
        img.onerror = () => { img.style.display = 'none'; tc.style.display = ''; tc.textContent = 'geen beeld'; };
        img.src = src;
      }
    });
  }
  const updateFramesThrottled = throttle(updateFrames, 150);

  async function fetchTimeline() {
    if (!state.videos.length && !state.activity && !state.gpx) return;
    const params = new URLSearchParams();
    if (state.activity) params.set('activity_id', state.activity.id);
    if (state.gpx) params.set('gpx', state.gpx);
    params.set('offset', String(state.offset));
    params.set('videos', state.videos.map(v => v.path).join('|'));
    if (!state.activity && !state.gpx) return;
    try {
      const r = await fetch('/api/timeline?' + params.toString());
      if (!r.ok) return;
      const j = await r.json();
      state.timeline = j;
      drawSpark(j.speed || []);
      drawSegs(j.segments || [], j.duration || 0);
      drawClips(j.clips || [], j.duration || 0);
      drawTicks(j.duration || 0);
      drawSegChips(j.segments || []);
      updateFramesThrottled();
      updateCommandPreview();
    } catch (e) { /* ignore transient errors while typing/dragging */ }
  }
  const refreshTimeline = debounce(fetchTimeline, 150);

  offEl.addEventListener('input', () => {
    state.offset = +offEl.value;
    offvEl.textContent = state.offset;
    refreshTimeline();
    updateCommandPreview();
  });

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
        state.offset = Math.round(j.offset);
        offEl.value = Math.max(+offEl.min, Math.min(+offEl.max, state.offset));
        offvEl.textContent = offEl.value;
        state.correlation = j.correlation;
        corrEl.style.display = '';
        corrvalEl.textContent = j.correlation.toFixed(2);
        corrlabelEl.textContent = j.correlation >= 0.7 ? 'goed' : (j.correlation >= 0.4 ? 'matig' : 'zwak');
        refreshTimeline();
        updateCommandPreview();
      }
    } finally {
      btn.innerHTML = prevHtml; btn.disabled = false;
    }
  });

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
    if (state.mode === 'segments' && state.selectedSegs.size) {
      cfg.pick = [...state.selectedSegs].sort((a, b) => a - b).join(',');
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
  offvEl.textContent = state.offset;
  renderVideos();
  loadActivities();
  updateCommandPreview();
})();
