"""Viral detection engine.

1. Build sentence-aligned candidate windows (20–45 s after silence removal).
2. Score all of them with free heuristics (audio energy + transcript cues).
3. Send the top-N to the LLM in ONE batched prompt (cheap: ~1 call/source)
   for the rubric: hook 25, entertainment 25, emotional 20, retention 20.
   Video quality (10) is measured technically, not by the LLM.
4. Blend LLM + heuristic, add the learning adjustment, de-duplicate overlaps,
   and store the best candidates. Only clips ≥ min_viral_score are selected.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .. import db
from ..config import env
from ..logging_setup import get_logger
from ..media import extract_audio_wav, probe, rms_envelope
from . import heuristics, learning, llm
from .segmenter import energy_windows, non_max_suppression, overlap, segs_in, sentence_windows, words_in

log = get_logger("scorer")
RUBRIC_MAX = {"hook": 25, "entertainment": 25, "emotional": 20, "retention": 20, "quality": 10}
LLM_WEIGHT = 0.65
LLM_SHORTLIST = 10

SYSTEM = (
    "You are an expert YouTube Shorts editor. You judge short spoken excerpts for their potential as "
    "standalone 20-45 second Shorts. Be strict and calibrated: an average excerpt scores about 50/100 in total; "
    "only genuinely compelling ones exceed 75. Never reward content that would need to be taken out of context "
    "to be interesting. Respond with JSON only."
)

PROMPT = """Source video title: {title}
Topic: {topic}

Rate each candidate excerpt. Rubric (points):
- hook (0-25): do the first ~3 seconds make a viewer stop scrolling?
- entertainment (0-25): funny, surprising, interesting conversation or reaction?
- emotional (0-20): emotional impact, strong reactions, relatability.
- retention (0-20): will people watch to the end? Clear payoff, self-contained, no missing context.

Also give:
- topic: 1-3 word topic label
- hook_type: one of question|number|statement|exclamation|story|reaction
- reason: one short sentence
- emphasis_words: up to 6 words from the excerpt worth highlighting in subtitles
- hook_text: a factual 2-6 word on-screen headline (no clickbait, must reflect the excerpt)
- context_ok: false if the excerpt would mislead or distort the speaker's meaning out of context

Candidates:
{candidates}

