import json

import pytest

from viralbot import db, rights
from viralbot.discovery.ranking import discovery_score
from viralbot.discovery.store import upsert_source
from viralbot.importer import ImportBlocked, import_source


def _src(**kw):
    base = {"platform": "youtube", "external_id": "abc", "title": "t", "views": 1000, "likes": 50, "comments": 5,
            "published_at": "2026-11-22T00:00:00Z"}
    base.update(kw)
    return base


def test_unknown_rights_become_leads_and_never_import():
    sid, new = upsert_source(_src())
    row = db.q1("SELECT * FROM sources WHERE id=?", (sid,))
    assert new and row["status"] == "lead" and row["rights_status"] == "unknown"
    with pytest.raises(ImportBlocked):
        import_source(dict(row))


def test_set_rights_queues_and_logs():
    sid, _ = upsert_source(_src())
    rights.set_rights(sid, "authorized", "email from creator 2026-11-01", actor="test")
    row = db.q1("SELECT * FROM sources WHERE id=?", (sid,))
    assert row["status"] == "queued" and row["rights_evidence"].startswith("email")
    assert db.scalar("SELECT COUNT(*) FROM rights_log WHERE source_id=?", (sid,)) == 1
    rights.set_rights(sid, "rejected")
    assert db.q1("SELECT status FROM sources WHERE id=?", (sid,))["status"] == "lead"


def test_risk_assessment():
    risk, w = rights.assess_clip_risk({"rights_status": "creative_commons"}, has_commentary=False)
    assert risk == "medium" and any("reused content" in x for x in w)
    assert rights.assess_clip_risk({"rights_status": "owned"}, False)[0] == "low"
    assert rights.assess_clip_risk({"rights_status": "unknown"}, False)[0] == "high"
    assert rights.assess_clip_risk({"rights_status": "authorized", "rights_evidence": ""}, True)[0] == "high"


def test_discovery_score_prefers_fast_growth():
    fast, _, _ = discovery_score(500_000, 30_000, 2_000, "2026-11-22T12:00:00Z")
    slow, _, _ = discovery_score(500_000, 3_000, 100, "2025-01-01T00:00:00Z")
    assert 0 <= slow < fast <= 100


class FakeResp:
    def __init__(self, data, status=200):
        self._d, self.status_code, self.text = data, status, json.dumps(data)

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_youtube_discovery_marks_cc_and_counts_quota(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "dummy")
    from viralbot import config
    config.reset_env()
    from viralbot.discovery import run_discovery, youtube

    def fake_get(url, params, timeout):
        if url.endswith("/search"):
            assert params["videoLicense"] == "creativeCommon"
            return FakeResp({"items": [{"id": {"videoId": "v1"}}, {"id": {"videoId": "v2"}}]})
        return FakeResp({"items": [
            {"id": "v1", "snippet": {"title": "CC talk", "channelId": "c1", "channelTitle": "Chan",
                                     "publishedAt": "2026-11-22T00:00:00Z"},
             "statistics": {"viewCount": "90000", "likeCount": "5000", "commentCount": "300"},
             "contentDetails": {"duration": "PT12M3S"}, "status": {"license": "creativeCommon"}},
            {"id": "v2", "snippet": {"title": "Std", "channelId": "c2", "channelTitle": "Other",
                                     "publishedAt": "2026-11-20T00:00:00Z"},
             "statistics": {"viewCount": "1500"}, "contentDetails": {"duration": "PT5M"},
             "status": {"license": "youtube"}}]})

    monkeypatch.setattr(youtube.requests, "get", fake_get)
    s = db.all_settings()
    s["topics"] = ["podcast"]
    s["sources_enabled"] = {"youtube": True, "twitch": False, "local_inbox": False}
    stats = run_discovery(s)
    assert stats["new"] == 2 and stats["processable_new"] == 1
    v1 = db.q1("SELECT * FROM sources WHERE external_id='v1'")
    assert v1["rights_status"] == "creative_commons" and v1["status"] == "queued" and "CC BY" in v1["attribution"]
    assert v1["duration_s"] == 723
    assert db.q1("SELECT status FROM sources WHERE external_id='v2'")["status"] == "lead"
    assert db.youtube_quota_used_today() == 101


def test_quota_guard_stops_discovery(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "dummy")
    monkeypatch.setenv("YOUTUBE_DAILY_QUOTA", "5200")
    from viralbot import config
    config.reset_env()
    from viralbot.discovery import youtube
    with pytest.raises(youtube.QuotaExceeded):
        youtube.QuotaGuard.spend("search")


def test_local_inbox_and_sidecar_rights(tmp_path):
    from conftest import make_video
    from viralbot.config import env
    from viralbot.discovery import run_discovery
    inbox = env().dir("inbox")
    make_video(inbox / "mine.mp4", 3)
    make_video(inbox / "cc.mp4", 3)
    (inbox / "cc.rights.json").write_text(json.dumps({"rights_status": "creative_commons", "attribution": "by X"}))
    s = db.all_settings()
    s["sources_enabled"] = {"youtube": False, "twitch": False, "local_inbox": True}
    assert run_discovery(s)["processable_new"] == 2
    assert {r["rights_status"] for r in db.q("SELECT rights_status FROM sources")} == {"owned", "creative_commons"}


def test_tiny_videos_do_not_look_viral():
    tiny, _, er = discovery_score(3, 1, 0, "2026-11-22T00:00:00Z")
    real, _, _ = discovery_score(200_000, 12_000, 800, "2026-11-22T00:00:00Z")
    assert er < 0.01 and tiny < real - 30
