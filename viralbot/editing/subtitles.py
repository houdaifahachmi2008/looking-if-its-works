"""Animated ASS subtitles: short word groups, active word highlighted,
emphasis words coloured — rendered by libass inside FFmpeg (free)."""
from __future__ import annotations

import re

from .timeline import Timeline

WHITE = "&H00FFFFFF&"
YELLOW = "&H0000E5FF&"   # ASS colours are &HAABBGGRR
GREEN = "&H0066FF66&"


def _ts(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _clean(word: str) -> str:
    w = word.replace("\\", "").replace("{", "").replace("}", "").strip()
    return w


def _norm(word: str) -> str:
    return re.sub(r"[^a-z0-9']", "", word.lower())


def group_words(words: list[dict], max_words: int = 3, max_chars: int = 18, max_gap: float = 0.35) -> list[list[dict]]:
    groups, cur = [], []
    for i, w in enumerate(words):
        if cur:
            chars = sum(len(x["w"]) + 1 for x in cur) + len(w["w"])
            gap = w["s"] - cur[-1]["e"]
            if len(cur) >= max_words or chars > max_chars or gap > max_gap or cur[-1]["w"].endswith((".", "?", "!", ",")):
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def build_ass(words: list[dict], timeline: Timeline, emphasis: list[str], *, font: str, banner: str = "",
              words_per_line: int = 3, width: int = 1080, height: int = 1920) -> str:
    # move words onto the output timeline (words inside cuts don't exist by construction)
    out_words = []
    lo, hi = timeline.keeps[0][0] - 0.05, timeline.keeps[-1][1] + 0.05
    for w in words:
        if w["s"] < lo or w["s"] > hi:  # outside this clip entirely
            continue
        s = timeline.to_output(w["s"])
        e = timeline.to_output(w["e"])
        if s is None:
            s = timeline.clamp_to_output(w["s"])
        if e is None:
            e = timeline.clamp_to_output(w["e"])
        if e - s < 0.05:
            e = s + 0.12
        if s < timeline.duration:
            out_words.append({"w": _clean(w["w"]), "s": s, "e": min(e, timeline.duration)})
    out_words = [w for w in out_words if w["w"]]
    emph = {_norm(x) for x in emphasis}

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{font},92,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,1,0,1,7,3,2,60,60,560,1
Style: Banner,{font},62,&H00FFFFFF,&H00FFFFFF,&H00000000,&HB4141414,-1,0,0,0,100,100,0,0,3,18,0,8,90,90,170,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if banner:
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(timeline.duration)},Banner,,0,0,0,,{_clean(banner)}")

    for g in group_words(out_words, max_words=words_per_line):
        g_start = g[0]["s"]
        for k, w in enumerate(g):
            seg_start = g_start if k == 0 else w["s"]
            seg_end = g[k + 1]["s"] if k + 1 < len(g) else max(w["e"], g[-1]["e"])
            if seg_end - seg_start < 0.03:
                continue
            parts = []
            for j, x in enumerate(g):
                token = x["w"].upper()
                is_emph = _norm(x["w"]) in emph
                if j == k:
                    parts.append(f"{{\\c{YELLOW}\\fscx112\\fscy112}}{token}{{\\c{WHITE}\\fscx100\\fscy100}}")
                elif is_emph:
                    parts.append(f"{{\\c{GREEN}}}{token}{{\\c{WHITE}}}")
                else:
                    parts.append(token)
            pop = "{\\fscx85\\fscy85\\t(0,90,\\fscx100\\fscy100)}" if k == 0 else ""
            lines.append(f"Dialogue: 0,{_ts(seg_start)},{_ts(seg_end)},Sub,,0,0,0,,{pop}{' '.join(parts)}")
    return header + "\n".join(lines) + "\n"
