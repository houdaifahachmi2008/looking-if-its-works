import pytest

from viralbot import db
from viralbot.analysis import learning
from viralbot.publishing import uploader


def _clip(tmp_path, status="approved", risk="low", rights="owned"):
    f = tmp_path / "c.mp4"
    f.write_bytes(b"0" * 1000)
    sid = db.ex("INSERT INTO sources(platform,external_id,rights_status,status) VALUES('local',?,?, 'analyzed')",
                (str(tmp_path), rights))
    return db.ex("INSERT INTO clips(source_id,start_s,end_s,status,file_path,title,description,hashtags,category_id,"
                 "risk_level,content_hash) VALUES(?,0,30,?,?,'A title','desc','[\"#shorts\"]','24',?,'abc123')",
                 (sid, status, str(f), risk))


def test_privacy_requires_second_gate():
    s = db.all_settings()
    s["default_privacy"] = "public"
    assert uploader.resolve_privacy(s) == "private"
    s["public_publishing_approved"] = True
    assert uploader.resolve_privacy(s) == "public"


def test_scheduled_publish_slot():
    s = db.all_settings()
    s.update(default_privacy="public", public_publishing_approved=True, schedule_uploads=True)
    body, at = uploader.build_body({"id": 1, "title": "Hi", "hashtags": "[]", "content_hash": "x"}, s)
    assert at and body["status"]["privacyStatus"] == "private" and body["status"]["publishAt"] == at
    assert body["snippet"]["title"].endswith("#Shorts") and "vb1x" in body["snippet"]["tags"][-1]


class FakeRequest:
    calls = 0

    def next_chunk(self):
        FakeRequest.calls += 1
        if FakeRequest.calls == 1:
            raise ConnectionError("flaky network")
        return None, {"id": "YT123"}


class FakeYT:
    def videos(self):
        return self

    def insert(self, part, body, media_body):
        assert body["status"]["privacyStatus"] == "private"
        return FakeRequest()


def test_upload_retries_records_and_dedupes(tmp_path, monkeypatch):
    monkeypatch.setattr(uploader, "youtube_client", lambda: FakeYT())
    monkeypatch.setattr(uploader.time, "sleep", lambda s: None)
    cid = _clip(tmp_path)
    vid = uploader.upload_clip(cid, db.all_settings())
    assert vid == "YT123" and FakeRequest.calls == 2
    assert db.q1("SELECT status FROM clips WHERE id=?", (cid,))["status"] == "uploaded"
    assert db.youtube_quota_used_today() == uploader.UPLOAD_QUOTA
    db.update("clips", cid, status="approved")
    with pytest.raises(uploader.UploadBlocked, match="already uploaded"):
        uploader.upload_clip(cid, db.all_settings())


def test_upload_blocks_high_risk_and_daily_limit(tmp_path, monkeypatch):
    cid = _clip(tmp_path, risk="high")
    with pytest.raises(uploader.UploadBlocked, match="rights"):
        uploader.upload_clip(cid, db.all_settings())
    cid2 = _clip(tmp_path / "..", rights="owned")
    s = db.all_settings()
    s["max_daily_uploads"] = 0
    with pytest.raises(uploader.UploadBlocked, match="daily"):
        uploader.upload_clip(cid2, s)


def test_learning_rewards_better_features():
    sid = db.ex("INSERT INTO sources(platform,external_id,rights_status,status) VALUES('local','l','owned','analyzed')")
    for i in range(10):
        good = i % 2 == 0
        cid = db.ex("INSERT INTO clips(source_id,start_s,end_s,duration_s,status,topic,layout,zoom_style,hook_type) "
                    "VALUES(?,0,30,?,'uploaded',?,'face_crop','punch',?)",
                    (sid, 27 if good else 42, "comedy" if good else "news", "question" if good else "statement"))
        uid = db.ex("INSERT INTO uploads(clip_id,youtube_video_id,status,uploaded_at) VALUES(?,?,'uploaded',?)",
                    (cid, f"v{i}", db.now()))
        db.ex("INSERT INTO analytics(upload_id,ts,views,likes,comments,avg_view_pct) VALUES(?,?,?,?,?,?)",
              (uid, db.now(), 50000 if good else 300, 2000 if good else 5, 50 if good else 0, 85 if good else 40))
    res = learning.recompute()
    assert res["samples"] == 10
    assert learning.feature_adjustment("topic", "comedy") > 0 > learning.feature_adjustment("topic", "news")
    assert learning.preferred("length") == "25-30s"
    assert learning.candidate_adjustment({"topic": "comedy", "hook_type": "question"}) > 0