Return: {{"clips": [{{"id": <id>, "hook": n, "entertainment": n, "emotional": n, "retention": n, "topic": "...",
"hook_type": "...", "reason": "...", "emphasis_words": ["..."], "hook_text": "...", "context_ok": true}}]}}"""


def _llm_scores(source: dict, shortlist: list[dict]) -> dict[int, dict]:
    cands = "\n".join(
        f"[id={i}] ({c['start']:.1f}s-{c['end']:.1f}s) \"{c['text'][:1200]}\"" for i, c in enumerate(shortlist))
    prompt = PROMPT.format(title=source.get("title", ""), topic=source.get("topic", ""), candidates=cands)
    data = llm.complete_json(prompt, purpose="viral_scoring", system=SYSTEM, temperature=0.2)
    out = {}
    for item in data.get("clips", []):
        try:
            out[int(item["id"])] = item
        except (KeyError, ValueError, TypeError):
            continue
    return out


def _clamp(v, hi):
    try:
        return float(np.clip(float(v), 0, hi))
    except (TypeError, ValueError):
        return None


def analyze_source(source: dict, transcript: dict, settings: dict) -> list[dict]:
    video = Path(source["local_path"])
    info = probe(video)
    wav = extract_audio_wav(video, env().dir("work") / f"src_{source['id']}.wav") if info["has_audio"] else None
    env_db, hop = rms_envelope(wav) if wav else (np.array([]), 0.25)
    words, segs = transcript.get("words", []), transcript.get("segments", [])
    min_s, max_s = settings["min_clip_seconds"], settings["max_clip_seconds"]
    target = learning.preferred_length_target(settings["target_clip_seconds"])
    max_pause = settings["max_pause_seconds"] if settings.get("remove_silence", True) else 99

    windows = sentence_windows(segs, words, min_s, max_s, max_pause) if words else []
    if not windows:
        windows = energy_windows(env_db, hop, info["duration"], min(max_s, max(min_s, target)))
    if not windows:
        log.info("source %s: no usable windows", source["id"])
        return []

    cands = []
    for s, e in windows:
        ws, ss = words_in(words, s, e), segs_in(segs, s, e)
        h = heuristics.score_window(ws, ss, s, e, env_db, hop, info, target)
        text = " ".join(x["text"] for x in ss) if ss else " ".join(w["w"] for w in ws)
        cands.append({"start": s, "end": e, "text": text, "heur": h,
                      "heur_total": sum(h[k] for k in RUBRIC_MAX)})

    # pre-rank with heuristics, keep diverse shortlist
    shortlist = non_max_suppression(cands, "heur_total", max_overlap=0.5)[:LLM_SHORTLIST]

    llm_data: dict[int, dict] = {}
    if llm.available() and any(c["text"] for c in shortlist):
        try:
            llm_data = _llm_scores(source, shortlist)
        except Exception as ex:
            log.warning("LLM scoring failed for source %s, using heuristics: %s", source["id"], ex)

    platform = source.get("platform", "")
    for i, c in enumerate(shortlist):
        h, L = c["heur"], llm_data.get(i)
        scores = {}
        for k, hi in RUBRIC_MAX.items():
            lv = _clamp(L.get(k), hi) if L and k != "quality" else None
            scores[k] = round(LLM_WEIGHT * lv + (1 - LLM_WEIGHT) * h[k], 1) if lv is not None else h[k]
        c["scores"] = scores
        c["viral_score"] = round(sum(scores.values()), 1)
        c["hook_type"] = (L or {}).get("hook_type") or h["hook_type"]
        c["topic"] = source.get("topic") or (L or {}).get("topic") or ""
        c["reason"] = (L or {}).get("reason") or (
            f"heuristic: hook {h['hook']}/25, {h['words_per_sec']} words/s, silence {int(h['silence_ratio'] * 100)}%")
        c["emphasis"] = (L or {}).get("emphasis_words") or heuristics.emphasis_words(c["text"])
        c["hook_text"] = ((L or {}).get("hook_text") or "").strip()[:60]
        c["scored_by"] = "llm+heuristic" if L else "heuristic"
        if L and L.get("context_ok") is False:
            c["viral_score"] = min(c["viral_score"], 40.0)
            c["reason"] = "context risk: " + c["reason"]
        feats = learning.clip_features({"topic": c["topic"], "duration_s": c["end"] - c["start"],
                                        "hook_type": c["hook_type"]}, platform)
        c["adjustment"] = round(learning.candidate_adjustment(feats), 1)
        c["adjusted_score"] = round(float(np.clip(c["viral_score"] + c["adjustment"], 0, 100)), 1)

    final = non_max_suppression(shortlist, "adjusted_score", max_overlap=0.25)
    # skip windows overlapping clips we already made from this source
    existing = [(r["start_s"], r["end_s"]) for r in db.q("SELECT start_s,end_s FROM clips WHERE source_id=?", (source["id"],))]
    final = [c for c in final if all(overlap((c["start"], c["end"]), x) < 0.5 for x in existing)]

    min_score = float(settings["min_viral_score"])
    max_n = int(settings["max_clips_per_source"])
    selected = [c for c in final if c["adjusted_score"] >= min_score][:max_n]
    rejected = [c for c in final if c["adjusted_score"] < min_score][:3]  # keep a few for transparency

    for c, status in [(c, "candidate") for c in selected] + [(c, "below_threshold") for c in rejected]:
        cid = db.ex(
            """INSERT INTO clips(source_id,start_s,end_s,duration_s,transcript_text,scores_json,viral_score,adjusted_score,
                   score_reason,hook_type,topic,emphasis_words,hook_text,status,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (source["id"], c["start"], c["end"], round(c["end"] - c["start"], 2), c["text"],
             json.dumps({**c["scores"], "heuristic": c["heur"], "scored_by": c["scored_by"], "adjustment": c["adjustment"]}),
             c["viral_score"], c["adjusted_score"], c["reason"], c["hook_type"], c["topic"],
             json.dumps(c["emphasis"]), c["hook_text"], status, db.now(), db.now()))
        c["id"] = cid
    log.info("source %s: %d windows, %d shortlisted, %d selected (≥%s)", source["id"], len(windows),
             len(shortlist), len(selected), min_score)
    return selected
