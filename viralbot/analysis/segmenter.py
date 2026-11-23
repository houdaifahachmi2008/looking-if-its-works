"""Candidate clip windows aligned to sentence boundaries."""
from __future__ import annotations

import numpy as np


def words_in(words: list[dict], s: float, e: float) -> list[dict]:
    return [w for w in words if w["s"] >= s - 0.05 and w["e"] <= e + 0.05]


def segs_in(segs: list[dict], s: float, e: float) -> list[dict]:
    return [x for x in segs if x["s"] >= s - 0.05 and x["e"] <= e + 0.05]


def speech_duration(words: list[dict], s: float, e: float, max_pause: float) -> float:
    """Length of the window after long pauses are compressed to ``max_pause``."""
    ws = words_in(words, s, e)
    if not ws:
        return e - s
    total = ws[-1]["e"] - ws[0]["s"]
    for a, b in zip(ws, ws[1:]):
        gap = b["s"] - a["e"]
        if gap > max_pause:
            total -= gap - max_pause
    return total


def sentence_windows(segs: list[dict], words: list[dict], min_s: float, max_s: float, max_pause: float,
                     max_candidates: int = 400) -> list[tuple[float, float]]:
    """All windows starting and ending on sentence boundaries whose *edited*
    duration (after silence removal) falls in [min_s, max_s]."""
    out = []
    for i in range(len(segs)):
        start = segs[i]["s"]
        for j in range(i, len(segs)):
            end = segs[j]["e"]
            if end - start > max_s * 2.0:
                break
            d = speech_duration(words, start, end, max_pause)
            if d > max_s:
                break
            if d >= min_s:
                out.append((round(start, 3), round(end, 3)))
        if len(out) >= max_candidates:
            break
    return out


def energy_windows(env_db: np.ndarray, hop: float, duration: float, target: float, k: int = 8) -> list[tuple[float, float]]:
    """Fallback when there is no speech: windows around the loudest moments."""
    if len(env_db) == 0 or duration < target:
        return [(0.0, min(duration, target))] if duration >= 20 else []
    win = max(1, int(3 / hop))
    smooth = np.convolve(env_db, np.ones(win) / win, mode="same")
    order = np.argsort(-smooth)
    picks: list[tuple[float, float]] = []
    for idx in order:
        t = idx * hop
        s = max(0.0, min(duration - target, t - target * 0.35))
        if all(abs(s - p[0]) > target * 0.8 for p in picks):
            picks.append((round(s, 2), round(s + target, 2)))
        if len(picks) >= k:
            break
    return picks


def overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    return inter / max(1e-6, min(a[1] - a[0], b[1] - b[0]))


def non_max_suppression(cands: list[dict], key: str, max_overlap: float = 0.25) -> list[dict]:
    kept: list[dict] = []
    for c in sorted(cands, key=lambda x: -x[key]):
        if all(overlap((c["start"], c["end"]), (k["start"], k["end"])) <= max_overlap for k in kept):
            kept.append(c)
    return kept
