# Local web GUI (v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local FastAPI web app (`python gui.py`) that reuses the existing pipeline to browse Strava activities, align clips against the ride telemetry (with live per-segment start-frame previews), pick music/output, and run a render as a streamed `main.py` subprocess.

**Architecture:** `gui/server.py` (FastAPI `app`) serves `gui/static/` (the approved mock, wired to the API) and exposes JSON/stream endpoints that call existing modules. `gui/render.py` builds the CLI argv. `gui.py` launches uvicorn on `127.0.0.1` and opens the browser.

**Tech Stack:** Python 3.14 (`.venv`), FastAPI 0.139, uvicorn 0.50.2, httpx 0.28.1 (already installed), FFmpeg, pytest + `fastapi.testclient`.

## Global Constraints

- Bind `127.0.0.1` only. One render at a time (second concurrent → HTTP 409).
- Reuse existing code; do not duplicate pipeline logic. Rendering runs `main.py` as a subprocess (`sys.executable`, `cwd=<repo root>`), streaming stdout to the UI.
- Repo root from `gui/server.py`: `REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`.
- Existing suite stays green. Use `.venv/bin/python`. Real-ffmpeg tests use `@pytest.mark.skipif(shutil.which("ffmpeg") is None, ...)`.
- `git add` only changed files (never `git add -A`; stray media/scratch exist).
- Commits end with a blank line then: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- The approved frontend mock lives at `/private/tmp/claude-501/-Users-Jelle-Tigchelaar-git-mtb-video/a50afca1-eb59-40a3-ad61-11a286923478/scratchpad/rhe_gui_mock.html` — the source for the static files.

## Existing interfaces

- `strava_client`: `is_configured()`, `_load_token()`, `_save_token()`, `_refresh_if_needed(token, now_epoch)`, `_API`, `get_segment_efforts(id)`, `get_activity_streams(id)`; uses `requests`.
- `strava_gpx.gpx_from_strava(id) -> GpxData`; `highlight_detector.load_gpx(path) -> GpxData` (`.speeds_kmh`, `.start_time`, `.name`), `get_video_duration`, `get_video_resolution`, `compute_optical_flow_per_second(path, cfg)`, `estimate_offset_by_motion(flow, gpx, cfg)`.
- `clip_sources.build_clip_sources(paths, gpx, cfg, offset_override=None, use_auto=False) -> [ClipSource(path, base_offset, duration, width, height, ...)]`.
- `segment_detector.parse_efforts(raw)`, `efforts_to_activity_ranges(efforts, gpx_start, cfg) -> [(start,end,name,stats)]`.
- `main.gather_videos_in_dir(dir)`, `generate_output_name(gpx)`; `config.Config`.

---

## Task 1: backend skeleton + Strava activities + static UI + launcher

**Files:** Create `gui/__init__.py`, `gui/server.py`, `gui.py`, `gui/static/index.html`, `gui/static/app.css`, `gui/static/app.js`; Modify `strava_client.py`, `requirements.txt`; Test `test_sync.py`.

- [ ] **Step 1: Pin deps** — append to `requirements.txt`:

```
fastapi==0.139.0
uvicorn==0.50.2
httpx==0.28.1
```

- [ ] **Step 2: Failing tests** — append to `test_sync.py`:

```python
def test_list_activities_normalises(monkeypatch):
    import strava_client
    monkeypatch.setattr(strava_client, "is_configured", lambda: True)
    monkeypatch.setattr(strava_client, "_load_token", lambda: {"access_token": "x", "expires_at": 9e9})
    monkeypatch.setattr(strava_client, "_refresh_if_needed", lambda t, now_epoch: t)
    monkeypatch.setattr(strava_client, "_save_token", lambda t: None)
    class R:
        def json(self): return [{"id": 42, "name": "Rit", "start_date": "2026-07-05T12:00:00Z",
                                 "distance": 21300, "moving_time": 4324, "total_elevation_gain": 186,
                                 "type": "Ride"}]
    monkeypatch.setattr(strava_client.requests, "get", lambda *a, **k: R())
    acts = strava_client.list_activities(5)
    assert acts[0]["id"] == 42 and acts[0]["name"] == "Rit"
    assert acts[0]["distance_km"] == 21.3 and acts[0]["moving_time_s"] == 4324


def test_gui_serves_index_and_activities(monkeypatch):
    from fastapi.testclient import TestClient
    import gui.server as srv
    monkeypatch.setattr("strava_client.list_activities", lambda n=15: [{"id": 1, "name": "A"}])
    c = TestClient(srv.app)
    assert c.get("/").status_code == 200 and "Ride Highlight Editor" in c.get("/").text
    r = c.get("/api/activities")
    assert r.status_code == 200 and r.json()[0]["name"] == "A"


def test_gui_activities_503_when_unconfigured(monkeypatch):
    from fastapi.testclient import TestClient
    import gui.server as srv
    def boom(n=15):
        raise RuntimeError("Strava not configured")
    monkeypatch.setattr("strava_client.list_activities", boom)
    r = TestClient(srv.app).get("/api/activities")
    assert r.status_code == 503 and "Strava" in r.json()["detail"]
```

