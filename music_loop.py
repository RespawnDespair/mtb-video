from __future__ import annotations

import subprocess

import numpy as np


def _decode_pcm(path: str, sr: int = 22050) -> np.ndarray:
    """Decode an audio file to a mono float32 numpy array at `sr` Hz."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(sr),
         "-f", "f32le", "-"],
        capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def detect_loop(path: str, min_loop_seconds: float, sr: int = 22050,
                a_max_s: float = 60.0, max_loop_s: float = 25.0):
    """Find an early, seamless loop (loop_start, loop_end) in seconds.

    Searches a coarse grid for a loop whose pre-window before loop_end best matches
    the pre-window before loop_start (smooth wrap), snapping to zero crossings.
    Falls back to (0.0, duration) when the track is too short for a loop."""
    x = _decode_pcm(path, sr)
    n = len(x)
    duration = n / sr
    win = int(0.12 * sr)
    hop = int(0.5 * sr)
    head = int(5.0 * sr)
    tail = int(1.0 * sr)
    a_max = int(a_max_s * sr)
    if n < head + int(min_loop_seconds * sr) + tail:
        return 0.0, float(duration)

    best = (None, None, 1e30)
    a = head
    while a + int(min_loop_seconds * sr) < n - tail and a <= a_max:
        wa = x[a - win:a]
        ea = float(np.sqrt(np.sum(wa * wa))) + 1e-9
        b = a + int(min_loop_seconds * sr)
        b1 = min(a + int(max_loop_s * sr), n - tail)
        while b <= b1:
            wb = x[b - win:b]
            diff = wb - wa
            seam = float(np.sqrt(np.sum(diff * diff))) / (ea + float(np.sqrt(np.sum(wb * wb))) + 1e-9)
            if seam < best[2]:
                best = (a, b, seam)
            b += hop
        a += hop
    a, b, _ = best
    if a is None:
        return 0.0, float(duration)

    def snap(i):
        lo = max(1, i - win); hi = min(n - 1, i + win)
        zc = [j for j in range(lo, hi) if x[j - 1] <= 0 < x[j]]
        return min(zc, key=lambda j: abs(j - i)) if zc else i

    return snap(a) / sr, snap(b) / sr
