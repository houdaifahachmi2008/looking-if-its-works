import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"

try:
    from dotenv import load_dotenv

    load_dotenv(ENV_PATH, override=True)
except ImportError:  # python-dotenv is optioneel
    pass


@dataclass(frozen=True)
class Sender:
    name: str
    company: str
    email: str
    phone: str
    website: str
    vat: str
    address: str


def sender() -> Sender:
    return Sender(
        name=os.getenv("SENDER_NAME", ""),
        company=os.getenv("SENDER_COMPANY", ""),
        email=os.getenv("SENDER_EMAIL", ""),
        phone=os.getenv("SENDER_PHONE", ""),
        website=os.getenv("SENDER_WEBSITE", ""),
        vat=os.getenv("SENDER_VAT", ""),
        address=os.getenv("SENDER_ADDRESS", ""),
    )


DB_PATH = os.getenv("LEADGEN_DB", str(ROOT / "leads.db"))
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
MAX_EMAILS_PER_DAY = int(os.getenv("MAX_EMAILS_PER_DAY", "30"))
SECONDS_BETWEEN_EMAILS = int(os.getenv("SECONDS_BETWEEN_EMAILS", "90"))
USER_AGENT = "leadgen-be/1.0 (+contact: {})".format(os.getenv("SENDER_EMAIL", "unknown"))