- [ ] **Step 3: Run — FAIL** (`ModuleNotFoundError: gui`, `AttributeError: list_activities`):
`.venv/bin/python -m pytest test_sync.py -k "list_activities or gui_serves or gui_activities" -v`

- [ ] **Step 4: Implement `strava_client.list_activities`** — add:

```python
def list_activities(n: int = 15) -> list:
    """Recent activities as summary dicts for the GUI picker."""
    if not is_configured():
        raise RuntimeError("Strava not configured (.strava_token.json + env vars).")
    token = _refresh_if_needed(_load_token(), now_epoch=time.time())
    _save_token(token)
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    acts = requests.get(f"{_API}/athlete/activities", headers=headers,
                        params={"per_page": n}, timeout=30).json()
    out = []
    for a in acts or []:
        out.append({
            "id": a.get("id"), "name": a.get("name") or "Activity",
            "start_date": a.get("start_date"), "type": a.get("type"),
            "distance_km": round((a.get("distance") or 0) / 1000, 1),
            "moving_time_s": a.get("moving_time") or 0,
            "elevation_gain_m": round(a.get("total_elevation_gain") or 0),
        })
    return out
```

- [ ] **Step 5: Create `gui/__init__.py`** (empty). **Create `gui/server.py`:**

```python
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

app = FastAPI(title="Ride Highlight Editor")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/api/activities")
def activities():
    import strava_client
    try:
        return strava_client.list_activities()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Strava niet beschikbaar: {e}")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
```

- [ ] **Step 6: Create the static files** from the approved mock. Read the mock at the scratchpad path above; split it into `gui/static/index.html` (the markup + `<title>`, linking `<link rel="stylesheet" href="/static/app.css">` and `<script src="/static/app.js">`), `gui/static/app.css` (the `<style>` contents), `gui/static/app.js` (the `<script>` contents). Keep the sample data for now (wiring to the API happens in Task 4). Verify it renders standalone.

- [ ] **Step 7: Create `gui.py`** (repo root launcher):

```python
"""Launch the Ride Highlight Editor GUI (local web app)."""
from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn


def main() -> None:
    p = argparse.ArgumentParser(description="Ride Highlight Editor GUI")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"Ride Highlight Editor → {url}")
    uvicorn.run("gui.server:app", host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
```

- [ ] **Step 8: Run — PASS.** **Step 9: Full suite** (`.venv/bin/python -m pytest test_sync.py -q`; +3) + `.venv/bin/python -c "import gui.server, gui.render" 2>/dev/null || .venv/bin/python -c "import gui.server"`. **Step 10: Commit** `feat: GUI backend skeleton + Strava activities + static UI + launcher`.

---

## Task 2: render argv (pure) + video/music/pick endpoints

**Files:** Create `gui/render.py`; Modify `gui/server.py`; Test `test_sync.py`.

- [ ] **Step 1: Failing tests** — append to `test_sync.py`:

```python
def test_build_render_argv_full():
    import sys as _s
    from gui.render import build_render_argv
    argv = build_render_argv({
        "videos": ["a.mp4", "b.mp4"], "strava_activity_id": "19189561939",
        "sync_offset": 577, "mode": "segments", "pick": "1,3,4",
        "music": "music/rock", "output_height": "source",
        "output_dir": "video_output", "output": "rit.mp4"})
    assert argv[:2] == [_s.executable, "main.py"]
    assert "--video" in argv and "a.mp4" in argv and "b.mp4" in argv
    assert argv[argv.index("--sync-offset") + 1] == "577"
    assert "--strava" in argv and argv[argv.index("--strava-activity-id") + 1] == "19189561939"
    assert argv[argv.index("--pick") + 1] == "1,3,4"
    assert argv[argv.index("--output") + 1] == "rit.mp4"


def test_build_render_argv_omits_absent():
    from gui.render import build_render_argv
    argv = build_render_argv({"gpx": "r.gpx", "mode": "flow", "output_dir": "video_output"})
    assert "--gpx" in argv and "--strava" not in argv
    assert "--music" not in argv and "--pick" not in argv and "--sync-offset" not in argv


@pytest.mark.skipif(_sh6.which("ffprobe") is None, reason="ffprobe not installed")
def test_api_videos_lists_clips(tmp_path):
    import subprocess
    from fastapi.testclient import TestClient
    import gui.server as srv
    for n in ["a.mp4", "b.mp4"]:
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=320x240:d=1",
                        str(tmp_path / n)], check=True, capture_output=True)
    r = TestClient(srv.app).post("/api/videos", json={"dir": str(tmp_path)})
    assert r.status_code == 200
    got = r.json()
    assert [v["name"] for v in got] == ["a.mp4", "b.mp4"] and got[0]["width"] == 320
```

- [ ] **Step 2: Run — FAIL.** `.venv/bin/python -m pytest test_sync.py -k "render_argv or api_videos" -v`

- [ ] **Step 3: Create `gui/render.py`:**

```python
from __future__ import annotations

import sys


def build_render_argv(cfg: dict) -> list:
    """Turn a GUI config dict into a main.py argv (only present flags are added)."""
    argv = [sys.executable, "main.py"]
    if cfg.get("videos"):
        argv += ["--video", *cfg["videos"]]
    if cfg.get("gpx"):
        argv += ["--gpx", cfg["gpx"]]
    if cfg.get("strava_activity_id"):
        argv += ["--strava", "--strava-activity-id", str(cfg["strava_activity_id"])]
    if cfg.get("sync_offset") is not None:
        argv += ["--sync-offset", str(cfg["sync_offset"])]
    if cfg.get("mode"):
        argv += ["--mode", cfg["mode"]]
    if cfg.get("pick"):
        argv += ["--pick", str(cfg["pick"])]
    if cfg.get("music"):
        argv += ["--music", cfg["music"]]
    if cfg.get("output_height"):
        argv += ["--output-height", str(cfg["output_height"])]
    if cfg.get("output_dir"):
        argv += ["--output-dir", cfg["output_dir"]]
    if cfg.get("output"):
        argv += ["--output", cfg["output"]]
    return argv
```

- [ ] **Step 4: Add endpoints to `gui/server.py`** (video probe, music dirs, native picker):

