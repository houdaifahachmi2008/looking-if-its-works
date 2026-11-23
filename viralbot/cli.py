"""Command line interface:  python -m viralbot <command>"""
from __future__ import annotations

import argparse
import json
import sys

from . import db
from .config import env
from .logging_setup import setup_logging


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="viralbot", description="ViralBot – rights-aware YouTube Shorts automation")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create data folders and database")
    sub.add_parser("status", help="pipeline counters + configuration check")
    sub.add_parser("discover", help="run discovery once")
    p = sub.add_parser("process", help="import → transcribe → analyze → render → QC (once)")
    p.add_argument("--upload", action="store_true", help="also upload approved clips")
    p.add_argument("--loops", type=int, default=1, help="repeat N times (drains the queue)")
    p = sub.add_parser("import", help="register a local video you own / licensed")
    p.add_argument("path")
    p.add_argument("--rights", default="owned", choices=["owned", "licensed", "authorized", "creative_commons"])
    p.add_argument("--title")
    p.add_argument("--topic", default="own content")
    p.add_argument("--evidence", default="")
    p.add_argument("--attribution")
    p = sub.add_parser("rights", help="set rights status of a source (with evidence)")
    p.add_argument("source_id", type=int)
    p.add_argument("status", choices=["unknown", "owned", "licensed", "creative_commons", "authorized", "rejected"])
    p.add_argument("--evidence", default="")
    p = sub.add_parser("approve", help="approve a clip for upload")
    p.add_argument("clip_id", type=int)
    p.add_argument("--commentary")
    p = sub.add_parser("reject", help="reject a clip")
    p.add_argument("clip_id", type=int)
    p.add_argument("--reason", default="")
    sub.add_parser("upload", help="upload approved clips now (respects daily limit)")
    sub.add_parser("analytics", help="collect YouTube stats and update learning")
    sub.add_parser("learn", help="recompute learning weights")
    p = sub.add_parser("run", help="run the bot continuously")
    p.add_argument("--enable", action="store_true", help="set bot_enabled=true before starting")
    p = sub.add_parser("auth-youtube", help="OAuth authorize your YouTube channel")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--port", type=int, default=8765)
    p = sub.add_parser("set", help="change a runtime setting, e.g. set min_viral_score 80")
    p.add_argument("key")
    p.add_argument("value", help="JSON value")
    sub.add_parser("demo", help="generate a synthetic owned demo video and run the pipeline on it")
    sub.add_parser("clips", help="list clips")

    a = ap.parse_args(argv)
    setup_logging()
    db.init_db()

    if a.cmd == "init":
        for d in ("inbox", "media", "work", "clips", "logs", "models"):
            env().dir(d)
        print(f"Initialized {env().data_dir}")
    elif a.cmd == "status":
        from .pipeline import counts
        from .publishing.youtube_auth import is_authorized
        e = env()
        _print({"counts": counts(), "config": {
            "data_dir": str(e.data_dir), "llm": e.active_llm, "transcription": e.transcribe_engine,
            "youtube_api_key": bool(e.youtube_api_key), "youtube_authorized": is_authorized(),
            "twitch": bool(e.twitch_client_id and e.twitch_client_secret), "yt_dlp_enabled": e.enable_ytdlp,
            "youtube_quota_used_today": db.youtube_quota_used_today()}})
    elif a.cmd == "discover":
        from .discovery import run_discovery
        _print(run_discovery())
    elif a.cmd == "process":
        from .pipeline import process_once
        for _ in range(a.loops):
            _print(process_once(upload=a.upload))
    elif a.cmd == "import":
        from .importer import register_local_file
        sid = register_local_file(a.path, a.rights, a.title, a.topic, a.evidence, a.attribution)
        print(f"registered source {sid}")
    elif a.cmd == "rights":
        from .rights import set_rights
        set_rights(a.source_id, a.status, a.evidence, actor="cli")
        print("ok")
    elif a.cmd == "approve":
        from .pipeline import set_clip_decision
        set_clip_decision(a.clip_id, True, commentary=a.commentary)
        print("approved")
    elif a.cmd == "reject":
        from .pipeline import set_clip_decision
        set_clip_decision(a.clip_id, False, a.reason)
        print("rejected")
    elif a.cmd == "upload":
        from .pipeline import requeue_failed_uploads, stage_upload
        requeue_failed_uploads()
        print(f"uploaded {stage_upload(db.all_settings(), limit=10)}")
    elif a.cmd == "analytics":
        from .analytics import collect
        _print(collect())
    elif a.cmd == "learn":
        from .analysis.learning import recompute
        _print(recompute())
    elif a.cmd == "run":
        from .scheduler import run_forever
        if a.enable:
            db.set_setting("bot_enabled", True)
        run_forever()
    elif a.cmd == "auth-youtube":
        from .publishing.youtube_auth import authorize_interactive
        print("token saved to", authorize_interactive(a.port, not a.no_browser))
    elif a.cmd == "set":
        try:
            val = json.loads(a.value)
        except json.JSONDecodeError:
            val = a.value
        db.set_setting(a.key, val)
        print(f"{a.key} = {json.dumps(val)}")
    elif a.cmd == "demo":
        from .demo import make_demo_video
        from .discovery import run_discovery
        from .pipeline import process_once
        path = make_demo_video()
        print(f"demo source: {path}")
        s = db.all_settings()
        s["sources_enabled"] = {"local_inbox": True, "youtube": False, "twitch": False}
        _print(run_discovery(s))
        for _ in range(3):
            _print(process_once())
        for r in db.q("SELECT id,status,adjusted_score,duration_s,title,file_path FROM clips ORDER BY id"):
            print(dict(r))
    elif a.cmd == "clips":
        for r in db.q("SELECT id,source_id,status,viral_score,adjusted_score,duration_s,title,risk_level FROM clips ORDER BY id DESC LIMIT 50"):
            print(dict(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
