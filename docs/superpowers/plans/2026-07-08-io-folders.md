# I/O folder structure + generated output names Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `--video` accepts a folder (defaults to `video_input/`); renders land in `video_output/` (overridable via `--output-dir`); `--output` becomes an optional name override, generated from the activity name + date when omitted.

**Architecture:** New pure helpers in `main.py` resolve the video input list and the output path; `main()` normalises `args.video` to the resolved list early (so the existing single/multi paths are untouched) and threads the resolved `output_path` into `build_final_video`. Two checked-in placeholder folders keep the structure in the repo.

**Tech Stack:** Python 3.14 (`.venv`), argparse, pytest (pure tests — no ffmpeg).

## Global Constraints

- Input videos: `.mp4`/`.mov`, case-insensitive, non-recursive, sorted by filename.
- `--video` optional (folder | files | omitted→`video_input/`); `--output` optional (bare name→in `--output-dir`; path→verbatim; omitted→generated `sanitize(gpx.name)_YYYY-MM-DD.mp4`, fallback `highlight_YYYY-MM-DD.mp4`); `--output-dir` default `video_output`.
- Single-video and multi-file behaviour otherwise unchanged; `args.video` is normalised to the resolved non-empty list so all existing `args.video[0]`/`len(args.video)` uses keep working.
- Output folder (and any parent of a verbatim `--output` path) is created if absent.
- Existing suite (133) stays green. Use `.venv/bin/python`. `git add` only changed files (never `git add -A`; stray media exist).
- Commit messages end with a blank line then: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## Existing interfaces (current `main.py`)

- `build_parser()` defines `--video` (`required=True, nargs="+"`), `--output` (`default="highlight.mp4"`), `--output-height`, etc.
- `main()` order: `args = build_parser().parse_args()`; `check_ffmpeg()`; `cfg = build_config_from_args(args)`; `cfg.output_height = resolve_output_height(args.output_height, args.video[0])`; `gpx = resolve_gpx_source(args)`; `offset_override, use_auto = resolve_offset_args(args)`; `if len(args.video) > 1: return _run_multi(args, cfg, gpx, offset_override, use_auto)`; then single path with `build_final_video(reel, gpx, cfg, args.output, args, offset_seconds=…)` and `print(f"Wrote {args.output}")`.
- `_run_multi(args, cfg, gpx, offset_override, use_auto)` ends with `build_final_video(reel, gpx, cfg, args.output, args, sources=sources)` and `print(f"Wrote {args.output}")`.
- `import os`, `import sys`, `import shutil` are already at module top.

---

## Task 1: pure I/O helpers + CLI flags

**Files:** Modify `main.py`; Test `test_sync.py`.

**Interfaces produced:** `gather_videos_in_dir(directory)`, `resolve_video_inputs(args)`, `_sanitize_filename(name)`, `generate_output_name(gpx)`, `resolve_output_path(args, gpx)`; CLI `--video` optional, `--output` default None, `--output-dir` default `"video_output"`.

- [ ] **Step 1: Failing tests** — append to `test_sync.py`:

```python
def test_gather_videos_in_dir_sorted_case_insensitive(tmp_path):
    import main
    for n in ["b.mp4", "a.mov", "c.txt", "d.MP4", "notes.md"]:
        (tmp_path / n).write_bytes(b"x")
    got = [__import__("os").path.basename(p) for p in main.gather_videos_in_dir(str(tmp_path))]
    assert got == ["a.mov", "b.mp4", "d.MP4"]


def test_resolve_video_inputs_files_and_folder(tmp_path):
    import main
    from types import SimpleNamespace
    (tmp_path / "01.mp4").write_bytes(b"x")
    (tmp_path / "02.mp4").write_bytes(b"x")
    f = tmp_path / "solo.mp4"; f.write_bytes(b"x")
    # explicit file list preserved
    assert main.resolve_video_inputs(SimpleNamespace(video=[str(f)])) == [str(f)]
    # folder expanded, sorted
    got = main.resolve_video_inputs(SimpleNamespace(video=[str(tmp_path)]))
    assert [__import__("os").path.basename(p) for p in got] == ["01.mp4", "02.mp4", "solo.mp4"]


def test_resolve_video_inputs_default_dir(tmp_path, monkeypatch):
    import main
    from types import SimpleNamespace
    monkeypatch.chdir(tmp_path)
    (tmp_path / "video_input").mkdir()
    (tmp_path / "video_input" / "a.mp4").write_bytes(b"x")
    got = main.resolve_video_inputs(SimpleNamespace(video=None))
    assert [__import__("os").path.basename(p) for p in got] == ["a.mp4"]


def test_resolve_video_inputs_empty_and_missing_raise(tmp_path):
    import main, pytest as _pt
    from types import SimpleNamespace
    empty = tmp_path / "empty"; empty.mkdir()
    with _pt.raises(SystemExit):
        main.resolve_video_inputs(SimpleNamespace(video=[str(empty)]))
    with _pt.raises(SystemExit):
        main.resolve_video_inputs(SimpleNamespace(video=[str(tmp_path / "nope.mp4")]))


def test_sanitize_filename():
    import main
    assert main._sanitize_filename("MTB Goeree Vol Gas!") == "MTB_Goeree_Vol_Gas"
    assert main._sanitize_filename("a/b:c*d") == "abcd"
    assert main._sanitize_filename("  ") == ""
    assert main._sanitize_filename("__x__") == "x"


def test_generate_output_name():
    import main
    from datetime import datetime, timezone
    from types import SimpleNamespace
    g = SimpleNamespace(name="Stellendam Goeree", start_time=datetime(2026, 7, 5, tzinfo=timezone.utc))
    assert main.generate_output_name(g) == "Stellendam_Goeree_2026-07-05.mp4"
    g2 = SimpleNamespace(name=None, start_time=datetime(2026, 7, 5, tzinfo=timezone.utc))
    assert main.generate_output_name(g2) == "highlight_2026-07-05.mp4"


def test_resolve_output_path_variants(tmp_path):
    import os, main
    from datetime import datetime, timezone
    from types import SimpleNamespace
    g = SimpleNamespace(name="Rit", start_time=datetime(2026, 7, 5, tzinfo=timezone.utc))
    odir = tmp_path / "out"
    # omitted -> generated in output_dir, dir created
    p = main.resolve_output_path(SimpleNamespace(output=None, output_dir=str(odir)), g)
    assert p == os.path.join(str(odir), "Rit_2026-07-05.mp4") and odir.is_dir()
    # bare name -> in output_dir
    p = main.resolve_output_path(SimpleNamespace(output="ride.mp4", output_dir=str(odir)), g)
    assert p == os.path.join(str(odir), "ride.mp4")
    # path with separator -> verbatim
    vp = tmp_path / "sub" / "x.mp4"
    p = main.resolve_output_path(SimpleNamespace(output=str(vp), output_dir=str(odir)), g)
    assert p == str(vp) and vp.parent.is_dir()


def test_parser_io_defaults():
    import main
    a = main.build_parser().parse_args(["--gpx", "r.gpx"])
    assert a.video is None and a.output is None and a.output_dir == "video_output"
```

- [ ] **Step 2: Run — FAIL** (`AttributeError`/parse error — helpers & flags missing):
`.venv/bin/python -m pytest test_sync.py -k "gather_videos or resolve_video_inputs or sanitize_filename or generate_output_name or resolve_output_path or parser_io" -v`

- [ ] **Step 3: Implement** in `main.py`. Add near the top (after imports / `_BLOCKS`):

```python
_VIDEO_EXTS = (".mp4", ".mov")
DEFAULT_INPUT_DIR = "video_input"


def gather_videos_in_dir(directory):
    """Video files (.mp4/.mov, case-insensitive) directly in `directory`, sorted."""
    return sorted(
        os.path.join(directory, f) for f in os.listdir(directory)
        if f.lower().endswith(_VIDEO_EXTS))


def resolve_video_inputs(args):
    """Resolve --video (files, folders, or omitted->video_input/) to an ordered list."""
    paths = args.video or [DEFAULT_INPUT_DIR]
    videos = []
    for p in paths:
        if os.path.isdir(p):
            videos.extend(gather_videos_in_dir(p))
        elif os.path.isfile(p):
            videos.append(p)
        else:
            raise SystemExit(f"Video-invoer niet gevonden: {p}")
    if not videos:
        raise SystemExit(f"Geen video's (.mp4/.mov) gevonden in: {', '.join(paths)}")
    return videos


def _sanitize_filename(name):
    """Make a safe filename stem: spaces->_, keep [A-Za-z0-9_-], collapse/trim, cap len."""
    import re
    name = re.sub(r"\s+", "_", (name or "").strip())
    name = re.sub(r"[^A-Za-z0-9_-]", "", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name[:100]


def generate_output_name(gpx):
    """Generated output filename: <sanitised activity name>_YYYY-MM-DD.mp4."""
    base = _sanitize_filename(getattr(gpx, "name", None) or "") or "highlight"
    return f"{base}_{gpx.start_time.strftime('%Y-%m-%d')}.mp4"


def resolve_output_path(args, gpx):
    """Resolve the output path: verbatim if --output has a dir, else <output_dir>/<name
    or generated>. Creates the target folder."""
    if args.output:
        path = args.output if os.path.dirname(args.output) \
            else os.path.join(args.output_dir, args.output)
    else:
        path = os.path.join(args.output_dir, generate_output_name(gpx))
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return path
```

Update the three CLI flags in `build_parser()`:

```python
    p.add_argument("--video", nargs="*", default=None,
                   help="Video files or a folder of videos (.mp4/.mov). "
                        "Defaults to the video_input/ folder.")
```
```python
    p.add_argument("--output", default=None,
                   help="Output filename (placed in --output-dir) or a full path. "
                        "Default: generated from the activity name + date.")
    p.add_argument("--output-dir", default="video_output",
                   help="Folder for rendered output (default: video_output).")
```

- [ ] **Step 4: Run — PASS** (same `-k` as Step 2). **Step 5: Full suite** (`.venv/bin/python -m pytest test_sync.py -q`; +8 tests) + `.venv/bin/python -c "import main"`. **Step 6: Commit** `feat: I/O folder helpers + optional --video/--output, --output-dir flag`.