```python
import subprocess, json, sys, shutil


def _probe(path):
    r = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json",
                        "-show_format", "-show_streams", path], capture_output=True, text=True)
    info = json.loads(r.stdout or "{}")
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    dur = float(info.get("format", {}).get("duration", 0) or 0)
    return {"path": path, "name": os.path.basename(path), "duration": dur,
            "width": int(v.get("width", 0)), "height": int(v.get("height", 0))}


@app.post("/api/videos")
def videos(body: dict):
    from main import gather_videos_in_dir
    paths = []
    if body.get("dir"):
        paths = gather_videos_in_dir(body["dir"])
    paths += [p for p in body.get("paths", []) if os.path.isfile(p)]
    return [_probe(p) for p in paths]


_AUDIO = (".mp3", ".m4a", ".aac", ".wav")


@app.get("/api/music")
def music(dir: str):
    if not os.path.isdir(dir):
        raise HTTPException(404, f"map niet gevonden: {dir}")
    subs = []
    for name in sorted(os.listdir(dir)):
        p = os.path.join(dir, name)
        if os.path.isdir(p):
            cnt = len([f for f in os.listdir(p) if f.lower().endswith(_AUDIO)])
            if cnt:
                subs.append({"name": name, "path": p, "count": cnt})
    here = len([f for f in os.listdir(dir) if f.lower().endswith(_AUDIO)])
    return {"dir": dir, "here": here, "subfolders": subs}


def _native_pick(mode: str):
    if sys.platform == "darwin":
        prompts = {"folder": "choose folder",
                   "files": "choose file with multiple selections allowed",
                   "gpx": 'choose file of type {"gpx","xml"}'}
        scr = (f'set sel to ({prompts[mode]})\n'
               'if class of sel is list then\n set out to ""\n'
               ' repeat with f in sel\n  set out to out & POSIX path of f & "\\n"\n end repeat\n'
               ' return out\nelse\n return POSIX path of sel\nend if')
        r = subprocess.run(["osascript", "-e", scr], capture_output=True, text=True)
        if r.returncode != 0:
            return None
        return [p for p in r.stdout.strip().split("\n") if p]
    # non-macOS fallback
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk(); root.withdraw()
    if mode == "folder":
        p = filedialog.askdirectory()
        res = [p] if p else None
    elif mode == "gpx":
        p = filedialog.askopenfilename(filetypes=[("GPX", "*.gpx *.xml")])
        res = [p] if p else None
    else:
        p = filedialog.askopenfilenames()
        res = list(p) or None
    root.destroy()
    return res


@app.post("/api/pick")
def pick(body: dict):
    mode = body.get("mode", "folder")
    if mode not in ("folder", "files", "gpx"):
        raise HTTPException(400, "onbekende modus")
    paths = _native_pick(mode)
    if not paths:
        return {"cancelled": True}
    return {"path": paths[0]} if mode in ("folder", "gpx") else {"paths": paths}
```

- [ ] **Step 5: Run — PASS.** **Step 6: Full suite** (+3) + import check. **Step 7: Commit** `feat: GUI render argv + video/music/pick endpoints`.

---

## Task 3: timeline + frame + auto-align endpoints

**Files:** Modify `gui/server.py`; Test `test_sync.py`.

- [ ] **Step 1: Failing tests** — append to `test_sync.py`:

```python
def test_api_timeline_shape(monkeypatch):
    from datetime import datetime, timezone
    from fastapi.testclient import TestClient
    import gui.server as srv
    from highlight_detector import GpxData
    g = GpxData(start_time=datetime(2026, 7, 5, tzinfo=timezone.utc),
                speeds_kmh=[10.0] * 600, coords=[(52.0, 4.0)] * 600, elevations_m=[5] * 600,
                hr_bpm=[100] * 600, cum_distance_m=list(range(600)), total_distance_km=5.0,
                elevation_gain_m=20.0, moving_time_s=600.0, first_coord=(52.0, 4.0), name="Rit")
    monkeypatch.setattr("strava_gpx.gpx_from_strava", lambda i: g)
    monkeypatch.setattr("strava_client.get_segment_efforts", lambda i: [])
    r = TestClient(srv.app).get("/api/timeline", params={"activity_id": "1", "offset": 0})
    j = r.json()
    assert j["duration"] == 600 and len(j["speed"]) > 0 and j["segments"] == []


@pytest.mark.skipif(_sh6.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_api_frame_returns_jpeg(tmp_path):
    import subprocess
    from fastapi.testclient import TestClient
    import gui.server as srv
    clip = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=320x240:d=3",
                    str(clip)], check=True, capture_output=True)
    r = TestClient(srv.app).get("/api/frame", params={"video": str(clip), "t": 1.0, "w": 160})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.content[:2] == b"\xff\xd8"  # JPEG SOI
```

- [ ] **Step 2: Run — FAIL.** `.venv/bin/python -m pytest test_sync.py -k "api_timeline or api_frame" -v`

- [ ] **Step 3: Add endpoints to `gui/server.py`:**

