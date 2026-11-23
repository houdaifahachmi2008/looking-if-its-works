"""Rights & permission tracking, copyright-risk assessment and monetization notes.

Core rule enforced everywhere in the pipeline:
    content is only downloaded / processed / published when its rights status
    is one of PROCESSABLE. Everything else is stored as a *lead* only
    (trend intelligence, or a creator you could ask for permission).
"""
from __future__ import annotations

from . import db

PROCESSABLE = {"owned", "licensed", "creative_commons", "authorized"}
ALL_STATUSES = ["unknown", "owned", "licensed", "creative_commons", "authorized", "rejected"]

RISK_BY_STATUS = {
    "owned": "low",
    "licensed": "low",
    "authorized": "medium",        # written permission – keep the evidence on file
    "creative_commons": "medium",  # CC BY: attribution mandatory; reused-content policy still applies
    "unknown": "high",
    "rejected": "high",
}


def is_processable(rights_status: str | None) -> bool:
    return (rights_status or "unknown") in PROCESSABLE


def set_rights(source_id: int, new_status: str, evidence: str = "", actor: str = "system",
               license: str | None = None, attribution: str | None = None) -> None:
    if new_status not in ALL_STATUSES:
        raise ValueError(f"invalid rights status {new_status!r}")
    row = db.q1("SELECT rights_status, status FROM sources WHERE id=?", (source_id,))
    if row is None:
        raise KeyError(source_id)
    fields = {"rights_status": new_status}
    if evidence:
        fields["rights_evidence"] = evidence
    if license is not None:
        fields["license"] = license
    if attribution is not None:
        fields["attribution"] = attribution
    # A lead that becomes processable enters the import queue; a revoked one is parked.
    if new_status in PROCESSABLE and row["status"] in {"lead", "discovered", "skipped", "error"}:
        fields["status"] = "queued"
    elif new_status not in PROCESSABLE and row["status"] in {"queued", "discovered"}:
        fields["status"] = "lead"
    db.update("sources", source_id, **fields)
    db.ex(
        "INSERT INTO rights_log(source_id,ts,old_status,new_status,evidence,actor) VALUES(?,?,?,?,?,?)",
        (source_id, db.now(), row["rights_status"], new_status, evidence, actor),
    )


def attribution_line(source: dict) -> str:
    if source.get("attribution"):
        return source["attribution"]
    status = source.get("rights_status")
    if status == "owned":
        return ""
    who = source.get("channel_title") or "the original creator"
    url = source.get("url") or ""
    if status == "creative_commons":
        return f"Source: \"{source.get('title', '')}\" by {who} {url} – licensed under CC BY (creativecommons.org/licenses/by/3.0)."
    return f"Original video: {who} {url} – used with permission.".replace("  ", " ")


def assess_clip_risk(source: dict, has_commentary: bool) -> tuple[str, list[str]]:
    """Return (risk_level, warnings) for a clip produced from ``source``."""
    status = source.get("rights_status") or "unknown"
    risk = RISK_BY_STATUS.get(status, "high")
    warnings: list[str] = []
    if status not in PROCESSABLE:
        warnings.append(f"BLOCKED: rights status is '{status}'. Do not publish without owning or licensing this content.")
    if status == "creative_commons":
        warnings.append("CC BY content: attribution is mandatory and is added to the description automatically.")
        warnings.append("Monetization: YouTube's 'reused content' policy can demonetize channels that mostly "
                        "repost others' material, even when licensed. Add original commentary or analysis.")
    if status == "authorized":
        warnings.append("Keep written permission on file (rights_evidence). Confirm it covers monetized Shorts.")
        if not (source.get("rights_evidence") or "").strip():
            warnings.append("No permission evidence recorded for this source.")
            risk = "high"
    if status in {"authorized", "licensed", "creative_commons"} and not has_commentary:
        warnings.append("No original commentary attached. Subtitles/zooms/cuts alone do not make third-party "
                        "content eligible for monetization.")
    if source.get("platform") == "twitch" and status != "owned":
        warnings.append("Twitch content may include copyrighted music/game audio from the stream; check before publishing.")
    return risk, warnings


MONETIZATION_NOTES = [
    "Adding subtitles, zooms or transitions does NOT make someone else's content yours or monetizable.",
    "YouTube Partner Program 'reused content' policy: channels must add significant original commentary, "
    "educational value or creative contribution when using others' material.",
    "Keep proof of license/permission for every non-owned source (stored in rights_evidence + rights_log).",
    "Repeated copyright claims/strikes can terminate the channel. Never publish 'unknown' rights content.",
    "Do not use copyrighted background music; game audio and stream music can trigger Content ID.",
    "Uploads start as private/unlisted. Public publishing requires an explicit second approval in Settings.",
]
