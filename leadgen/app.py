"""Sitevo-dashboard: een lokale app in je browser. Start met: python -m leadgen.app"""
import contextlib
import csv
import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import audit, config, db, finder, sender, writer

HOST, PORT = "127.0.0.1", 8765
STATIC = Path(__file__).resolve().parent / "static"

SETTINGS_FIELDS = [
    "SENDER_NAME", "SENDER_COMPANY", "SENDER_EMAIL", "SENDER_PHONE", "SENDER_WEBSITE",
    "SENDER_VAT", "SENDER_ADDRESS", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD",
    "ANTHROPIC_API_KEY", "WRITER_MODE", "MAX_EMAILS_PER_DAY", "SECONDS_BETWEEN_EMAILS",
]
SECRET_FIELDS = {"SMTP_PASSWORD", "ANTHROPIC_API_KEY"}


# ------------------------------------------------------------------ achtergrondtaken

class _LogWriter(io.TextIOBase):
    def __init__(self, job):
        self.job = job
        self.buf = ""

    def write(self, s):
        self.buf += s
        *lines, self.buf = self.buf.split("\n")
        for line in lines:
            if line.strip():
                self.job["log"].append(line.rstrip())
        return len(s)

    def flush(self):
        if self.buf.strip():
            self.job["log"].append(self.buf.rstrip())
        self.buf = ""


JOB = {"running": False, "name": None, "log": [], "started": None, "finished": None}
STOP = threading.Event()
_job_lock = threading.Lock()


def start_job(name: str, fn) -> bool:
    with _job_lock:
        if JOB["running"]:
            return False
        STOP.clear()
        JOB.update(running=True, name=name, log=[], started=time.time(), finished=None)

    def runner():
        out = _LogWriter(JOB)
        with contextlib.redirect_stdout(out):
            try:
                fn()
                print("✓ Klaar." if not STOP.is_set() else "■ Gestopt.")
            except SystemExit as e:
                print(f"⚠ {e}")
            except Exception as e:  # toon fouten in de app i.p.v. te crashen
                print(f"⚠ Fout: {type(e).__name__}: {e}")
        out.flush()
        JOB.update(running=False, finished=time.time())

    threading.Thread(target=runner, daemon=True).start()
    return True


def _job_find(cities, categories):
    def fn():
        for city in cities:
            for cat in categories:
                if STOP.is_set():
                    return
                print(f"Zoeken: {cat} in {city} ...")
                try:
                    total, new = finder.find(city, cat)
                    print(f"  {total} gevonden, {new} nieuw")
                except Exception as e:
                    print(f"  ⚠ {e}")
    return fn


# ------------------------------------------------------------------ gegevens

def stats() -> dict:
    with db.connect() as conn:
        q = lambda sql, *a: conn.execute(sql, a).fetchone()[0]
        by_status = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM leads GROUP BY status")}
        by_situation = {r[0] or "onbekend": r[1] for r in conn.execute(
            "SELECT situation, COUNT(*) FROM leads WHERE situation IS NOT NULL GROUP BY situation")}
        by_category = [dict(r) for r in conn.execute(
            "SELECT category, COUNT(*) n, SUM(situation IN ('no_website','facebook_only','outdated','unreachable')) hot "
            "FROM leads GROUP BY category ORDER BY n DESC")]
        recent = [dict(r) for r in conn.execute(
            "SELECT id, name, email, subject, sent_at FROM leads WHERE status='sent' ORDER BY sent_at DESC LIMIT 8")]
        per_day = [dict(r) for r in conn.execute(
            "SELECT date(sent_at) d, COUNT(*) n FROM leads WHERE status='sent' "
            "AND date(sent_at) >= date('now','-13 day') GROUP BY d ORDER BY d")]
        return {
            "total": q("SELECT COUNT(*) FROM leads"),
            "with_email": q("SELECT COUNT(*) FROM leads WHERE email IS NOT NULL"),
            "missing_email": q("SELECT COUNT(*) FROM leads WHERE status='audited' AND email IS NULL"),
            "hot": q("SELECT COUNT(*) FROM leads WHERE score >= 60"),
            "by_status": by_status,
            "by_situation": by_situation,
            "by_category": by_category,
            "sent_today": sender.sent_today(conn),
            "daily_limit": config.MAX_EMAILS_PER_DAY,
            "recent": recent,
            "per_day": per_day,
            "suppressed": q("SELECT COUNT(*) FROM suppression"),
            "claude": writer.use_claude_default(),
            "has_api_key": bool(os.getenv("ANTHROPIC_API_KEY")),
            "ready_by_type": {t: n for t, n in _ready_counts(conn).items()},
            "settings_ok": all(getattr(config.sender(), k) for k in ("name", "email", "phone")),
            "smtp_ok": bool(config.SMTP_HOST and config.SMTP_USER and config.SMTP_PASSWORD),
        }


