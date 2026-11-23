"""Title / description / hashtags / category generation.

LLM when available (one small call per clip), deterministic template fallback
otherwise. A guard rejects misleading clickbait patterns and enforces
YouTube limits (title ≤ 100 chars, description ≤ 5000, no '<' '>').
"""
from __future__ import annotations

import json
import re

from . import db
from .analysis import heuristics, llm
from .config import YOUTUBE_CATEGORIES
from .logging_setup import get_logger
from .rights import attribution_line

log = get_logger("metadata")

CLICKBAIT = re.compile(r"\b(you won'?t believe|gone wrong|gone sexual|not clickbait|shocking|must see|"
                       r"\(?emotional\)?|100% real|insane!!+|watch till the end)\b", re.I)
TOPIC_CATEGORY = [
    (r"gam|minecraft|fortnite|valorant|stream|twitch|clutch", "20"),
    (r"podcast|interview|talk|conversation", "22"),
    (r"funny|comedy|joke|prank|laugh", "23"),
    (r"motivat|mindset|success|discipline|learn|educat|lesson", "27"),
    (r"tech|ai|science|code|software", "28"),
    (r"sport|football|basketball|soccer|fitness|gym", "17"),
    (r"news|politic|debate", "25"),
]

PROMPT = """Write YouTube Shorts metadata (English) for this clip.

Source title: {title}
Source creator: {creator}
Topic: {topic}
Transcript of the clip: "{text}"

Rules:
- title: max 70 characters, curiosity-driven but ACCURATE to the transcript. No clickbait phrases
  ("you won't believe", "shocking", "gone wrong"), no ALL CAPS sentences, max 1 emoji.
- description: 1-2 sentences describing what happens, honest, no false claims.
- hashtags: 3-5 relevant hashtags without spaces (include #shorts).
- category_id: one of {cats}

Return JSON: {{"title": "...", "description": "...", "hashtags": ["#..."], "category_id": "24"}}"""


def guess_category(topic: str, text: str, default: str = "24") -> str:
    blob = f"{topic} {text}".lower()
    for pat, cid in TOPIC_CATEGORY:
        if re.search(pat, blob):
            return cid
    return default


def sanitize_title(t: str) -> str:
    t = re.sub(r"[<>]", "", t or "").strip().strip('"')
    t = CLICKBAIT.sub("", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" -|:")
    letters = [c for c in t if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.6:
        t = t.capitalize()
    if len(t) > 90:
        t = t[:87].rsplit(" ", 1)[0] + "…"
    return t


def _fallback(clip: dict, source: dict) -> dict:
    text = clip.get("transcript_text") or ""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    first = next((s for s in sentences if 4 <= len(s.split()) <= 14), sentences[0] if sentences else "")
    hook = (clip.get("hook_text") or "").strip()
    title = hook or first or source.get("title", "Short clip")
    title = title.rstrip(".")
    kws = [w for w in heuristics.emphasis_words(text, 3) if not w.isdigit()]
    topic_tag = re.sub(r"[^a-z0-9]", "", (clip.get("topic") or "").lower().split(":")[-1])[:24]
    tags = ["#shorts"] + ([f"#{topic_tag}"] if topic_tag else []) + [f"#{k}" for k in kws[:2]]
    rest = [x for x in sentences if x != first][:2]
    desc = " ".join(rest)[:220] if rest else first
    if source.get("title"):
        desc = f"{desc}\n\nFrom: {source['title']}".strip()
    return {"title": title, "description": desc, "hashtags": tags,
            "category_id": guess_category(clip.get("topic") or "", text)}


def generate(clip: dict, source: dict, default_category: str = "24") -> dict:
    meta = None
    if llm.available():
        try:
            meta = llm.complete_json(PROMPT.format(
                title=source.get("title", ""), creator=source.get("channel_title", ""), topic=clip.get("topic", ""),
                text=(clip.get("transcript_text") or "")[:2500], cats=json.dumps(YOUTUBE_CATEGORIES)),
                purpose="metadata", temperature=0.7)
        except Exception as ex:
            log.warning("LLM metadata failed for clip %s: %s", clip.get("id"), ex)
    if not meta or not meta.get("title"):
        meta = _fallback(clip, source)

    title = sanitize_title(meta.get("title", ""))[:95]
    hashtags = []
    for h in meta.get("hashtags", []):
        h = "#" + re.sub(r"[^\w]", "", str(h).lstrip("#"))
        if len(h) > 2 and h.lower() not in [x.lower() for x in hashtags]:
            hashtags.append(h)
    if "#shorts" not in [h.lower() for h in hashtags]:
        hashtags.insert(0, "#shorts")
    hashtags = hashtags[:5]
    cat = str(meta.get("category_id") or "")
    if cat not in YOUTUBE_CATEGORIES:
        cat = guess_category(clip.get("topic") or "", clip.get("transcript_text") or "", default_category)

    parts = [re.sub(r"[<>]", "", meta.get("description", "")).strip()]
    if clip.get("commentary"):
        parts.append(f"Commentary: {clip['commentary'].strip()}")
    attr = attribution_line(source)
    if attr:
        parts.append(attr)
    parts.append(" ".join(hashtags))
    description = "\n\n".join(p for p in parts if p)[:4900]
    tags = [h.lstrip("#") for h in hashtags] + [t for t in re.findall(r"[a-z]{4,}", (clip.get("topic") or "").lower())][:5]
    return {"title": title or "Short", "description": description, "hashtags": hashtags,
            "category_id": cat, "tags": tags[:15]}


def apply(clip_id: int) -> dict:
    clip = dict(db.q1("SELECT * FROM clips WHERE id=?", (clip_id,)))
    source = dict(db.q1("SELECT * FROM sources WHERE id=?", (clip["source_id"],)))
    meta = generate(clip, source, db.get_setting("default_category_id") or "24")
    db.update("clips", clip_id, title=meta["title"], description=meta["description"],
              hashtags=json.dumps(meta["hashtags"]), category_id=meta["category_id"])
    return meta
