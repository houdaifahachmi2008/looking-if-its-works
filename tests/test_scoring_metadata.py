import json

from conftest import fake_words, make_video

from viralbot import db, metadata
from viralbot.analysis import heuristics, llm, scorer
from viralbot.analysis.segmenter import non_max_suppression, sentence_windows
from viralbot.transcription import parse_srt_vtt, segments_from_words, words_from_segments

STRONG = ("Why do ninety percent of people quit the gym? Honestly, the truth is they never had a plan! "
          "Wait, what? That's crazy! No way. I failed three times and I cried. Haha, it was hilarious! "
          "You build a system and it carries you. I love that. It changed my entire life!")
WEAK = ("so um the weather has been pretty average this week and we might record on tuesday or wednesday "
        "and the sponsor is the newsletter and that is basically it for the housekeeping part of the show")


def _score(text):
    w = fake_words(text)
    segs = segments_from_words(w)
    return heuristics.score_window(w, segs, w[0]["s"], w[-1]["e"], None, 0.25, {"height": 1080, "fps": 30}, 30)


def test_heuristics_rank_strong_above_weak():
    s, w = _score(STRONG), _score(WEAK)
    total = lambda d: sum(d[k] for k in scorer.RUBRIC_MAX)
    assert total(s) > total(w) + 15
    assert s["hook_type"] == "question"
    for k, hi in scorer.RUBRIC_MAX.items():
        assert 0 <= s[k] <= hi


def test_sentence_windows_and_nms():
    words = fake_words((STRONG + " ") * 4)
    segs = segments_from_words(words)
    wins = sentence_windows(segs, words, 20, 45, 0.45)
    assert wins and all(20 <= e - s <= 50 for s, e in wins)
    cands = [{"start": s, "end": e, "score": i} for i, (s, e) in enumerate(wins)]
    kept = non_max_suppression(cands, "score", 0.25)
    assert len(kept) < len(cands)


def test_srt_parsing():
    srt = "1\n00:00:01,000 --> 00:00:03,500\nHello there friend.\n\n2\n00:00:04,000 --> 00:00:05,000\nBye!\n"
    segs = parse_srt_vtt(srt)
    assert len(segs) == 2 and segs[0]["s"] == 1.0 and segs[1]["e"] == 5.0
    words = words_from_segments(segs)
    assert words[0]["w"] == "Hello" and abs(words[2]["e"] - 3.5) < 0.01


def test_analyze_source_with_mocked_llm(tmp_path, monkeypatch):
    video = make_video(tmp_path / "v.mp4", 60)
    sid = db.ex("INSERT INTO sources(platform,external_id,title,topic,rights_status,status,local_path) "
                "VALUES('local','x','My podcast','podcast','owned','transcribed',?)", (str(video),))
    words = fake_words((STRONG + " ") * 3, start=1)
    tr = {"words": words, "segments": segments_from_words(words)}

    def fake_llm(prompt, purpose, system="", temperature=0.4):
        n = prompt.count("[id=")
        return {"clips": [{"id": i, "hook": 24, "entertainment": 23, "emotional": 18, "retention": 18,
                           "topic": "fitness", "hook_type": "question", "reason": "great",
                           "emphasis_words": ["truth"], "hook_text": "Why people quit", "context_ok": i != 0}
                          for i in range(n)]}

    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "complete_json", fake_llm)
    source = dict(db.q1("SELECT * FROM sources WHERE id=?", (sid,)))
    selected = scorer.analyze_source(source, tr, db.all_settings())
    assert selected, "expected at least one selected clip"
    for c in selected:
        assert c["adjusted_score"] >= 75 and c["scored_by"] == "llm+heuristic"
    row = db.q1("SELECT * FROM clips WHERE status='candidate' LIMIT 1")
    assert row["hook_text"] == "Why people quit" and json.loads(row["scores_json"])["quality"] <= 10


def test_metadata_guardrails():
    assert "shocking" not in metadata.sanitize_title("SHOCKING: you won't believe this").lower()
    assert metadata.sanitize_title("THIS IS ALL CAPS TITLE").startswith("This is")
    clip = {"id": 1, "transcript_text": STRONG, "topic": "fitness podcast", "hook_text": ""}
    src = {"title": "Ep 12", "rights_status": "creative_commons", "channel_title": "Chan", "url": "https://y/1"}
    meta = metadata.generate(clip, src)
    assert meta["hashtags"][0] == "#shorts" and len(meta["hashtags"]) <= 5
    assert "CC BY" in meta["description"] and len(meta["title"]) <= 100
    assert meta["category_id"] in {"17", "22", "27"}


def test_gemini_retired_model_falls_back(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-old")
    from viralbot import config
    config.reset_env()
    monkeypatch.setattr(llm, "_gemini_model_override", None)

    class R:
        def __init__(self, code, data):
            self.status_code, self._d, self.text = code, data, json.dumps(data)
        def json(self):
            return self._d
        def raise_for_status(self):
            pass

    calls = []

    def post(url, headers, json, timeout):
        calls.append(url)
        if "gemini-old" in url:
            return R(404, {"error": "not found"})
        return R(200, {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}],
                       "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}})

    def get(url, headers, params, timeout):
        return R(200, {"models": [
            {"name": "models/gemini-9-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-9-flash-image", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/embedding-1", "supportedGenerationMethods": ["embedContent"]}]})

    monkeypatch.setattr(llm.requests, "post", post)
    monkeypatch.setattr(llm.requests, "get", get)
    assert llm.complete_json("hi", "test") == {"ok": True}
    assert calls[-1].endswith("models/gemini-9-flash:generateContent")
    assert db.scalar("SELECT model FROM api_costs WHERE provider='gemini'", (), "") == "gemini-9-flash"