def _ready_counts(conn) -> dict:
    counts = {t: 0 for t in writer.TYPES}
    for lead in writer.ready_leads(conn):
        counts[writer.template_type(lead)] += 1
    return counts


def templates_payload() -> dict:
    with db.connect() as conn:
        ready = _ready_counts(conn)
        tpls = {t: {lang: writer.get_template(conn, t, lang) for lang in ("nl", "fr")} for t in writer.TYPES}
    return {
        "types": [{"key": k, **v, "ready": ready[k]} for k, v in writer.TYPES.items()],
        "templates": tpls,
        "placeholders": writer.PLACEHOLDERS,
    }


SAMPLE_LEADS = {
    "no_website": {"name": "Bakkerij Janssens", "category": "bakker", "city": "Gent", "situation": "no_website",
                   "issues": '["geen website gevonden"]', "website": None},
    "bad_website": {"name": "Kapsalon Lisa", "category": "kapper", "city": "Aalst", "situation": "outdated",
                    "issues": json.dumps(["geen beveiligde verbinding (https)",
                                          "niet geoptimaliseerd voor gsm (geen responsive design)",
                                          "copyright-vermelding staat nog op 2016 (site lijkt niet onderhouden)"]),
                    "website": "http://kapsalonlisa.be"},
    "could_be_better": {"name": "Garage Claes", "category": "garage", "city": "Mechelen", "situation": "improvable",
                        "issues": json.dumps(["geen meta-beschrijving (slecht voor Google)",
                                              "telefoonnummer is niet aanklikbaar op gsm"]),
                        "website": "https://garageclaes.be"},
}


def preview_template(d: dict) -> dict:
    ttype, lang = d.get("type"), d.get("lang", "nl")
    if ttype not in writer.TYPES:
        return {"error": "Onbekend type"}
    with db.connect() as conn:
        real = [l for l in conn.execute("SELECT * FROM leads WHERE situation IS NOT NULL ORDER BY score DESC LIMIT 500")
                if writer.template_type(l) == ttype and (l["lang"] or "nl") == lang]
    lead = dict(real[0]) if real else {**SAMPLE_LEADS[ttype], "lang": lang}
    subject, body = writer.render(lead, {"subject": d.get("subject", ""), "body": d.get("body", "")})
    return {"subject": subject, "body": body + writer.footer(lang), "example": lead["name"], "real": bool(real)}


def list_leads(params: dict) -> list[dict]:
    sql = ("SELECT id, name, category, city, postcode, phone, email, email_kind, website, situation, issues, score, "
           "status, subject, sent_at FROM leads WHERE 1=1")
    args = []
    for field in ("status", "situation", "category"):
        if params.get(field):
            sql += f" AND {field}=?"
            args.append(params[field])
    if params.get("q"):
        sql += " AND (name LIKE ? OR city LIKE ? OR email LIKE ?)"
        args += [f"%{params['q']}%"] * 3
    if params.get("has_email") == "1":
        sql += " AND email IS NOT NULL"
    sql += " ORDER BY score DESC, name LIMIT 2000"
    with db.connect() as conn:
        rows = []
        for r in conn.execute(sql, args):
            d = dict(r)
            d["mail_type"] = writer.template_type(r) if r["situation"] else None
            del d["issues"]
            rows.append(d)
        return rows