---

## Task 2: wire into main(), placeholder folders, docs

**Files:** Modify `main.py`, `README.md`; Create `video_input/README.md`, `video_output/README.md`; Test `test_sync.py`.

- [ ] **Step 1: Normalise inputs + resolve output path in `main()`.** After `check_ffmpeg()` (and before `resolve_output_height`), insert:

```python
    args.video = resolve_video_inputs(args)
```

After `gpx = resolve_gpx_source(args)`, insert:

```python
    output_path = resolve_output_path(args, gpx)
```

- [ ] **Step 2: Thread `output_path` through both paths.**
  - Multi guard: `if len(args.video) > 1: return _run_multi(args, cfg, gpx, offset_override, use_auto, output_path)`.
  - `_run_multi` signature → `def _run_multi(args, cfg, gpx, offset_override, use_auto, output_path) -> int:`; inside, change `build_final_video(reel, gpx, cfg, args.output, args, sources=sources)` → `build_final_video(reel, gpx, cfg, output_path, args, sources=sources)` and `print(f"Wrote {args.output}")` → `print(f"Wrote {output_path}")`.
  - Single path: `build_final_video(reel, gpx, cfg, args.output, args, offset_seconds=r.offset_used)` → use `output_path`; `print(f"Wrote {args.output}")` → `print(f"Wrote {output_path}")`.
  (Both `--dry-run`/`--inspect` early returns are unaffected — they run before rendering.)

- [ ] **Step 3: Failing test for main() wiring** — append to `test_sync.py`:

```python
def test_run_multi_accepts_output_path(monkeypatch):
    """_run_multi takes an explicit output_path param (signature wiring)."""
    import main, inspect
    assert "output_path" in inspect.signature(main._run_multi).parameters
```

Run: `.venv/bin/python -m pytest test_sync.py -k run_multi_accepts_output_path -v` → PASS after Step 2 (it validates the new signature).

- [ ] **Step 4: Placeholder folders.** Create `video_input/README.md`:

```markdown
# video_input

Put your source ride videos (`.mp4` / `.mov`) here. Running without `--video` uses this
folder, taking the files sorted by filename. The video files themselves are git-ignored;
this README keeps the folder in the repo.
```

Create `video_output/README.md`:

```markdown
# video_output

Rendered highlight videos are written here (the default `--output-dir`). Filenames are
generated from the activity name + date unless you pass `--output`. The video files are
git-ignored; this README keeps the folder in the repo.
```

- [ ] **Step 5: Verify.** `.venv/bin/python -c "import main"`; full suite `.venv/bin/python -m pytest test_sync.py -q` all green; confirm the placeholders are tracked and videos still ignored: `git check-ignore video_output/highlight.mp4` (ignored) and `git status --short video_input/README.md video_output/README.md` (untracked, will add). `grep -n "args.output\b" main.py` shows no leftover raw `args.output` in the render/Wrote lines (only inside `resolve_output_path`).

- [ ] **Step 6: README** — update the Usage section: show `--video` optional / folder, `video_input/`, `--output-dir`, and generated names. Replace the two example blocks near the top so they reflect the new defaults, e.g.:

```markdown
# Put clips in video_input/ (or pass --video), render to video_output/:
python main.py --gpx ride.gpx --strava --strava-activity-id 1234567890 --mode segments

# Explicit inputs + a folder, custom output name/dir:
python main.py --video clip1.mp4 clip2.mp4 --gpx ride.gpx \
    --output myrun.mp4 --output-dir renders/
```

Add a short note: "Without `--output` the file is named `<activity>_<YYYY-MM-DD>.mp4` in `--output-dir` (default `video_output/`). `--video` defaults to `video_input/` and also accepts a folder."

- [ ] **Step 7: Commit** — stage `main.py README.md test_sync.py video_input/README.md video_output/README.md`. Message: `feat: default video_input/ + video_output/ folders and generated output names`.

---

## Self-Review

**Spec coverage:**
- `gather_videos_in_dir`, `resolve_video_inputs` (folder/files/default/empty/missing) → Task 1. ✓
- `_sanitize_filename`, `generate_output_name` (name + fallback), `resolve_output_path` (generated/bare/verbatim + dir creation) → Task 1. ✓
- CLI `--video` optional, `--output` default None, `--output-dir` → Task 1. ✓
- `main()` normalises `args.video`, computes `output_path`, threads it (single + `_run_multi` with new param) → Task 2. ✓
- Placeholder `video_input//video_output/` READMEs; README docs → Task 2. ✓

**Placeholder scan:** none.

**Type consistency:** helpers read `args.video`/`args.output`/`args.output_dir` and `gpx.name`/`gpx.start_time`; `resolve_output_path(args, gpx) -> str` feeds `build_final_video(..., output_path, ...)` (existing `output` positional) and `_run_multi(..., output_path)` (new trailing param) consistently. `args.video` normalised to `list[str]` before any `args.video[0]`/`len(args.video)` use.
