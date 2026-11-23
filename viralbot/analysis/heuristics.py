"""Free, offline signal extraction used for (a) pre-ranking candidates before
spending LLM tokens and (b) a full fallback scorer when no LLM is configured."""
from __future__ import annotations

import re

import numpy as np

FILLER_START = {"so", "um", "uh", "and", "but", "like", "yeah", "okay", "ok", "well", "anyway", "right"}
HOOK_WORDS = {"you", "your", "why", "how", "what", "never", "nobody", "everyone", "secret", "truth", "actually",
              "wait", "crazy", "insane", "biggest", "worst", "best", "stop", "imagine", "listen", "honestly",
              "literally", "mistake", "nobody's", "here's", "this", "only", "first", "real"}
FUNNY = {"haha", "hahaha", "lol", "lmao", "laugh", "laughing", "laughed", "funny", "hilarious", "joke", "joking",
         "bro", "dude", "ridiculous", "dumb", "stupid", "weird", "[laughter]", "(laughs)", "[laughs]"}
SURPRISE = {"wait", "what", "suddenly", "unexpected", "twist", "shocked", "unbelievable", "wow", "whoa", "omg",
            "seriously", "impossible", "turns", "realized", "plot"}
EMOTION = {"love", "hate", "amazing", "incredible", "scared", "afraid", "cry", "crying", "cried", "angry", "furious",
           "dream", "dreams", "fail", "failed", "failure", "quit", "proud", "believe", "pain", "hurt", "happy", "sad",
           "lost", "win", "won", "never", "everything", "nothing", "alone", "family", "fear", "heart", "life", "die",
           "died", "broke", "broken", "hope", "beautiful", "terrible", "disgusting", "grateful"}
PHRASES = {"no way": ("surprise", 2), "oh my god": ("surprise", 2), "what the": ("surprise", 2),
           "turns out": ("surprise", 2), "i can't believe": ("emotion", 2), "the truth is": ("hook", 2),
           "here's the thing": ("hook", 2), "let me tell you": ("hook", 2), "you won't believe": ("hook", 2),
           "never give up": ("emotion", 2)}

_tok = re.compile(r"[a-z0-9']+|\[[a-z]+\]|\([a-z]+\)")


def tokens(text: str) -> list[str]:
    return _tok.findall(text.lower())


def hook_type(first_sentence: str) -> str:
    s = first_sentence.strip().lower()
    if s.endswith("?") or s.split(" ")[0:1] and s.split(" ")[0] in {"why", "how", "what", "who", "when", "would", "have", "do", "did", "is", "are"}:
        return "question"
    if re.search(r"\d", s):
        return "number"
    if s.endswith("!"):
        return "exclamation"
    if re.match(r"^(i|we|my|when i|so i)\b", s):
        return "story"
    return "statement"


def energy_slice(env_db: np.ndarray | None, hop: float, s: float, e: float) -> np.ndarray:
    if env_db is None or len(env_db) == 0:
        return np.array([])
    a, b = int(s / hop), max(int(s / hop) + 1, int(e / hop))
    return env_db[a:b]


def score_window(words: list[dict], segs: list[dict], s: float, e: float, env_db: np.ndarray | None, hop: float,
                 src_info: dict, target: float) -> dict:
    """Heuristic rubric: hook 25, entertainment 25, emotional 20, retention 20, quality 10."""
    text = " ".join(w["w"] for w in words)
    toks = tokens(text)
    n = max(1, len(toks))
    dur = max(0.1, e - s)
    first = segs[0]["text"] if segs else text[:80]
    first_toks = tokens(first)
    low = text.lower()
    phrase_hits = {"hook": 0, "surprise": 0, "emotion": 0}
    for ph, (kind, w) in PHRASES.items():
        if ph in low:
            phrase_hits[kind] += w

    env_all_med = float(np.median(env_db)) if env_db is not None and len(env_db) else -30.0
    win = energy_slice(env_db, hop, s, e)
    head = energy_slice(env_db, hop, s, s + 3)
    win_med = float(np.median(win)) if len(win) else env_all_med
    head_med = float(np.median(head)) if len(head) else win_med
    dyn = float(np.percentile(win, 90) - np.percentile(win, 30)) if len(win) > 4 else 0.0

    # ---- hook (25)
    hook = 6.0
    if first.strip().endswith("?"):
        hook += 6
    if any(t in {"you", "your", "you're"} for t in first_toks):
        hook += 3
    if re.search(r"\d", first):
        hook += 3
    hook += min(6, 2 * sum(1 for t in first_toks if t in HOOK_WORDS))
    hook += min(3, phrase_hits["hook"])
    if len(first_toks) <= 14:
        hook += 2
    if first_toks and first_toks[0] in FILLER_START:
        hook -= 4
    hook += float(np.clip((head_med - env_all_med) / 2, -2, 4))
    hook = float(np.clip(hook, 0, 25))

    # ---- entertainment (25)
    funny = sum(1 for t in toks if t in FUNNY)
    surprise = sum(1 for t in toks if t in SURPRISE) + phrase_hits["surprise"]
    excl = text.count("!")
    qs = text.count("?")
    ent = 7.0 + min(7, funny * 2.0) + min(6, surprise * 1.5) + min(3, excl * 0.75) + min(2, qs * 0.5)
    ent += float(np.clip(dyn / 4, 0, 3))
    ent = float(np.clip(ent, 0, 25))

    # ---- emotional (20)
    emo_hits = sum(1 for t in toks if t in EMOTION) + phrase_hits["emotion"]
    emo = 5.0 + min(9, emo_hits * 9 / max(4, n / 12)) + min(3, excl * 0.75)
    emo += float(np.clip((win_med - env_all_med) / 2, -1, 3))
    emo = float(np.clip(emo, 0, 20))

    # ---- retention (20)
    wps = len(words) / dur
    if 2.2 <= wps <= 3.8:
        ret = 8.0
    else:
        ret = max(0.0, 8 - abs(wps - 3.0) * 4)
    speech_time = sum(max(0.0, w["e"] - w["s"]) for w in words)
    silence_ratio = 1 - min(1.0, speech_time / dur)
    ret += 5 * (1 - min(1.0, silence_ratio / 0.5))
    last = segs[-1]["text"].strip() if segs else text
    if last.endswith((".", "!", "?")):
        ret += 3
    ret += 4 * max(0.0, 1 - abs(dur - target) / 20)
    ret = float(np.clip(ret, 0, 20))

    # ---- quality (10)
    h = min(src_info.get("width") or 0, src_info.get("height") or 0) or (src_info.get("height") or 0)
    q = 5 if h >= 1080 else 4 if h >= 720 else 2 if h >= 480 else 1
    q += 3 if -32 <= win_med <= -8 else 1
    q += 2 if (src_info.get("fps") or 30) >= 24 else 1
    q = float(np.clip(q, 0, 10))

    return {"hook": round(hook, 1), "entertainment": round(ent, 1), "emotional": round(emo, 1),
            "retention": round(ret, 1), "quality": round(q, 1), "hook_type": hook_type(first),
            "words_per_sec": round(wps, 2), "silence_ratio": round(silence_ratio, 2)}


def emphasis_words(text: str, limit: int = 8) -> list[str]:
    """Pick words worth highlighting in subtitles."""
    out = []
    for t in tokens(text):
        if (t in EMOTION or t in SURPRISE or t in FUNNY or t.isdigit() or t in {"never", "always", "secret", "truth",
                                                                                 "million", "billion", "best", "worst"}):
            if t not in out:
                out.append(t)
    return out[:limit]
