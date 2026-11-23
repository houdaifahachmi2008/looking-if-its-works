"""Configuration.

Two layers:

1. ``Env`` – secrets and paths, read from environment variables / ``.env``.
   Never stored in the database, never shown in the dashboard.
2. Runtime settings – tunable behaviour (topics, thresholds, upload limits...).
   Defaults live in ``DEFAULT_SETTINGS``; overrides are stored in the SQLite
   ``settings`` table so the dashboard and the worker share them live.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if load_dotenv is not None:
    load_dotenv(PROJECT_ROOT / ".env")


def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str) -> list[str]:
    v = os.getenv(name, "")
    return [x.strip() for x in v.split(",") if x.strip()]


@dataclass
class Env:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("VIRALBOT_DATA_DIR", PROJECT_ROOT / "data")).resolve())

    # AI providers
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "auto").lower())  # auto|gemini|openai|none
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    # USD per 1M tokens (input, output). Override when provider pricing changes.
    llm_price_in: float = field(default_factory=lambda: float(os.getenv("LLM_PRICE_INPUT_PER_M", "0.30")))
    llm_price_out: float = field(default_factory=lambda: float(os.getenv("LLM_PRICE_OUTPUT_PER_M", "2.50")))

    # Transcription
    transcribe_engine: str = field(default_factory=lambda: os.getenv("TRANSCRIBE_ENGINE", "faster-whisper").lower())  # faster-whisper|openai|none
    whisper_model: str = field(default_factory=lambda: os.getenv("WHISPER_MODEL", "base"))
    whisper_device: str = field(default_factory=lambda: os.getenv("WHISPER_DEVICE", "cpu"))
    whisper_compute_type: str = field(default_factory=lambda: os.getenv("WHISPER_COMPUTE_TYPE", "int8"))
    openai_whisper_price_per_min: float = field(default_factory=lambda: float(os.getenv("OPENAI_WHISPER_PRICE_PER_MIN", "0.006")))

    # YouTube
    youtube_api_key: str = field(default_factory=lambda: os.getenv("YOUTUBE_API_KEY", ""))
    youtube_client_secrets: Path = field(default_factory=lambda: Path(os.getenv("YOUTUBE_CLIENT_SECRETS", PROJECT_ROOT / "client_secret.json")))
    youtube_daily_quota: int = field(default_factory=lambda: int(os.getenv("YOUTUBE_DAILY_QUOTA", "10000")))

    # Twitch
    twitch_client_id: str = field(default_factory=lambda: os.getenv("TWITCH_CLIENT_ID", ""))
    twitch_client_secret: str = field(default_factory=lambda: os.getenv("TWITCH_CLIENT_SECRET", ""))

    # Rights: channels / broadcasters you own or have written permission from
    authorized_youtube_channels: list[str] = field(default_factory=lambda: _list("AUTHORIZED_YOUTUBE_CHANNELS"))
    authorized_twitch_broadcasters: list[str] = field(default_factory=lambda: [x.lower() for x in _list("AUTHORIZED_TWITCH_BROADCASTERS")])
    owned_youtube_channels: list[str] = field(default_factory=lambda: _list("OWNED_YOUTUBE_CHANNELS"))

    # Remote media import (yt-dlp) is opt-in and only ever used for rights-cleared items.
    enable_ytdlp: bool = field(default_factory=lambda: _bool("ENABLE_YTDLP", False))

    subtitle_font: str = field(default_factory=lambda: os.getenv("SUBTITLE_FONT", "Inter Black"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "viralbot.db"

    @property
    def youtube_token_path(self) -> Path:
        return self.data_dir / "youtube_token.json"

    def dir(self, name: str) -> Path:
        p = self.data_dir / name
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def active_llm(self) -> str:
        if self.llm_provider == "none":
            return "none"
        if self.llm_provider == "gemini":
            return "gemini" if self.gemini_api_key else "none"
        if self.llm_provider == "openai":
            return "openai" if self.openai_api_key else "none"
        if self.gemini_api_key:
            return "gemini"
        if self.openai_api_key:
            return "openai"
        return "none"


_env: Env | None = None


def env() -> Env:
    global _env
    if _env is None:
        _env = Env()
        _env.data_dir.mkdir(parents=True, exist_ok=True)
    return _env


def reset_env() -> None:
    """Re-read environment (used by tests)."""
    global _env
    _env = None


DEFAULT_SETTINGS: dict = {
    # --- bot control
    "bot_enabled": False,
    # --- discovery
    "sources_enabled": {"youtube": True, "twitch": True, "local_inbox": True},
    "topics": [
        "podcast funny moment",
        "streamer reaction",
        "gaming highlight",
        "motivational speech",
        "interview surprising",
        "debate discussion",
    ],
    "youtube_creative_commons_only": True,   # only CC-BY videos are processable when discovered via search
    "discovery_interval_hours": 4,
    "discovery_lookback_days": 7,
    "discovery_max_results_per_topic": 10,
    "twitch_game_names": ["Just Chatting", "Fortnite", "Minecraft"],
    # --- analysis
    "min_viral_score": 75,
    "max_clips_per_source": 3,
    "min_clip_seconds": 20,
    "max_clip_seconds": 45,
    "target_clip_seconds": 32,
    "max_source_minutes": 180,
    # --- editing
    "zoom_style": "punch",          # none | subtle | punch
    "layout": "auto",               # auto | face_crop | blur_fill
    "remove_silence": True,
    "max_pause_seconds": 0.45,
    "subtitle_words_per_line": 3,
    "add_hook_banner": True,
    # --- publishing
    "require_manual_approval": True,
    "auto_approve_medium_risk": False,
    "max_daily_uploads": 3,
    "default_privacy": "private",    # private | unlisted | public
    "public_publishing_approved": False,  # second gate: must be explicitly enabled to go public
    "schedule_uploads": False,
    "upload_slots_utc": ["15:00", "18:00", "21:00"],
    "set_custom_thumbnail": False,
    "default_category_id": "24",     # Entertainment
    # --- analytics / learning
    "analytics_interval_hours": 6,
    "learning_min_samples": 5,
    "learning_max_adjustment": 8,
}

# YouTube video categories (US region IDs that accept uploads)
YOUTUBE_CATEGORIES = {
    "1": "Film & Animation", "2": "Autos & Vehicles", "10": "Music", "15": "Pets & Animals",
    "17": "Sports", "19": "Travel & Events", "20": "Gaming", "22": "People & Blogs",
    "23": "Comedy", "24": "Entertainment", "25": "News & Politics", "26": "Howto & Style",
    "27": "Education", "28": "Science & Technology", "29": "Nonprofits & Activism",
}
