"""Herkennen van e-mailadressen + Belgische regels voor ongevraagde B2B-mails.

Belgische wet (KB 4 april 2003, art. 1): je mag zonder voorafgaande toestemming mailen naar
*onpersoonlijke* adressen van rechtspersonen (info@, contact@, ...). Naar persoonlijke adressen
(jan.peeters@...) of naar eenmanszaken als natuurlijke persoon heb je in principe toestemming nodig.
Daarom versturen we standaard alleen naar 'generic' adressen; 'uncertain' moet je zelf nakijken.
"""
import re
import unicodedata

GENERIC_PREFIXES = {
    "info", "contact", "hello", "hallo", "bonjour", "welkom", "office", "kantoor", "admin",
    "administratie", "administration", "sales", "verkoop", "vente", "booking", "bookings",
    "reservatie", "reservaties", "reservation", "reservations", "reserveren", "mail", "post",
    "secretariaat", "secretariat", "service", "support", "klantendienst", "shop", "winkel",
    "boutique", "order", "orders", "bestelling", "bestellingen", "commande", "afspraak",
    "afspraken", "rdv", "team", "praktijk", "cabinet", "accueil", "onthaal", "events",
}
FREE_MAIL_DOMAINS = {
    "gmail.com", "hotmail.com", "hotmail.be", "outlook.com", "outlook.be", "live.be", "live.com",
    "telenet.be", "skynet.be", "proximus.be", "yahoo.com", "yahoo.fr", "icloud.com", "me.com",
    "scarlet.be", "msn.com", "gmx.com", "gmx.net",
}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)


def classify_email(email: str, business_name: str = "") -> str:
    local, _, domain = email.lower().partition("@")
    local_base = re.split(r"[.+_-]", local)[0]
    if local in GENERIC_PREFIXES or local_base in GENERIC_PREFIXES:
        return "generic"
    # voornaam.achternaam@ of initialen -> persoonlijk
    if re.fullmatch(r"[a-z]+[._-][a-z]+", local) or len(local) <= 3:
        return "personal"
    # bv. kapsalonlisa@gmail.com of info-achtig adres met bedrijfsnaam erin
    name_tokens = [_norm(w) for w in re.split(r"\s+", business_name) if len(_norm(w)) >= 4]
    if any(tok in _norm(local) for tok in name_tokens):
        return "uncertain" if domain in FREE_MAIL_DOMAINS else "generic"
    return "uncertain"


def extract_emails(text: str, site_domain: str | None = None) -> list[str]:
    found = []
    for m in EMAIL_RE.findall(text):
        m = m.strip(".").lower()
        if m.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
            continue
        if any(bad in m for bad in ("example.", "sentry", "wixpress", "domain.com", "@2x")):
            continue
        if m not in found:
            found.append(m)
    if site_domain:  # adressen op het eigen domein eerst
        found.sort(key=lambda e: 0 if e.endswith(site_domain) else 1)
    return found
