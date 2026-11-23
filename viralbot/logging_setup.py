"""Logging: console + rotating file + WARNING-and-above mirrored into the DB
(so the dashboard can show processing errors)."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .config import env

_configured = False


class DBHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - trivial
        try:
            from . import db
            stage = record.name.split(".")[-1]
            msg = record.getMessage()
            if record.exc_info:
                msg += " | " + repr(record.exc_info[1])
            db.log_event(record.levelname, stage, msg)
        except Exception:
            pass


def setup_logging(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger("viralbot")
    root.setLevel(level)
    root.propagate = False

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)

    fh = RotatingFileHandler(env().dir("logs") / "viralbot.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    dh = DBHandler(level=logging.WARNING)
    root.addHandler(dh)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"viralbot.{name}")
