"""Verstuur goedgekeurde mails via SMTP, met daglimiet, pauzes en afmeldlijst."""
import datetime as dt
import smtplib
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from . import config, db


def explain_smtp_error(e: Exception) -> str:
    """Zet een SMTP-fout om in een begrijpelijke uitleg in het Nederlands."""
    host = (config.SMTP_HOST or "").lower()
    raw = str(e)
    if isinstance(e, smtplib.SMTPAuthenticationError):
        raw = e.smtp_error.decode(errors="ignore") if isinstance(e.smtp_error, bytes) else str(e.smtp_error)
    low = raw.lower()
    if "basic authentication is disabled" in low or "5.7.139" in low or "outlook" in host or "office365" in host:
        if isinstance(e, smtplib.SMTPAuthenticationError):
            return ("Outlook weigert de login. Microsoft laat programma's vaak niet meer inloggen met een "
                    "wachtwoord. Probeer een app-wachtwoord (account.microsoft.com → Beveiliging → "
                    "Geavanceerde beveiligingsopties → App-wachtwoorden). Lukt dat niet, gebruik dan Gmail "
                    "met een app-wachtwoord: antwoorden kunnen gewoon naar je Outlook-adres blijven gaan.")
    if "application-specific password required" in low or "5.7.9" in low:
        return ("Gmail vraagt een app-wachtwoord. Zet eerst tweestapsverificatie aan, maak dan een "
                "app-wachtwoord aan op myaccount.google.com/apppasswords en plak die 16 letters hier.")
    if "username and password not accepted" in low or "5.7.8" in low or "535" in low:
        if "gmail" in host or "google" in host:
            return ("Gmail accepteert de login niet. Je gewone Gmail-wachtwoord werkt hier NIET: gebruik een "
                    "app-wachtwoord (myaccount.google.com/apppasswords). Controleer ook of de gebruiker je "
                    "volledige Gmail-adres is.")
        return "Gebruikersnaam of wachtwoord wordt niet aanvaard. Controleer ze, of gebruik een app-wachtwoord."
    if isinstance(e, (TimeoutError, ConnectionRefusedError, OSError)) and not isinstance(e, smtplib.SMTPException):
        return (f"Geen verbinding met {config.SMTP_HOST}:{config.SMTP_PORT}. Controleer de servernaam en poort "
                "(587), je internet, en of je antivirus/firewall Python niet blokkeert.")
    return f"Fout van de mailserver: {raw[:250]}"


def connect_smtp() -> smtplib.SMTP:
    smtp = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30)
    smtp.ehlo()
    smtp.starttls()
    smtp.ehlo()
    smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
    return smtp


def sent_today(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM leads WHERE status='sent' AND date(sent_at)=date('now','localtime')"
    ).fetchone()[0]


def _message(lead) -> EmailMessage:
    s = config.sender()
    msg = EmailMessage()
    display = f"{s.name} – {s.company}" if s.company else s.name
    # verstuur altijd vanaf het account waarmee je inlogt (anders weigert of herschrijft de
    # mailserver de afzender); antwoorden gaan naar je eigen adres via Reply-To
    msg["From"] = formataddr((display, config.SMTP_USER or s.email))
    msg["To"] = lead["email"]
    msg["Subject"] = lead["subject"]
    msg["Reply-To"] = s.email
    msg["Message-ID"] = make_msgid(domain=s.email.split("@")[-1] or None)
    word = "désinscrire" if lead["lang"] == "fr" else "uitschrijven"
    msg["List-Unsubscribe"] = f"<mailto:{s.email}?subject={word}>"
    msg.set_content(lead["body"])
    return msg


def run(really_send: bool = False, stop=None) -> None:
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
            print("Geen goedgekeurde mails. Keur eerst mails goed.")
            return

        smtp = None
        if really_send:
            try:
                smtp = connect_smtp()
            except Exception as e:
                raise SystemExit(explain_smtp_error(e))

        done = 0
        try:
            for lead in rows[:budget]:
                if stop and stop.is_set():
                    print("Gestopt.")
                    break
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
                    if stop:
                        stop.wait(config.SECONDS_BETWEEN_EMAILS)
                    else:
                        time.sleep(config.SECONDS_BETWEEN_EMAILS)
        finally:
            if smtp:
                smtp.quit()
        if not really_send:
            print("\nDit was een test: er is niets verstuurd.")