```python
from fastapi.responses import Response


def _gpx_for(activity_id, gpx):
    from highlight_detector import load_gpx
    import strava_gpx
    return load_gpx(gpx) if gpx else strava_gpx.gpx_from_strava(activity_id)


@app.get("/api/timeline")
def timeline(activity_id: str = None, gpx: str = None, offset: float = 0.0, videos: str = ""):
    from config import Config
    g = _gpx_for(activity_id, gpx)
    cfg = Config()
    speeds = list(g.speeds_kmh or [])
    dur = len(speeds)
    n = min(300, dur) or 0
    prof = [max(speeds[i * dur // n: max(i * dur // n + 1, (i + 1) * dur // n)]) for i in range(n)] if n else []
    segs = []
    if activity_id:
        try:
            import strava_client
            from segment_detector import parse_efforts, efforts_to_activity_ranges
            efforts = parse_efforts(strava_client.get_segment_efforts(activity_id))
            for i, (a0, a1, name, _st) in enumerate(efforts_to_activity_ranges(efforts, g.start_time, cfg), 1):
                segs.append({"n": i, "name": name, "start": a0, "end": a1})
        except Exception:
            pass
    clips = []
    vids = [v for v in videos.split("|") if v]
    if vids:
        from clip_sources import build_clip_sources
        for s in build_clip_sources(vids, g, cfg, offset_override=offset):
            clips.append({"file": os.path.basename(s.path), "path": s.path, "base": s.base_offset,
                          "start": s.base_offset, "end": s.base_offset + s.duration})
    return {"duration": dur, "speed": prof, "segments": segs, "clips": clips}


_frame_cache: dict = {}


@app.get("/api/frame")
def frame(video: str, t: float, w: int = 320):
    if not os.path.isfile(video):
        raise HTTPException(404, "video niet gevonden")
    key = (video, round(t), w)
    if key not in _frame_cache:
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(max(0.0, t)), "-i", video,
                            "-frames:v", "1", "-vf", f"scale={w}:-2", "-q:v", "4",
                            "-f", "mjpeg", "pipe:1"], capture_output=True)
        if r.returncode != 0 or not r.stdout:
            raise HTTPException(404, "geen frame op deze tijd")
        if len(_frame_cache) > 200:
            _frame_cache.pop(next(iter(_frame_cache)))
        _frame_cache[key] = r.stdout
    return Response(_frame_cache[key], media_type="image/jpeg")


@app.post("/api/auto-align")
def auto_align(body: dict):
    from config import Config
    from highlight_detector import compute_optical_flow_per_second, estimate_offset_by_motion
    g = _gpx_for(body.get("activity_id"), body.get("gpx"))
    cfg = Config()
    flow = compute_optical_flow_per_second(body["video"], cfg)
    off, corr = estimate_offset_by_motion(flow, g, cfg)
    return {"offset": off, "correlation": corr}
```

- [ ] **Step 4: Run — PASS.** **Step 5: Full suite** (+2) + import check. **Step 6: Commit** `feat: GUI timeline, frame preview, and auto-align endpoints`.

---

## Task 4: render streaming + stop; wire the frontend

**Files:** Modify `gui/server.py`, `gui/static/app.js`; Test `test_sync.py`.

- [ ] **Step 1: Failing test** — append to `test_sync.py`:

```python
@pytest.mark.skipif(_sh6.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_api_render_streams_command_and_exit(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import gui.server as srv
    # a trivially-failing config (no gpx/video) is fine — we assert streaming + exit line
    cfg = {"videos": [], "gpx": "", "mode": "flow", "output_dir": str(tmp_path)}
    with TestClient(srv.app) as c:
        with c.stream("POST", "/api/render", json=cfg) as r:
            body = "".join(chunk for chunk in r.iter_text())
    assert body.startswith("$ ") and "main.py" in body and "[exit" in body
```

- [ ] **Step 2: Run — FAIL** (`404`/no route). `.venv/bin/python -m pytest test_sync.py -k api_render_streams -v`

- [ ] **Step 3: Add render endpoints to `gui/server.py`:**

```python
from fastapi.responses import StreamingResponse
from gui.render import build_render_argv

_render = {"proc": None}


@app.post("/api/render")
def render(cfg: dict):
    if _render["proc"] and _render["proc"].poll() is None:
        raise HTTPException(409, "er loopt al een render")
    argv = build_render_argv(cfg)

    def stream():
        yield "$ " + " ".join(argv) + "\n"
        p = subprocess.Popen(argv, cwd=REPO, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1)
        _render["proc"] = p
        try:
            for line in p.stdout:
                yield line
        finally:
            p.wait()
            _render["proc"] = None
            yield f"\n[exit {p.returncode}]\n"

    return StreamingResponse(stream(), media_type="text/plain")


@app.post("/api/render/stop")
def render_stop():
    p = _render["proc"]
    if p and p.poll() is None:
        p.terminate()
        return {"stopped": True}
    return {"stopped": False}
```