def get_lead(lead_id: int) -> dict | None:
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["mail_type"] = writer.template_type(r) if r["situation"] else None
        d["issues"] = json.loads(d["issues"] or "[]")
        d["suppressed"] = bool(d["email"]) and db.is_suppressed(conn, d["email"])
        return d


def update_lead(lead_id: int, data: dict) -> dict:
    with db.connect() as conn:
        lead = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not lead:
            return {"error": "Niet gevonden"}
        for field in ("subject", "body", "email", "lang", "phone"):
            if field in data:
                conn.execute(f"UPDATE leads SET {field}=? WHERE id=?", (data[field], lead_id))
        if "email" in data and data["email"]:
            from .emails import classify_email
            conn.execute("UPDATE leads SET email_kind=? WHERE id=?",
                         (classify_email(data["email"], lead["name"]), lead_id))
        if data.get("status"):
            new = data["status"]
            kind = conn.execute("SELECT email_kind FROM leads WHERE id=?", (lead_id,)).fetchone()[0]
            if new == "approved" and kind != "generic" and not data.get("consent"):
                return {"error": "Dit lijkt een persoonlijk adres. Bevestig eerst dat je toestemming hebt."}
            conn.execute("UPDATE leads SET status=? WHERE id=?", (new, lead_id))
    return {"ok": True, "lead": get_lead(lead_id)}


def rewrite_lead(lead_id: int) -> dict:
    with db.connect() as conn:
        lead = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not lead:
            return {"error": "Niet gevonden"}
        if lead["status"] == "new":
            return {"error": "Controleer dit bedrijf eerst."}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            writer.draft_one(conn, lead)
    return {"ok": True, "lead": get_lead(lead_id), "log": buf.getvalue()}


def read_settings() -> dict:
    out = {}
    for k in SETTINGS_FIELDS:
        v = os.getenv(k, "")
        out[k] = ("•" * 8 if v else "") if k in SECRET_FIELDS else v
    return out


def save_settings(data: dict) -> dict:
    current = {k: os.getenv(k, "") for k in SETTINGS_FIELDS}
    for k in SETTINGS_FIELDS:
        if k not in data:
            continue
        v = str(data[k]).strip()
        if k in SECRET_FIELDS:
            if v and set(v) == {"•"}:
                continue  # niet gewijzigd
            v = v.replace("•", "")  # per ongeluk meegetypte puntjes
        current[k] = v
    host = current.get("SMTP_HOST", "").lower()
    if "gmail" in host or "google" in host:
        current["SMTP_PASSWORD"] = current["SMTP_PASSWORD"].replace(" ", "")  # app-wachtwoorden hebben geen spaties
    if not current.get("SMTP_USER") and current.get("SENDER_EMAIL"):
        current["SMTP_USER"] = current["SENDER_EMAIL"]

    def quote(v: str) -> str:
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = ["# Ingesteld via de Sitevo-app"] + [f"{k}={quote(v)}" for k, v in current.items()]
    config.ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for k, v in current.items():
        os.environ[k] = v
    importlib.reload(config)
    return {"ok": True, "settings": read_settings()}


def test_smtp() -> dict:
    if not (config.SMTP_HOST and config.SMTP_USER and config.SMTP_PASSWORD):
        return {"ok": False, "message": "Vul eerst server, gebruiker en wachtwoord in."}
    try:
        sender.connect_smtp().quit()
        return {"ok": True, "message": "Verbinding gelukt! Je kan mails versturen."}
    except Exception as e:
        return {"ok": False, "message": sender.explain_smtp_error(e)}


