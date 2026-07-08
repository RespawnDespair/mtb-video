  // --- speed/motion sparkline (deterministic pseudo-profile) ---
  (function () {
    const N = 120, W = 1000, H = 96;
    let s = 7; const rnd = () => (s = (s * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
    const pts = [];
    for (let i = 0; i < N; i++) {
      const x = i / (N - 1);
      // baseline rolling speed + segment surges near 0.15, 0.41, 0.5
      let v = 0.42 + 0.18 * Math.sin(x * 7) + 0.12 * Math.sin(x * 19 + 1);
      const surge = (c, w, a) => a * Math.exp(-((x - c) * (x - c)) / (2 * w * w));
      v += surge(0.15, 0.05, 0.34) + surge(0.41, 0.02, 0.42) + surge(0.5, 0.02, 0.30);
      v += (rnd() - 0.5) * 0.12;
      v = Math.max(0.05, Math.min(1, v));
      pts.push([x * W, H - v * (H - 10) - 4]);
    }
    let d = `M0 ${H} L${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`;
    for (const [x, y] of pts) d += ` L${x.toFixed(1)} ${y.toFixed(1)}`;
    const area = d + ` L${W} ${H} Z`;
    const end = pts[pts.length - 1];
    document.getElementById('spark').innerHTML =
      `<path d="${area}" fill="var(--track-soft)"/>` +
      `<path d="${d.replace(/^M0 96 /, 'M')}" fill="none" stroke="var(--track)" stroke-width="2" vector-effect="non-scaling-stroke"/>` +
      `<circle cx="${end[0].toFixed(1)}" cy="${end[1].toFixed(1)}" r="3.5" fill="var(--track)"/>`;
  })();

  // --- alignment: offset shifts clip bars; command preview stays in sync ---
  const RIDE = 4324;           // ride length (s) ~ 1:12:04
  const durA = 151, durB = 1300, gapAB = 500;  // clip durations + recording gap (s)
  const lane = document.getElementById('lane');
  const clipA = document.getElementById('clipA'), clipB = document.getElementById('clipB');
  const offEl = document.getElementById('off'), offv = document.getElementById('offv');
  const cmd = document.getElementById('cmd');
  let activity = { id: '19189561939', name: 'Stellendam Goeree Vol Gas', date: '2026-07-05' };

  function place(el, startS, durS) {
    el.style.left = (100 * startS / RIDE) + '%';
    el.style.width = Math.max(3, 100 * durS / RIDE) + '%';
  }
  function slug(s) { return s.replace(/\s+/g, '_').replace(/[^A-Za-z0-9_-]/g, ''); }
  function mmss(t) { t = Math.max(0, Math.round(t)); return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`; }

  // segments (ride seconds) + which clip covers them -> start-frame preview
  const SEGMENTS = [
    { name: 'Hobbelen langs de Damweg', start: 203, end: 1358, grad: 'linear-gradient(150deg,#4a5d3a,#8a7b4e 60%,#c9a15b)' },
    { name: 'MTB Goeree Roggebos', start: 1673, end: 1919, grad: 'linear-gradient(150deg,#2f4632,#3e6b47 55%,#7fa06a)' },
    { name: 'MTB lusje kort Havenhoofd', start: 2063, end: 2262, grad: 'linear-gradient(150deg,#3a4a5d,#5e7186 60%,#a9b8c4)' },
  ];
  function clipsAt(off) {
    return [
      { file: '003.mp4', base: off, start: off, end: off + durA },
      { file: '004.mp4', base: off + gapAB, start: off + gapAB, end: off + gapAB + durB },
    ];
  }
  const framesEl = document.getElementById('frames');
  framesEl.innerHTML = SEGMENTS.map((s, i) => `
    <div class="frame" id="fr${i}">
      <div class="thumb" style="background:${s.grad}"><span class="tc">–</span></div>
      <div class="cap"><b>${s.name}</b><span class="file">—</span></div>
    </div>`).join('');

  function updateFrames(off) {
    const clips = clipsAt(off);
    SEGMENTS.forEach((s, i) => {
      const el = document.getElementById('fr' + i);
      const tc = el.querySelector('.tc'), file = el.querySelector('.file');
      // earliest clip overlapping the segment; covered start = max(seg.start, clip.start)
      const cov = clips.filter(c => c.end > s.start && c.start < s.end).sort((a, b) => a.base - b.base)[0];
      if (!cov) { el.classList.add('empty'); tc.style.display = 'none'; file.textContent = 'geen beeld — clip dekt dit segment niet'; return; }
      el.classList.remove('empty'); tc.style.display = '';
      const local = Math.max(s.start, cov.start) - cov.base;
      tc.textContent = mmss(local);
      file.textContent = `${cov.file} @ ${mmss(local)}`;
    });
  }

  function render() {
    const off = +offEl.value;
    offv.textContent = off;
    place(clipA, off, durA);
    place(clipB, off + gapAB, durB);
    const name = (document.getElementById('outname').value.trim()) ||
      `${slug(activity.name)}_${activity.date}.mp4`;
    cmd.innerHTML =
      `<b>python main.py</b> \\\n` +
      `  <span class="flag">--strava</span> <span class="flag">--strava-activity-id</span> ${activity.id} \\\n` +
      `  <span class="flag">--sync-offset</span> ${off} <span class="flag">--mode</span> segments <span class="flag">--pick</span> 1,3,4 \\\n` +
      `  <span class="flag">--music</span> music/rock <span class="flag">--output-height</span> source \\\n` +
      `  <span class="flag">--output-dir</span> video_output <span class="flag">--output</span> ${name}`;
    updateFrames(off);
  }
  offEl.addEventListener('input', render);
  document.getElementById('outname').addEventListener('input', render);

  // drag clip A to nudge the shared offset
  let drag = null;
  clipA.addEventListener('pointerdown', e => { drag = { x: e.clientX, off: +offEl.value }; clipA.setPointerCapture(e.pointerId); });
  clipA.addEventListener('pointermove', e => {
    if (!drag) return;
    const w = lane.clientWidth;
    const d = (e.clientX - drag.x) / w * RIDE;
    offEl.value = Math.max(0, Math.min(1200, Math.round(drag.off + d)));
    render();
  });
  clipA.addEventListener('pointerup', () => drag = null);

  // activity selection
  document.querySelectorAll('#acts .act').forEach(row => {
    const pick = () => {
      document.querySelectorAll('#acts .act').forEach(r => r.setAttribute('aria-checked', 'false'));
      row.setAttribute('aria-checked', 'true');
      activity = { id: row.dataset.id, name: row.dataset.name, date: row.dataset.date };
      render();
    };
    row.addEventListener('click', pick);
    row.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } });
  });

  // chip toggles (visual only in the mock)
  document.querySelectorAll('.chip').forEach(c => c.addEventListener('click', () => {
    const grp = c.parentElement;
    if (grp.querySelectorAll('[aria-pressed]').length && !grp.closest('.stack')?.querySelector('.label')?.textContent.includes('Segmenten')) {
      // single-select groups (mode, resolution, music)
      if (!c.textContent.includes('·') || grp.children.length <= 3) {
        [...grp.children].forEach(x => x.setAttribute('aria-pressed', 'false'));
      }
    }
    c.setAttribute('aria-pressed', c.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
  }));

  document.getElementById('autobtn').addEventListener('click', () => { offEl.value = 577; render(); });
  render();
