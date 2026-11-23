"""Connection management for the dashboard's "Connections" page.

* reads/writes keys in the local ``.env`` file (never in the database, never in git)
* one cheap live test per integration
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import requests

from . import config
from .config import PROJECT_ROOT, env

ENV_FILE = PROJECT_ROOT / ".env"
EXAMPLE_FILE = PROJECT_ROOT / ".env.example"
_KEY_RE = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]*)\s*=")


def read_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def save_env_values(values: dict[str, str], path: Path = ENV_FILE) -> None:
    """Update/insert ``KEY=value`` lines in .env, keeping comments and order.
    Creates .env from .env.example on first use. Also applies the values to the
    current process so the change takes effect immediately."""
    if not path.exists():
        base = EXAMPLE_FILE.read_text(encoding="utf-8") if EXAMPLE_FILE.exists() and path == ENV_FILE else ""
        path.write_text(base, encoding="utf-8")
    lines = path.read_text(encoding="utf-8").splitlines()
    pending = {k: (v or "").strip() for k, v in values.items()}
    for i, line in enumerate(lines):
        m = _KEY_RE.match(line)
        if m and m.group(1) in pending:
            k = m.group(1)
            lines[i] = f"{k}={pending.pop(k)}"
    for k, v in pending.items():
        lines.append(f"{k}={v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    for k, v in values.items():
        os.environ[k] = (v or "").strip()
    config.reset_env()


def mask(value: str) -> str:
    if not value:
        return "—"
    return value[:4] + "…" + value[-4:] if len(value) > 10 else "••••"


# ------------------------------------------------------------------ live tests
# Each returns (ok: bool, message: str). They use the cheapest possible call.

def test_youtube_key(key: str | None = None) -> tuple[bool, str]:
    key = key or env().youtube_api_key
    if not key:
        return False, "No key set"
    try:
        r = requests.get("https://www.googleapis.com/youtube/v3/videos",
                         params={"part": "id", "id": "dQw4w9WgXcQ", "key": key}, timeout=20)
        d = r.json()
    except (requests.RequestException, ValueError) as ex:
        return False, f"Network error: {ex}"
    if "error" in d:
        return False, d["error"].get("message", "error")[:200]
    return True, "Works (1 quota unit used)"


def test_gemini(key: str | None = None) -> tuple[bool, str]:
    key = key or env().gemini_api_key
    if not key:
        return False, "No key set"
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                         headers={"x-goog-api-key": key}, params={"pageSize": 200}, timeout=20)
        d = r.json()
    except (requests.RequestException, ValueError) as ex:
        return False, f"Network error: {ex}"
    if "error" in d:
        msg = d["error"].get("message", "error")
        if "denied" in msg.lower():
            msg += " → the key must be bound to a service account with the Gemini API selected (see README)."
        return False, msg[:300]
    n = sum(1 for m in d.get("models", []) if "generateContent" in m.get("supportedGenerationMethods", []))
    return True, f"Works – {n} models available"


def test_openai(key: str | None = None) -> tuple[bool, str]:
    key = key or env().openai_api_key
    if not key:
        return False, "No key set"
    try:
        r = requests.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=20)
    except requests.RequestException as ex:
        return False, f"Network error: {ex}"
    if r.status_code == 200:
        return True, "Works (note: API usage needs prepaid credit on platform.openai.com)"
    try:
        return False, r.json().get("error", {}).get("message", f"HTTP {r.status_code}")[:200]
    except ValueError:
        return False, f"HTTP {r.status_code}"


def ollama_models(host: str | None = None) -> tuple[bool, list[str] | str]:
    host = (host or env().ollama_host).rstrip("/")
    try:
        r = requests.get(f"{host}/api/tags", timeout=5)
        r.raise_for_status()
        return True, [m["name"] for m in r.json().get("models", [])]
    except (requests.RequestException, ValueError) as ex:
        return False, f"Ollama not reachable at {host} – is it installed and running? ({type(ex).__name__})"


def test_twitch(cid: str | None = None, secret: str | None = None) -> tuple[bool, str]:
    cid, secret = cid or env().twitch_client_id, secret or env().twitch_client_secret
    if not (cid and secret):
        return False, "Client ID and secret required"
    try:
        r = requests.post("https://id.twitch.tv/oauth2/token", timeout=20,
                          data={"client_id": cid, "client_secret": secret, "grant_type": "client_credentials"})
    except requests.RequestException as ex:
        return False, f"Network error: {ex}"
    if r.status_code == 200:
        return True, "Works"
    try:
        return False, r.json().get("message", f"HTTP {r.status_code}")
    except ValueError:
        return False, f"HTTP {r.status_code}"


def youtube_channel() -> tuple[bool, str]:
    """Name of the channel ViralBot is logged in to (upload login)."""
    from .publishing.youtube_auth import NotAuthorized, youtube_client
    try:
        res = youtube_client().channels().list(part="snippet", mine=True).execute()
    except NotAuthorized as ex:
        return False, str(ex)
    except Exception as ex:
        return False, f"{type(ex).__name__}: {str(ex)[:200]}"
    items = res.get("items", [])
    if not items:
        return False, "Logged in, but this Google account has no YouTube channel"
    return True, items[0]["snippet"]["title"]


def logout_youtube() -> None:
    p = env().youtube_token_path
    if p.exists():
        p.unlink()
