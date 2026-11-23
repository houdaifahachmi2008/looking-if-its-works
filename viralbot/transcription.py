"""Speech-to-text with word timestamps.

Engines (TRANSCRIBE_ENGINE):
* ``faster-whisper`` (default): free, local, CPU int8. Model weights are
  downloaded from Hugging Face on first use (~150 MB for "base").
* ``openai``: OpenAI Whisper API (~$0.006/min), word timestamps via verbose_json.
* sidecar captions: if ``<video>.srt|.vtt|.words.json`` exists next to the
  source, it is used directly (free, and usually more accurate for your own content).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests

from . import db
from .config import env
from .logging_setup import get_logger
from .media import extract_audio_wav, probe

log = get_logger("transcription")
_model = None


def _ts(s: str) -> float:
    s = s.strip().replace(",", ".")
    parts = s.split(":")
    parts = [float(p) for p in parts]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def parse_srt_vtt(text: str) -> list[dict]:
    segs = []
    pat = re.compile(r"(\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3})[^\n]*\n(.*?)(?:\n\s*\n|\Z)", re.S)
    for m in pat.finditer(text.replace("\r", "")):
        body = re.sub(r"<[^>]+>", "", m.group(3)).strip().replace("\n", " ")
        if body:
            segs.append({"text": body, "s": _ts(m.group(1)), "e": _ts(m.group(2))})
    return segs


def words_from_segments(segs: list[dict]) -> list[dict]:
    """Approximate word timings by distributing each segment's time by word length."""
    words = []
    for sg in segs:
        toks = sg["text"].split()
        if not toks:
            continue
        weights = [len(t) + 2 for t in toks]
        total = sum(weights)
        t = sg["s"]
        span = max(0.01, sg["e"] - sg["s"])
        for tok, w in zip(toks, weights):
            d = span * w / total
            words.append({"w": tok, "s": round(t, 3), "e": round(t + d, 3)})
            t += d
    return words


def segments_from_words(words: list[dict], max_gap: float = 0.8) -> list[dict]:
    """Group words into sentence-like segments (punctuation or long pause)."""
    segs, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        end_punct = w["w"].rstrip('"\')').endswith((".", "?", "!"))
        gap = (nxt["s"] - w["e"]) if nxt else 99
        if end_punct or gap > max_gap or len(cur) >= 40:
            segs.append({"text": " ".join(x["w"] for x in cur), "s": cur[0]["s"], "e": cur[-1]["e"]})
            cur = []
    return segs


def _sidecar(video: Path) -> tuple[list[dict], list[dict], str] | None:
    for ext in (".words.json", ".json"):
        p = video.with_suffix(ext)
        if p.exists():
            try:
                data = json.loads(p.read_text())
                words = data["words"] if isinstance(data, dict) else data
                words = [{"w": w["w"], "s": float(w["s"]), "e": float(w["e"])} for w in words]
                return words, segments_from_words(words), f"sidecar{ext}"
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
    for ext in (".srt", ".vtt", ".en.srt", ".en.vtt"):
        p = Path(str(video.with_suffix("")) + ext)
        if p.exists():
            segs = parse_srt_vtt(p.read_text(errors="ignore"))
            if segs:
                return words_from_segments(segs), segs, f"sidecar{ext}"
    return None


def _faster_whisper(wav: Path) -> tuple[list[dict], list[dict], str]:
    global _model
    from faster_whisper import WhisperModel
    e = env()
    if _model is None:
        log.info("loading faster-whisper model '%s' (%s/%s)", e.whisper_model, e.whisper_device, e.whisper_compute_type)
        _model = WhisperModel(e.whisper_model, device=e.whisper_device, compute_type=e.whisper_compute_type,
                              download_root=str(e.dir("models")))
    segments, info = _model.transcribe(str(wav), word_timestamps=True, vad_filter=True, language=None, beam_size=1)
    words, segs = [], []
    for sg in segments:
        segs.append({"text": sg.text.strip(), "s": sg.start, "e": sg.end})
        for w in sg.words or []:
            words.append({"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3)})
    return words, segs, info.language


def _openai_whisper(wav: Path, duration: float) -> tuple[list[dict], list[dict], str]:
    e = env()
    if wav.stat().st_size > 24 * 1024 * 1024:
        raise RuntimeError("audio > 24 MB; use faster-whisper for long sources")
    with open(wav, "rb") as f:
        r = requests.post("https://api.openai.com/v1/audio/transcriptions",
                          headers={"Authorization": f"Bearer {e.openai_api_key}"},
                          data={"model": "whisper-1", "response_format": "verbose_json",
                                "timestamp_granularities[]": ["word", "segment"]},
                          files={"file": ("audio.wav", f, "audio/wav")}, timeout=600)
    r.raise_for_status()
    data = r.json()
    db.record_cost("openai", "whisper-1", "transcription", duration / 60, "minutes",
                   duration / 60 * e.openai_whisper_price_per_min)
    words = [{"w": w["word"], "s": w["start"], "e": w["end"]} for w in data.get("words", [])]
    segs = [{"text": s["text"].strip(), "s": s["start"], "e": s["end"]} for s in data.get("segments", [])]
    return words, segs, data.get("language", "en")


def transcribe_source(source: dict) -> dict:
    video = Path(source["local_path"])
    words = segs = None
    engine = env().transcribe_engine
    lang = "en"
    side = _sidecar(video)
    if side:
        words, segs, engine = side
    elif engine != "none":
        wav = extract_audio_wav(video, env().dir("work") / f"src_{source['id']}.wav")
        if engine == "openai":
            words, segs, lang = _openai_whisper(wav, probe(video)["duration"])
        else:
            words, segs, lang = _faster_whisper(wav)
    words, segs = words or [], segs or []
    # Whisper segments can be long; re-split on punctuation for finer clip boundaries
    if words:
        segs = segments_from_words(words)
    text = " ".join(s["text"] for s in segs)
    db.ex("INSERT OR REPLACE INTO transcripts(source_id,engine,language,text,words_json,segments_json,created_at) "
          "VALUES(?,?,?,?,?,?,?)", (source["id"], engine, lang, text, json.dumps(words), json.dumps(segs), db.now()))
    log.info("source %s transcribed via %s: %d words", source["id"], engine, len(words))
    return {"words": words, "segments": segs, "engine": engine, "language": lang}


def load_transcript(source_id: int) -> dict | None:
    row = db.q1("SELECT * FROM transcripts WHERE source_id=?", (source_id,))
    if not row:
        return None
    return {"words": json.loads(row["words_json"] or "[]"), "segments": json.loads(row["segments_json"] or "[]"),
            "engine": row["engine"], "language": row["language"], "text": row["text"]}
