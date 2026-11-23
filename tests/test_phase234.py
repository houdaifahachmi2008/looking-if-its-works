"""Audience signals, credit overlays, music detection, credit comments, Drive intake,
follow-ups, scouting and creator reports."""
import json
import wave

import numpy as np

from conftest import fake_words

from viralbot import creators, crowd, db
from viralbot.discovery.store import upsert_source


def _src(ext="v1", ch="UCabc", name="Creator", platform="youtube", dur=600, **kw):
    item = {"platform": platform, "external_id": ext, "title": "t", "channel_id": ch, "channel_title": name,
            "views": 10000, "published_at": "2026-11-22T00:00:00Z", "duration_s": dur, "url": "https://x/" + ext}
    item.update(kw)
    return upsert_source(item)[0]


# ---------------------------------------------------------------- audience signals
def test_parse_timestamps():
    assert crowd.parse_timestamps("lol 3:45 and 1:02:03 😂 but not 99:99 or 12:345", 4000) == [225.0, 3723.0]
    assert crowd.parse_timestamps("at 10:00", 300) == []  # beyond the video


def test_youtube_comment_signals(monkeypatch):
    from viralbot.discovery import youtube
    pages = [{"items": [{"snippet": {"topLevelComment": {"snippet": {"textOriginal": "2:05 😂😂", "likeCount": 500}}}},
                        {"snippet": {"topLevelComment": {"snippet": {"textOriginal": "nice vid", "likeCount": 9}}}}],
              "nextPageToken": "p2"},
             {"items": [{"snippet": {"topLevelComment": {"snippet": {"textOriginal": "2:07 and 5:00", "likeCount": 0}}}}]}]
    monkeypatch.setattr(youtube, "_get", lambda ep, params: pages.pop(0))
    sigs = crowd.youtube_comment_signals("vid", 600)
    assert sorted(s["t"] for s in sigs) == [125.0, 127.0, 300.0]
    assert max(s["weight"] for s in sigs) > 6  # the liked comment counts most


def test_twitch_vod_signals(monkeypatch):
    from viralbot.discovery import twitch
    data = {"data": [{"video_id": "42", "vod_offset": 100, "duration": 30, "view_count": 2000},
                     {"video_id": "43", "vod_offset": 50, "duration": 30, "view_count": 9},
                     {"video_id": "42", "vod_offset": None, "duration": 30, "view_count": 9}]}
    monkeypatch.setattr(twitch, "_get", lambda path, params: data)
    sigs = crowd.twitch_vod_signals({"vod_id": "42", "user_id": "7", "created_at": "2026-11-20T10:00:00Z",
                                     "duration": "2h3m"})
    assert len(sigs) == 1 and sigs[0]["t"] == 100 and sigs[0]["dur"] == 30


def test_window_scores_prefer_pointed_moment():
    sigs = [{"t": 125, "dur": 0, "weight": 5}, {"t": 127, "dur": 0, "weight": 1}, {"t": 300, "dur": 30, "weight": 2}]
    sc = crowd.window_scores(sigs, [(0, 40), (110, 150), (290, 320)])
    assert sc[1] == 1.0 and sc[0] == 0.0 and 0 < sc[2] < 1
    assert crowd.window_scores([], [(0, 1)]) == [0.0]


def test_scorer_uses_crowd(tmp_path):
    from conftest import make_video
    from viralbot.analysis import scorer
    from viralbot.transcription import segments_from_words
    video = make_video(tmp_path / "v.mp4", 130)
    text = ("this is an ordinary sentence about the weather and the week. " * 30)
    words = fake_words(text, start=1)
    sid = _src("crowdvid", dur=130)
    db.update("sources", sid, local_path=str(video), rights_status="owned")
    db.ex("INSERT INTO crowd_signals(source_id,t,dur,weight,kind) VALUES(?,?,?,?,?)", (sid, 95, 0, 10, "comment"))
    s = db.all_settings()
    s["min_viral_score"] = 0
    s["max_clips_per_source"] = 1
    best = scorer.analyze_source(dict(db.q1("SELECT * FROM sources WHERE id=?", (sid,))),
                                 {"words": words, "segments": segments_from_words(words)}, s)[0]
    assert best["start"] - 3 <= 95 <= best["end"] and best["crowd"] == 1.0
    assert json.loads(db.q1("SELECT scores_json FROM clips WHERE id=?", (best["id"],))["scores_json"])["crowd"] == 1.0


