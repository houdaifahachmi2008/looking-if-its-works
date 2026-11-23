"""OAuth 2.0 for the YouTube Data API (uploads) and YouTube Analytics API."""
from __future__ import annotations

from ..config import env

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


class NotAuthorized(RuntimeError):
    pass


def load_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = env().youtube_token_path
    if not path.exists():
        raise NotAuthorized("YouTube not authorized. Run: python -m viralbot auth-youtube")
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            path.write_text(creds.to_json())
        else:
            raise NotAuthorized("YouTube token invalid. Re-run: python -m viralbot auth-youtube")
    return creds


def authorize_interactive(port: int = 8765, open_browser: bool = True) -> str:
    from google_auth_oauthlib.flow import InstalledAppFlow

    secrets = env().youtube_client_secrets
    if not secrets.exists():
        raise NotAuthorized(f"OAuth client file not found: {secrets}. Download it from Google Cloud Console "
                            "(APIs & Services → Credentials → OAuth client ID → Desktop app).")
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
    creds = flow.run_local_server(port=port, open_browser=open_browser, prompt="consent",
                                  authorization_prompt_message="Open this URL to authorize ViralBot:\n{url}\n")
    env().youtube_token_path.write_text(creds.to_json())
    return str(env().youtube_token_path)


def is_authorized() -> bool:
    try:
        load_credentials()
        return True
    except Exception:
        return False


def youtube_client():
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=load_credentials(), cache_discovery=False)


def analytics_client():
    from googleapiclient.discovery import build
    return build("youtubeAnalytics", "v2", credentials=load_credentials(), cache_discovery=False)
