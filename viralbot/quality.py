"""Automated quality control + duplicate detection + copyright-risk gate."""
from __future__ import annotations

import json

from . import db
from .logging_setup import get_logger
from .media import black_ratio, loudness_stats, phash_distance, probe, sha256_file, video_phash
from .rights import assess_clip_risk, is_processable

log = get_logger("quality")
DUP_PHASH_DISTANCE = 8.0  # mean Hamming distance (0-64) below which two clips are "the same"


def check_clip(clip: dict, source: dict, settings: dict) -> dict:
    path = clip["file_path"]
    info = probe(path)
    checks: dict[str, dict] = {}

    def add(name, ok, detail, blocking=True):
        checks[name] = {"ok": bool(ok), "detail": detail, "blocking": blocking}

    d = info["duration"]
    add("duration", settings["min_clip_seconds"] - 0.5 <= d <= settings["max_clip_seconds"] + 0.5, f"{d:.1f}s")
    add("vertical_1080x1920", info.get("width") == 1080 and info.get("height") == 1920,
        f"{info.get('width')}x{info.get('height')}")
    add("has_audio", info["has_audio"], info.get("acodec", "none"))
    loud = loudness_stats(path)
    mv = loud.get("mean_volume", -99)
    add("audio_level", -30 <= mv <= -6, f"mean {mv} dB, max {loud.get('max_volume')} dB")
    br = black_ratio(path, d)
    add("not_black", br < 0.2, f"{br:.0%} black")
    has_words = bool((clip.get("transcript_text") or "").strip())
    add("subtitles", has_words, "speech transcript present" if has_words else "no speech – no subtitles",
        blocking=False)
    add("rights_cleared", is_processable(source.get("rights_status")), source.get("rights_status"))
    add("viral_score", (clip.get("adjusted_score") or 0) >= settings["min_viral_score"],
        f"{clip.get('adjusted_score')} ≥ {settings['min_viral_score']}")

    # duplicates: identical file or perceptually similar to any approved / uploaded clip
    content_hash = sha256_file(path)
    ph = video_phash(path)
    dup_of = None
    for r in db.q("SELECT id, content_hash, phash FROM clips WHERE id!=? AND status IN "
                  "('pending_approval','approved','uploading','uploaded')", (clip["id"],)):
        if r["content_hash"] == content_hash or phash_distance(ph, r["phash"] or "") < DUP_PHASH_DISTANCE:
            dup_of = r["id"]
            break
    if dup_of is None:  # same moment of the same source, different framing
        from .analysis.segmenter import overlap
        for r in db.q("SELECT id, start_s, end_s FROM clips WHERE id!=? AND source_id=? AND status IN "
                      "('pending_approval','approved','uploading','uploaded')", (clip["id"], clip["source_id"])):
            if overlap((clip["start_s"], clip["end_s"]), (r["start_s"], r["end_s"])) > 0.5:
                dup_of = r["id"]
                break
    add("not_duplicate", dup_of is None, f"duplicate of clip {dup_of}" if dup_of else "unique")
    # same uploaded hash
    if db.q1("SELECT 1 FROM uploads WHERE content_hash=?", (content_hash,)):
        add("not_already_uploaded", False, "identical file already uploaded")

    risk, warnings = assess_clip_risk(source, bool((clip.get("commentary") or "").strip()))
    passed = all(c["ok"] for c in checks.values() if c["blocking"])
    return {"passed": passed, "checks": checks, "risk": risk, "warnings": warnings,
            "content_hash": content_hash, "phash": ph}


def apply(clip_id: int, settings: dict) -> dict:
    clip = dict(db.q1("SELECT * FROM clips WHERE id=?", (clip_id,)))
    source = dict(db.q1("SELECT * FROM sources WHERE id=?", (clip["source_id"],)))
    res = check_clip(clip, source, settings)
    if not res["passed"]:
        status = "qc_failed"
    elif res["risk"] == "high":
        status = "pending_approval"  # never auto-approve high risk (and uploader re-checks rights)
    elif settings.get("require_manual_approval", True):
        status = "pending_approval"
    elif res["risk"] == "medium" and not settings.get("auto_approve_medium_risk", False):
        status = "pending_approval"
    else:
        status = "approved"
    db.update("clips", clip_id, status=status, qc_json=json.dumps(res["checks"]), risk_level=res["risk"],
              warnings_json=json.dumps(res["warnings"]), content_hash=res["content_hash"], phash=res["phash"])
    failed = [k for k, v in res["checks"].items() if not v["ok"] and v["blocking"]]
    log.info("clip %s QC %s → %s%s", clip_id, "passed" if res["passed"] else "FAILED", status,
             f" ({', '.join(failed)})" if failed else "")
    return {**res, "status": status}
