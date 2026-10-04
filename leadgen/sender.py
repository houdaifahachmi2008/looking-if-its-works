"""Verstuur goedgekeurde mails via SMTP, met daglimiet, pauzes en afmeldlijst."""
import datetime as dt
import smtplib
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from . import config, db


def sent_today(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM leads WHERE status='sent' AND date(sent_at)=date('now','localtime')"
    ).fetchone()[0]


def _message(lead) -> EmailMessage:
    s = config.sender()
    msg = EmailMessage()
    display = f"{s.name} – {s.company}" if s.company else s.name
    msg["From"] = formataddr((display, s.email))
    msg["To"] = lead["email"]
    msg["Subject"] = lead["subject"]
    msg["Reply-To"] = s.email
    msg["Message-ID"] = make_msgid(domain=s.email.split("@")[-1] or None)
    word = "désinscrire" if lead["lang"] == "fr" else "uitschrijven"
    msg["List-Unsubscribe"] = f"<mailto:{s.email}?subject={word}>"
    msg.set_content(lead["body"])
    return msg


def run(really_send: bool = False) -> None:
    s = config.sender()
    # website, btw-nummer, adres en bedrijfsnaam zijn optioneel
    missing = [k for k in ("name", "email", "phone") if not getattr(s, k)]
    if missing:
        raise SystemExit(f"Vul eerst je gegevens in .env in (ontbreekt: {', '.join(missing)}).")

    with db.connect() as conn:
        budget = config.MAX_EMAILS_PER_DAY - sent_today(conn)
        rows = conn.execute("SELECT * FROM leads WHERE status='approved' ORDER BY score DESC").fetchall()
        if budget <= 0:
            print("Daglimiet bereikt, morgen verder.")
            return
        if not rows:
            print("Geen goedgekeurde mails. Gebruik eerst: python -m leadgen nakijken")
            return

        smtp = None
        if really_send:
            smtp = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30)
            smtp.starttls()
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)

        done = 0
        try:
            for lead in rows[:budget]:
                if db.is_suppressed(conn, lead["email"]):
                    conn.execute("UPDATE leads SET status='skipped' WHERE id=?", (lead["id"],))
                    continue
                # nooit twee keer hetzelfde adres of domein mailen
                dup = conn.execute(
                    "SELECT 1 FROM leads WHERE status='sent' AND lower(email)=lower(?)", (lead["email"],)
                ).fetchone()
                if dup:
                    conn.execute("UPDATE leads SET status='skipped' WHERE id=?", (lead["id"],))
                    continue
                if not really_send:
                    print(f"[TEST] zou sturen naar {lead['email']:35} | {lead['subject']}")
                    continue
                smtp.send_message(_message(lead))
                conn.execute(
                    "UPDATE leads SET status='sent', sent_at=? WHERE id=?",
                    (dt.datetime.now().isoformat(timespec="seconds"), lead["id"]),
                )
                conn.commit()
                done += 1
                print(f"✓ verstuurd naar {lead['email']} ({lead['name']})")
                if done < min(budget, len(rows)):
                    time.sleep(config.SECONDS_BETWEEN_EMAILS)
        finally:
            if smtp:
                smtp.quit()
        if not really_send:
            print("\nDit was een test: er is niets verstuurd. Voeg --echt toe om echt te versturen.")
