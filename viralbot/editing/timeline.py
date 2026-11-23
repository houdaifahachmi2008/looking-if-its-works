"""Edit decision list: which parts of the source survive (silence removal),
and how source timestamps map onto the output timeline."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Timeline:
    keeps: list[tuple[float, float]]  # source-time intervals, in order

    @property
    def duration(self) -> float:
        return sum(b - a for a, b in self.keeps)

    def to_output(self, t: float) -> float | None:
        """Map a source timestamp to output time (None if it was cut)."""
        acc = 0.0
        for a, b in self.keeps:
            if a <= t <= b:
                return acc + (t - a)
            acc += b - a
        return None

    def clamp_to_output(self, t: float) -> float:
        """Like to_output but snaps timestamps inside a cut to the next kept moment."""
        acc = 0.0
        for a, b in self.keeps:
            if t < a:
                return acc
            if t <= b:
                return acc + (t - a)
            acc += b - a
        return acc

    def cut_points(self) -> list[float]:
        pts, acc = [], 0.0
        for a, b in self.keeps[:-1]:
            acc += b - a
            pts.append(acc)
        return pts


def build_timeline(words: list[dict], start: float, end: float, src_duration: float, *, remove_silence: bool,
                   max_pause: float, min_len: float, max_len: float, lead: float = 0.12, pad: float = 0.15,
                   tail: float = 0.35) -> Timeline:
    """Create keep-intervals for a clip.

    Only *pauses between words* are shortened – no spoken word is ever removed
    and nothing is reordered, so the speaker's meaning is preserved.
    """
    ws = [w for w in words if w["s"] >= start - 0.05 and w["e"] <= end + 0.05]
    if not ws:
        e = min(end, start + max_len, src_duration)
        return Timeline([(round(start, 3), round(e, 3))])

    a0 = max(0.0, ws[0]["s"] - lead)
    if not remove_silence:
        keeps = [(a0, min(src_duration, ws[-1]["e"] + tail))]
    else:
        keeps = []
        cur_a = a0
        for prev, nxt in zip(ws, ws[1:]):
            gap = nxt["s"] - prev["e"]
            if gap > max_pause:
                keeps.append((cur_a, prev["e"] + pad))
                cur_a = nxt["s"] - pad
        keeps.append((cur_a, min(src_duration, ws[-1]["e"] + tail)))
    tl = Timeline([(round(a, 3), round(b, 3)) for a, b in keeps if b - a > 0.05])

    # enforce max length: drop trailing words, preferring to end on a sentence boundary
    if tl.duration > max_len:
        best_end = None
        for w in ws:
            trial = _truncate(tl, w["e"] + tail)
            if trial.duration <= max_len:
                if w["w"].rstrip('"\')').endswith((".", "!", "?")) or best_end is None:
                    best_end = w["e"] + tail
            else:
                break
        tl = _truncate(tl, best_end if best_end else tl.keeps[0][0] + max_len)
        if tl.duration > max_len:  # safety
            tl = _truncate(tl, tl.keeps[0][0] + max_len)

    # too short after compressing silences → keep original pacing instead
    if tl.duration < min_len and remove_silence:
        return build_timeline(words, start, end, src_duration, remove_silence=False, max_pause=max_pause,
                              min_len=min_len, max_len=max_len, lead=lead, pad=pad, tail=tail)
    return tl


def _truncate(tl: Timeline, src_end: float) -> Timeline:
    out = []
    for a, b in tl.keeps:
        if a >= src_end:
            break
        out.append((a, min(b, src_end)))
    return Timeline(out or tl.keeps[:1])