def send_test_mail(to: str) -> dict:
    """Stuurt één voorbeeldmail naar jezelf om te zien hoe het eruitziet."""
    from email.message import EmailMessage
    lead = {**SAMPLE_LEADS["no_website"], "lang": "nl"}
    subject, body = writer._template(lead)
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = config.SMTP_USER, to, "[TEST] " + subject
    msg["Reply-To"] = config.sender().email or config.SMTP_USER
    msg.set_content(body + writer.footer("nl"))
    try:
        smtp = sender.connect_smtp()
        smtp.send_message(msg)
        smtp.quit()
        return {"ok": True, "message": f"Testmail verstuurd naar {to}. Kijk ook in je spam."}
    except Exception as e:
        return {"ok": False, "message": sender.explain_smtp_error(e)}


def export_csv() -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    cols = ["name", "category", "street", "postcode", "city", "phone", "email", "email_kind",
            "website", "situation", "score", "issues", "status", "sent_at"]
    w.writerow(cols)
    with db.connect() as conn:
        for r in conn.execute("SELECT * FROM leads ORDER BY score DESC"):
            w.writerow([", ".join(json.loads(r[c] or "[]")) if c == "issues" else r[c] for c in cols])
    return ("﻿" + buf.getvalue()).encode("utf-8")