- [ ] **Step 4: Run — PASS** (`-k api_render_streams`).

- [ ] **Step 5: Wire the frontend** in `gui/static/app.js` — replace the mock's sample-data behaviour with real API calls. Keep the existing DOM/interaction structure; make these concrete changes:
  - On load: `fetch('/api/activities')` → render the activity rows (name, date, distance, moving time, elevation); on 503 show a notice with the `strava_auth.py` hint + a "Kies GPX-bestand…" button (`POST /api/pick {mode:'gpx'}`).
  - "Kies map…" / "Bestanden toevoegen…" → `POST /api/pick {mode:'folder'|'files'}` → then `POST /api/videos {dir|paths}` → render the clip list.
  - Selecting an activity (or a chosen GPX) + having videos → `GET /api/timeline?activity_id=&offset=&videos=<paths joined by |>` → draw the speed profile (from `speed[]`), the segment bands (from `segments[]`), and position the clip bars (from `clips[]` base/start/end vs `duration`). Re-fetch (debounced ~150 ms) when the offset slider changes.
  - Per rendered segment: `GET /api/frame?video=&t=&w=320` where `video`/`t` come from the covering clip in the timeline payload (`t = max(seg.start, clip.start) - clip.base`); set the frame `<img>` src, throttled ~150 ms on offset change; show "geen beeld" when no clip covers it.
  - "Auto-uitlijnen" → `POST /api/auto-align {video, activity_id|gpx}` (spinner) → set the offset slider + correlation.
  - Music chips: `GET /api/music?dir=music` → list subfolders; selecting one sets the music path. Output dir "Wijzig…" → `POST /api/pick {mode:'folder'}`.
  - "Start render" → build the config object, `POST /api/render`, and stream the response body into a progress panel (append text as it arrives; parse the trailing `[exit N]`); show a "Stop" button wired to `/api/render/stop`.
  - The command preview stays live client-side; the authoritative command is the first streamed line from `/api/render`.
  (The alignment math — clip placement and per-segment covered-start local time — mirrors the mock's `updateFrames`/`place`, but now driven by the real `clips`/`segments`/`duration` from `/api/timeline`.)

- [ ] **Step 6: Manual smoke** (documented, not a unit test): `.venv/bin/python gui.py --no-browser &` then `curl -s localhost:8000/ | grep -q "Ride Highlight" && echo ok`; kill it. Note in the report that full click-through was verified manually.

- [ ] **Step 7: README** — add a short "GUI" section: `python gui.py` opens the app in the browser; describe the flow (pick videos → Strava activity/GPX → align with live frames → music → output → Start).

- [ ] **Step 8: Full suite** (+1) + `.venv/bin/python -c "import gui.server"`. **Step 9: Commit** `feat: GUI render streaming + stop; wire the frontend to the API`.

---

## Self-Review

**Spec coverage:**
- Launcher `gui.py`, `gui/server.py` app + static serving, `/api/activities`, `list_activities` → Task 1. ✓
- `build_render_argv`, `/api/videos`, `/api/music`, native `/api/pick` → Task 2. ✓
- `/api/timeline` (profile + segments + clip coverage), `/api/frame` (cached JPEG), `/api/auto-align` → Task 3. ✓
- `/api/render` streaming + `/api/render/stop`; frontend wired to every endpoint incl. throttled per-segment frames; README → Task 4. ✓
- Bind 127.0.0.1 (launcher), one-render-at-a-time 409, Strava-503 + GPX fallback, reuse existing modules → Tasks 1/3/4. ✓

**Placeholder scan:** none.

**Type consistency:** `build_render_argv(cfg: dict) -> list` (Task 2) consumed by `/api/render` (Task 4). `/api/timeline` returns `{duration, speed[], segments[{n,name,start,end}], clips[{file,path,base,start,end}]}` consumed by the frontend (Task 4). `/api/frame(video,t,w)` bytes consumed per-segment (Task 4). `list_activities(n)` (Task 1) used by `/api/activities` + frontend. `REPO`/`STATIC` defined once in Task 1 and reused by later endpoints. `_gpx_for` shared by timeline + auto-align (Task 3).
```