# ---------------------------------------------------------------- music detection
def _wav(path, sr, segments):
    """segments: list of (seconds, amplitude)"""
    x = np.concatenate([np.sin(np.arange(int(sr * d)) * 2 * np.pi * 220 / sr) * a for d, a in segments])
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((x * 32767).astype(np.int16).tobytes())


def test_background_music_detector():
    from viralbot import quality
    from viralbot.config import env
    for music_level, expected in ((0.25, True), (0.001, False)):
        sid = _src(f"m{music_level}", rights_status="owned")
        segs, words, t = [], [], 0.0
        for i in range(12):      # 1 s speech, 1 s pause
            segs += [(1.0, 0.5), (1.0, music_level)]
            words.append({"w": f"w{i}", "s": t, "e": t + 1.0})
            t += 2.0
        _wav(env().dir("work") / f"src_{sid}.wav", 16000, segs)
        db.ex("INSERT OR REPLACE INTO transcripts(source_id,words_json,segments_json) VALUES(?,?,?)",
              (sid, json.dumps(words), "[]"))
        flag, detail = quality.background_audio({"start_s": 0, "end_s": 24}, {"id": sid})
        assert flag is expected, detail


# ---------------------------------------------------------------- overlays & comments
def test_credit_label_and_overlay():
    from viralbot.editing.renderer import credit_label
    assert credit_label({"platform": "twitch", "channel_title": "CoolStreamer"}) == "twitch.tv/coolstreamer"
    assert credit_label({"platform": "youtube", "channel_title": "Clutch King"}) == "@ClutchKing"


def test_credit_comment(monkeypatch):
    from viralbot.publishing import uploader, youtube_auth
    s = db.all_settings()
    src = {"rights_status": "authorized", "attribution": "Original video: Sam – https://y/1 (used with permission)"}
    text = uploader.credit_comment_text(src, s)
    assert text.startswith("🎙 Credit: Original video: Sam") and "Rate this moment" in text
    assert "Credit" not in uploader.credit_comment_text({"rights_status": "owned"}, s)

    posted = []

    class YT:
        def commentThreads(self):
            return self

        def insert(self, part, body):
            posted.append(body)
            return self

        def execute(self):
            return {}

    monkeypatch.setattr(youtube_auth, "has_scope", lambda scope: True)
    assert uploader.post_credit_comment(YT(), "vid", src, "private", None, s) is False   # private: skipped
    assert uploader.post_credit_comment(YT(), "vid", src, "public", None, s) is True
    assert posted[0]["snippet"]["videoId"] == "vid"
    monkeypatch.setattr(youtube_auth, "has_scope", lambda scope: False)
    assert uploader.post_credit_comment(YT(), "vid", src, "public", None, s) is False    # needs re-login


# ---------------------------------------------------------------- creators: drive, follow-ups, scout, report
def test_drive_folder_and_discovery(monkeypatch):
    from viralbot.discovery import drive
    assert drive.folder_id("https://drive.google.com/drive/folders/1AbCdEfGhIjKlMn?usp=sharing") == "1AbCdEfGhIjKlMn"
    assert drive.folder_id("not a link") is None
    monkeypatch.setenv("YOUTUBE_API_KEY", "dummy")
    from viralbot import config
    config.reset_env()
    creators.upsert("youtube", "UCdrive", name="Drive Guy", status="approved", evidence="mail")
    creators.set_drive_folder("youtube", "UCdrive", "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMn")

    class R:
        status_code = 200

        def json(self):
            return {"files": [{"id": "f1", "name": "ep1.mp4", "mimeType": "video/mp4",
                               "videoMediaMetadata": {"durationMillis": "600000"}},
                              {"id": "f2", "name": "notes.txt", "mimeType": "text/plain"}]}

    monkeypatch.setattr(drive.requests, "get", lambda url, params, timeout: R())
    items = drive.discover(db.all_settings())
    assert len(items) == 1 and items[0]["rights_status"] == "authorized" and items[0]["duration_s"] == 600
    assert items[0]["url"].endswith("/f1?alt=media") and "used with permission" in items[0]["attribution"]


