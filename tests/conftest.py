import os
import subprocess
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Each test gets its own data dir + database, and no real API keys."""
    monkeypatch.setenv("VIRALBOT_DATA_DIR", str(tmp_path / "data"))
    for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "YOUTUBE_API_KEY", "TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET",
              "AUTHORIZED_YOUTUBE_CHANNELS", "OWNED_YOUTUBE_CHANNELS", "AUTHORIZED_TWITCH_BROADCASTERS",
              "OLLAMA_MODEL", "ENABLE_YTDLP"):
        monkeypatch.setenv(k, "")
    monkeypatch.setenv("TRANSCRIBE_ENGINE", "none")
    from viralbot import config, db
    config.reset_env()
    db.close()
    db.init_db()
    yield
    db.close()
    config.reset_env()


def make_video(path: Path, seconds: float = 30, size: str = "1280x720", audio: bool = True) -> Path:
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s={size}:d={seconds}:r=30"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=f=220:d={seconds}:sample_rate=48000", "-af", "volume=0.3"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True)
    return path


def fake_words(text: str, start: float = 0.5, wps: float = 2.8, pause_every: int = 0, pause: float = 1.2):
    words, t = [], start
    for i, w in enumerate(text.split()):
        d = 1 / wps
        words.append({"w": w, "s": round(t, 3), "e": round(t + d * 0.85, 3)})
        t += d
        if pause_every and (i + 1) % pause_every == 0:
            t += pause
    return words
