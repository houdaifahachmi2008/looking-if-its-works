"""Video importer.

Brings rights-cleared sources onto local disk. It NEVER touches a source whose
rights status is not processable, and it never circumvents DRM or access
controls.

Supported:
* local files (inbox or ``python -m viralbot import <file>``) – copied/linked by path
* direct HTTP(S) media URLs you control (e.g. your own storage bucket)
* platform pages (YouTube/Twitch) – only when ENABLE_YTDLP=true AND the
  source is owned/licensed/authorized/creative_commons. Note that YouTube's
  Terms of Service restrict downloading; use this only for your own channel or
  where the rights holder has authorized it. Prefer exporting your own videos
  from YouTube Studio / Twitch VOD downloads into the inbox instead.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import requests

from . import db
from .config import env
from .logging_setup import get_logger
from .media import probe
from .rights import is_processable

log = get_logger("importer")


class ImportBlocked(RuntimeError):
    pass


def _download_http(url: str, dst: Path) -> Path:
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "text/html" in ctype:
            raise ImportBlocked("URL returned an HTML page, not a media file")
        tmp = dst.with_suffix(dst.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        tmp.rename(dst)
    return dst


def _download_ytdlp(url: str, dst_dir: Path, stem: str) -> Path:
    if shutil.which("yt-dlp") is None:
        raise ImportBlocked("yt-dlp is not installed (pip install yt-dlp)")
    out_tmpl = str(dst_dir / f"{stem}.%(ext)s")
    cmd = ["yt-dlp", "--no-playlist", "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
           "--merge-output-format", "mp4", "-o", out_tmpl, "--no-progress", url]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if p.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {p.stderr[-800:]}")
    files = sorted(dst_dir.glob(f"{stem}.*"))
    files = [f for f in files if f.suffix in {".mp4", ".mkv", ".webm"}]
    if not files:
        raise RuntimeError("yt-dlp produced no file")
    return files[0]


def import_source(source: dict) -> Path:
    if not is_processable(source.get("rights_status")):
        raise ImportBlocked(f"rights status '{source.get('rights_status')}' does not allow importing")
    media_dir = env().dir("media")
    stem = f"src_{source['id']}"
    local = source.get("local_path")
    if local and Path(local).exists():
        path = Path(local)
    else:
        url = source.get("url") or ""
        host = urlparse(url).netloc.lower()
        if url.startswith("file://"):
            path = Path(urlparse(url).path)
            if not path.exists():
                raise FileNotFoundError(path)
        elif any(h in host for h in ("youtube.com", "youtu.be", "twitch.tv")):
            if not env().enable_ytdlp:
                raise ImportBlocked("platform download disabled (ENABLE_YTDLP=false). Export the video yourself "
                                    "and drop it into data/inbox, or enable yt-dlp for rights-cleared sources.")
            path = _download_ytdlp(url, media_dir, stem)
        elif url.startswith(("http://", "https://")):
            ext = Path(urlparse(url).path).suffix or ".mp4"
            path = _download_http(url, media_dir / f"{stem}{ext}")
        else:
            raise ImportBlocked("source has no importable location")
    info = probe(path)
    if not info.get("has_video"):
        raise RuntimeError("file has no video stream")
    max_min = db.get_setting("max_source_minutes") or 180
    if info["duration"] > max_min * 60:
        raise ImportBlocked(f"source longer than {max_min} min")
    db.update("sources", source["id"], local_path=str(path), duration_s=info["duration"],
              width=info.get("width"), height=info.get("height"))
    return path


def register_local_file(path: str | Path, rights_status: str = "owned", title: str | None = None,
                        topic: str = "own content", evidence: str = "", attribution: str | None = None) -> int:
    """Manually register a local file (CLI ``import`` command)."""
    from .discovery.local_inbox import file_id
    from .discovery.store import upsert_source
    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(p)
    sid, _ = upsert_source({
        "platform": "local", "external_id": file_id(p), "url": p.as_uri(), "title": title or p.stem,
        "channel_title": "own content", "topic": topic, "rights_status": rights_status,
        "rights_evidence": evidence or "Registered manually via CLI", "attribution": attribution,
        "local_path": str(p),
    })
    return sid
