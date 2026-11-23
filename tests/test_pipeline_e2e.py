"""End-to-end: inbox → transcript sidecar → analysis → FFmpeg render → QC → approval."""
import json

from conftest import fake_words, make_video

from viralbot import db, pipeline
from viralbot.config import env
from viralbot.discovery import run_discovery
from viralbot.media import probe

TEXT = ("Why do ninety percent of people quit the gym? Honestly, the truth is they never had a plan! "
        "Wait, what? That's crazy! No way. I failed three times and I cried. Haha, it was hilarious! "
        "You build a system and it carries you. I love that. It changed my entire life! ")


def test_full_pipeline(tmp_path):
    inbox = env().dir("inbox")
    video = make_video(inbox / "ep1.mp4", 70)
    words = fake_words(TEXT * 3, start=2, pause_every=12, pause=1.4)
    (inbox / "ep1.words.json").write_text(json.dumps({"words": words}))
    s = db.all_settings()
    s["sources_enabled"] = {"local_inbox": True, "youtube": False, "twitch": False}
    s["max_clips_per_source"] = 1
    s["zoom_style"] = "subtle"
    assert run_discovery(s)["processable_new"] == 1
    out = pipeline.process_once(s)
    assert out["imported"] == 1 and out["transcribed"] == 1 and out["clips_created"] == 1 and out["rendered"] == 1
    clip = db.q1("SELECT * FROM clips WHERE file_path IS NOT NULL")
    info = probe(clip["file_path"])
    assert (info["width"], info["height"]) == (1080, 1920)
    assert 20 <= info["duration"] <= 45 and info["has_audio"]
    qc = json.loads(clip["qc_json"])
    assert all(v["ok"] for v in qc.values()), qc
    assert clip["status"] == "pending_approval"   # manual approval is the default
    assert clip["title"] and "#shorts" in clip["description"]
    pipeline.set_clip_decision(clip["id"], True, commentary="My take: systems beat motivation.")
    assert db.q1("SELECT status FROM clips WHERE id=?", (clip["id"],))["status"] == "approved"


def test_stale_lease_recovery():
    sid = db.ex("INSERT INTO sources(platform,external_id,rights_status,status,lease_at) "
                "VALUES('local','z','owned','transcribing','2000-01-01T00:00:00+00:00')")
    assert pipeline.release_stale_leases() == 1
    assert db.q1("SELECT status FROM sources WHERE id=?", (sid,))["status"] == "imported"


def test_worker_tick_and_heartbeat(monkeypatch):
    from viralbot import scheduler
    monkeypatch.setattr(scheduler.time, "sleep", lambda s: None)
    monkeypatch.setattr(scheduler.signal, "signal", lambda *a: None)
    db.set_setting("bot_enabled", True)
    db.set_setting("sources_enabled", {"local_inbox": True, "youtube": False, "twitch": False})
    scheduler.run_forever(max_ticks=1)
    hb = db.get_setting("worker_heartbeat")
    assert hb["state"] == "stopped" and db.get_setting("last_discovery_at")
