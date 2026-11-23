import pytest

from viralbot import creators, db
from viralbot.discovery.store import upsert_source


def _lead(ext, channel_id, name, platform="youtube"):
    sid, _ = upsert_source({"platform": platform, "external_id": ext, "title": f"vid {ext}", "channel_id": channel_id,
                            "channel_title": name, "views": 50_000, "likes": 2000, "comments": 100,
                            "published_at": "2026-11-22T00:00:00Z", "url": f"https://youtu.be/{ext}"})
    return sid


def test_approval_requires_evidence():
    with pytest.raises(ValueError, match="evidence"):
        creators.upsert("youtube", "UCabc", status="approved")


def test_approve_authorizes_existing_and_revoke_stops_clips():
    s1, s2 = _lead("a1", "UCabc", "Podcaster"), _lead("a2", "UCabc", "Podcaster")
    other = _lead("b1", "UCzzz", "Someone else")
    creators.upsert("youtube", "UCabc", name="Podcaster", status="asked")
    assert db.q1("SELECT status FROM sources WHERE id=?", (s1,))["status"] == "lead"
    creators.upsert("youtube", "UCabc", name="Podcaster", status="approved", evidence="email 2026-11-25")
    for sid in (s1, s2):
        r = db.q1("SELECT * FROM sources WHERE id=?", (sid,))
        assert r["rights_status"] == "authorized" and r["status"] == "queued"
        assert "used with permission" in r["attribution"] and "email 2026-11-25" in r["rights_evidence"]
    assert db.q1("SELECT rights_status FROM sources WHERE id=?", (other,))["rights_status"] == "unknown"
    cid = db.ex("INSERT INTO clips(source_id,start_s,end_s,status) VALUES(?,0,30,'pending_approval')", (s1,))
    creators.upsert("youtube", "UCabc", name="Podcaster", status="revoked", evidence="email 2026-11-25")
    assert db.q1("SELECT rights_status FROM sources WHERE id=?", (s1,))["rights_status"] == "unknown"
    assert db.q1("SELECT status FROM clips WHERE id=?", (cid,))["status"] == "rejected"


def test_discovery_marks_approved_creators():
    from viralbot.discovery import twitch, youtube
    creators.upsert("youtube", "UCyes", name="Yes", status="approved", evidence="DM")
    creators.upsert("twitch", "CoolStreamer", name="CoolStreamer", status="approved", evidence="Discord")
    yt = youtube._rights_for({"channel_id": "UCyes", "title": "t", "channel_title": "Yes", "url": "u"}, False)
    assert yt["rights_status"] == "authorized"
    assert youtube._rights_for({"channel_id": "UCno", "title": "t", "channel_title": "No", "url": "u"}, False)["rights_status"] == "unknown"
    item = twitch._clip_item({"id": "c1", "broadcaster_name": "coolstreamer", "broadcaster_id": "999",
                              "url": "https://clips.twitch.tv/c1", "view_count": 10}, "twitch:x")
    assert item["rights_status"] == "authorized" and "twitch.tv/coolstreamer" in item["attribution"]
    assert twitch._clip_item({"id": "c2", "broadcaster_name": "other", "broadcaster_id": "1"}, "x")["rights_status"] == "unknown"
    assert creators.approved_ids("twitch") == ["coolstreamer"]


def test_suggestions_and_template():
    _lead("s1", "UCpop", "Popular Pod")
    _lead("s2", "UCpop", "Popular Pod")
    sug = creators.suggestions()
    assert sug and sug[0]["cid"] == "UCpop" and sug[0]["videos"] == 2
    creators.upsert("youtube", "UCpop", name="Popular Pod")
    assert all(s["cid"] != "UCpop" for s in creators.suggestions())
    nl = creators.message_template("Sam", "Five Star Moments", "nl")
    en = creators.message_template("Sam", "Five Star Moments", "en")
    assert "Hoi Sam" in nl and "Five Star Moments" in nl and "inkomsten" in nl
    assert "Hi Sam" in en and "monetized" in en


def test_ytdlp_command_uses_current_python(monkeypatch):
    import importlib.util
    import sys
    from viralbot import importer
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object() if name == "yt_dlp" else None)
    assert importer.ytdlp_command() == [sys.executable, "-m", "yt_dlp"]