def test_followups_due():
    cid = creators.upsert("twitch", "slowreply", name="Slow", status="asked")
    db.ex("UPDATE creators SET asked_at='2026-01-01T00:00:00+00:00' WHERE id=?", (cid,))
    creators.upsert("twitch", "fresh", name="Fresh", status="asked")
    due = creators.followups_due(5)
    assert [d["channel_id"] for d in due] == ["slowreply"]
    creators.mark_followed_up(cid)
    assert creators.followups_due(5) == []
    assert "herinnering" in creators.followup_template("Slow", "X", "nl")


def test_scout_ranks_long_form_in_sweet_spot():
    _src("a", "UCbig", "Huge Star", dur=300)
    _src("b", "UCmid", "Mid Podcast", dur=3600)
    db.ex("INSERT INTO channel_stats(platform,channel_id,subscribers) VALUES('youtube','UCbig',9000000)")
    db.ex("INSERT INTO channel_stats(platform,channel_id,subscribers) VALUES('youtube','UCmid',80000)")
    res = creators.scout(10, refresh=False)
    assert res[0]["cid"] == "UCmid" and res[0]["clipability"] == 1.0
    assert [r["cid"] for r in creators.scout(10, max_subs=500000, refresh=False)] == ["UCmid"]


def test_creator_report():
    cid = creators.upsert("youtube", "UCrep", name="Rep", status="approved", evidence="ok")
    sid = _src("r1", "UCrep", "Rep")
    clip = db.ex("INSERT INTO clips(source_id,start_s,end_s,status,title) VALUES(?,0,30,'uploaded','Great moment')", (sid,))
    up = db.ex("INSERT INTO uploads(clip_id,youtube_video_id,status,uploaded_at) VALUES(?,?, 'uploaded', datetime('now'))",
               (clip, "yt1"))
    db.ex("INSERT INTO analytics(upload_id,ts,views,likes,comments) VALUES(?,?,?,?,?)", (up, db.now(), 12345, 600, 7))
    rep = creators.report(cid, 30)
    assert rep["shorts"] == 1 and rep["views"] == 12345
    txt = creators.report_text(rep, "Five Star Moments", "nl")
    assert "12.345 weergaven" in txt and "Great moment" in txt


# ---------------------------------------------------------------- discovery details & migration
def test_twitch_vod_item_and_creator_shorts_skipped(monkeypatch):
    from viralbot.discovery import twitch, youtube
    creators.upsert("twitch", "streamy", name="Streamy", status="approved", evidence="discord")
    vod = twitch._vod_item({"id": "555", "user_id": "9", "user_login": "Streamy", "title": "stream",
                            "created_at": "2026-11-20T10:00:00Z", "duration": "1h30m", "view_count": 100,
                            "url": "https://www.twitch.tv/videos/555"})
    assert vod["rights_status"] == "authorized" and vod["duration_s"] == 5400 and vod["raw"]["kind"] == "vod"

    monkeypatch.setenv("YOUTUBE_API_KEY", "dummy")
    from viralbot import config
    config.reset_env()
    creators.upsert("youtube", "UCshorts", name="S", status="approved", evidence="mail")
    monkeypatch.setattr(youtube, "channel_uploads", lambda ch: [
        {"external_id": "long", "duration_s": 1200}, {"external_id": "short", "duration_s": 45}])
    s = db.all_settings()
    s["topics"] = []
    assert [x["external_id"] for x in youtube.discover(s)] == ["long"]


def test_migration_adds_columns_to_old_creators_table():
    conn = db.connect()
    conn.execute("DROP TABLE creators")
    conn.execute("CREATE TABLE creators (id INTEGER PRIMARY KEY, platform TEXT, channel_id TEXT, status TEXT)")
    db.init_db()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(creators)")}
    assert {"drive_folder", "followups", "last_followup_at"} <= cols