# ------------------------------------------------------------------ http

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # stil
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def _local_only(self) -> bool:
        # bescherming tegen andere websites die je lokale app proberen aan te spreken
        origin = self.headers.get("Origin")
        return origin in (None, f"http://{HOST}:{PORT}", f"http://localhost:{PORT}")

    def do_GET(self):
        url = urlparse(self.path)
        p = {k: v[0] for k, v in parse_qs(url.query).items()}
        path = url.path
        if path in ("/", "/index.html"):
            return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/api/stats":
            return self._send(200, stats())
        if path == "/api/leads":
            return self._send(200, list_leads(p))
        if path.startswith("/api/leads/"):
            lead = get_lead(int(path.rsplit("/", 1)[1]))
            return self._send(200 if lead else 404, lead or {"error": "Niet gevonden"})
        if path == "/api/job":
            return self._send(200, JOB)
        if path == "/api/templates":
            return self._send(200, templates_payload())
        if path == "/api/categories":
            return self._send(200, list(finder.CATEGORIES))
        if path == "/api/settings":
            return self._send(200, read_settings())
        if path == "/api/suppression":
            with db.connect() as conn:
                return self._send(200, [dict(r) for r in conn.execute(
                    "SELECT * FROM suppression ORDER BY created_at DESC")])
        if path == "/api/export.csv":
            return self._send(200, export_csv(), "text/csv; charset=utf-8",
                              {"Content-Disposition": 'attachment; filename="sitevo-leads.csv"'})
        self._send(404, {"error": "Niet gevonden"})

    def do_POST(self):
        if not self._local_only():
            return self._send(403, {"error": "Niet toegestaan"})
        path = urlparse(self.path).path
        d = self._json()
        busy = {"error": "Er loopt al een taak. Wacht tot die klaar is of stop ze."}

        if path == "/api/jobs/find":
            cities = [c.strip() for c in d.get("cities", []) if c.strip()]
            cats = [c for c in d.get("categories", []) if c in finder.CATEGORIES]
            if not cities or not cats:
                return self._send(400, {"error": "Kies minstens één gemeente en één sector."})
            ok = start_job("Bedrijven zoeken", _job_find(cities, cats))
            return self._send(200 if ok else 409, {"ok": True} if ok else busy)
        if path == "/api/jobs/audit":
            ok = start_job("Websites controleren", lambda: audit.run(int(d.get("limit", 100)), STOP))
            return self._send(200 if ok else 409, {"ok": True} if ok else busy)
        if path == "/api/jobs/rescan":
            ok = start_job("E-mails zoeken", lambda: audit.rescan(int(d.get("limit", 200)), STOP))
            return self._send(200 if ok else 409, {"ok": True} if ok else busy)
        if path == "/api/jobs/write":
            types = [t for t in d.get("types", list(writer.TYPES)) if t in writer.TYPES]
            if not types:
                return self._send(400, {"error": "Kies minstens één soort mail."})
            ok = start_job("Mails schrijven",
                           lambda: writer.run(int(d.get("min_score", 0)), int(d.get("limit", 50)), STOP, types))
            return self._send(200 if ok else 409, {"ok": True} if ok else busy)
        if path == "/api/jobs/send":
            real = bool(d.get("real"))
            ok = start_job("Mails versturen" if real else "Test versturen", lambda: sender.run(real, STOP))
            return self._send(200 if ok else 409, {"ok": True} if ok else busy)
        if path == "/api/jobs/stop":
            STOP.set()
            return self._send(200, {"ok": True})
        if path == "/api/approve-all":
            with db.connect() as conn:
                n = conn.execute("UPDATE leads SET status='approved' WHERE status='drafted' "
                                 "AND email_kind='generic' AND score>=?", (int(d.get("min_score", 30)),)).rowcount
            return self._send(200, {"ok": True, "count": n})
        if path.startswith("/api/leads/") and path.endswith("/rewrite"):
            return self._send(200, rewrite_lead(int(path.split("/")[3])))
        if path.startswith("/api/leads/"):
            res = update_lead(int(path.rsplit("/", 1)[1]), d)
            return self._send(400 if "error" in res else 200, res)
        if path == "/api/settings":
            return self._send(200, save_settings(d))
        if path == "/api/settings/test-smtp":
            return self._send(200, test_smtp())
        if path == "/api/templates":
            ttype, lang = d.get("type"), d.get("lang")
            if ttype not in writer.TYPES or lang not in ("nl", "fr"):
                return self._send(400, {"error": "Onbekend sjabloon"})
            if not (d.get("subject") or "").strip() or not (d.get("body") or "").strip():
                return self._send(400, {"error": "Onderwerp en bericht mogen niet leeg zijn."})
            with db.connect() as conn:
                writer.save_template(conn, ttype, lang, d["subject"], d["body"])
            return self._send(200, templates_payload())
        if path == "/api/templates/reset":
            with db.connect() as conn:
                writer.reset_template(conn, d.get("type"), d.get("lang"))
            return self._send(200, templates_payload())
        if path == "/api/templates/preview":
            res = preview_template(d)
            return self._send(400 if "error" in res else 200, res)
        if path == "/api/templates/apply":
            if d.get("type") not in writer.TYPES:
                return self._send(400, {"error": "Onbekend type"})
            return self._send(200, {"ok": True, "count": writer.reapply(d["type"])})
        if path == "/api/settings/test-mail":
            return self._send(200, send_test_mail(d.get("to") or config.SMTP_USER))
        if path == "/api/suppression":
            value = (d.get("value") or "").strip()
            if not value or ("@" not in value):
                return self._send(400, {"error": "Geef een e-mailadres of @domein in."})
            with db.connect() as conn:
                db.suppress(conn, value)
            return self._send(200, {"ok": True})
        if path == "/api/suppression/delete":
            with db.connect() as conn:
                conn.execute("DELETE FROM suppression WHERE value=?", (d.get("value", ""),))
            return self._send(200, {"ok": True})
        self._send(404, {"error": "Niet gevonden"})


def _open_window(url: str):
    """Open als app-venster (Edge/Chrome zonder adresbalk), anders gewone browser."""
    if sys.platform == "win32":
        candidates = [
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ]
        for exe in candidates:
            if os.path.exists(exe):
                subprocess.Popen([exe, f"--app={url}", "--window-size=1400,900"])
                return
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
        exe = shutil.which(name)
        if exe:
            subprocess.Popen([exe, f"--app={url}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    webbrowser.open(url)


def main():
    with db.connect():
        pass  # maakt de database aan als ze nog niet bestaat
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        print("De app draait al. Ik open het venster opnieuw.")
        _open_window(f"http://{HOST}:{PORT}")
        return
    url = f"http://{HOST}:{PORT}"
    print(f"Sitevo draait op {url}\nLaat dit venster open zolang je de app gebruikt. Sluiten = app stoppen.")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, _open_window, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
