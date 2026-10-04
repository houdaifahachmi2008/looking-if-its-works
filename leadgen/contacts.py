"""Grondiger zoeken naar e-mailadressen en websites.

- doorzoekt meerdere pagina's per website (contact, over ons, privacy, colofon, ...)
- ontcijfert adressen die tegen spam verstopt zijn: Cloudflare-beveiliging, HTML-codes,
  'info [at] bedrijf [dot] be', 'info(at)bedrijf.be', schema.org-gegevens
- zoekt de eigen website van bedrijven waarvoor OpenStreetMap er geen kent (domeinnaam raden
  en controleren of de naam en gemeente van het bedrijf op die site staan)
"""
import html as htmlmod
import re
import unicodedata
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from . import config
from .emails import FREE_MAIL_DOMAINS, GENERIC_PREFIXES, extract_emails

CONTACT_HINTS = (
    "contact", "contacteer", "contacteer-ons", "contactez", "nous-contacter", "over-ons", "over ons",
    "about", "a-propos", "wie-zijn-we", "qui-sommes", "impressum", "colofon", "mentions", "legal",
    "juridisch", "privacy", "disclaimer", "afspraak", "reserv", "rendez-vous", "team", "openingsuren",
)
COMMON_PATHS = (
    "/contact", "/contact/", "/contact.html", "/contacteer-ons", "/nl/contact", "/fr/contact",
    "/contactez-nous", "/over-ons", "/privacy", "/privacyverklaring", "/mentions-legales", "/colofon",
)
OBFUSCATED = re.compile(
    r"([A-Za-z0-9._%+-]+)\s*(?:\[\s*at\s*\]|\(\s*at\s*\)|\{\s*at\s*\}|\s+at\s+|\[@\]|\(@\))\s*"
    r"([A-Za-z0-9-]+(?:\s*(?:\.|\[\s*(?:dot|punt|point)\s*\]|\(\s*(?:dot|punt|point)\s*\))\s*[A-Za-z0-9-]+)+)",
    re.I,
)
LEGAL_SUFFIXES = {"bv", "bvba", "nv", "vof", "cv", "comm", "srl", "sprl", "sa", "scs", "snc", "vzw", "asbl", "bvba."}
GENERIC_WORDS = {
    "de", "het", "den", "der", "la", "le", "les", "du", "des", "en", "et", "&", "van", "bij", "chez", "and",
    "the", "t", "s", "d", "l",
}


def _get(url: str, timeout: int = 10):
    return requests.get(url, timeout=timeout, allow_redirects=True, headers={"User-Agent": config.USER_AGENT})


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def decode_cfemail(hexstr: str) -> str | None:
    """Cloudflare 'email protection': eerste byte is de sleutel, de rest is XOR-gecodeerd."""
    try:
        data = bytes.fromhex(hexstr)
        return "".join(chr(b ^ data[0]) for b in data[1:])
    except ValueError:
        return None


def emails_in_page(raw_html: str, site_domain: str | None = None) -> list[str]:
    text = htmlmod.unescape(raw_html)  # &#64; -> @, &#105;nfo -> info
    found = extract_emails(text, site_domain)
    for hexstr in re.findall(r'data-cfemail="([0-9a-fA-F]+)"', raw_html) + re.findall(
            r"/cdn-cgi/l/email-protection#([0-9a-fA-F]+)", raw_html):
        e = decode_cfemail(hexstr)
        if e and "@" in e:
            found += extract_emails(e)
    plain = BeautifulSoup(text, "html.parser").get_text(" ")
    for user, domain in OBFUSCATED.findall(plain):
        domain = re.sub(r"\s*(?:\[\s*(?:dot|punt|point)\s*\]|\(\s*(?:dot|punt|point)\s*\))\s*", ".", domain, flags=re.I)
        domain = re.sub(r"\s*\.\s*", ".", domain)
        found += extract_emails(f"{user}@{domain}")
    return list(dict.fromkeys(found))


def rank_emails(emails: list[str], site_domain: str | None) -> list[str]:
    def key(e: str):
        local, _, dom = e.partition("@")
        own = 0 if site_domain and (dom == site_domain or dom.endswith("." + site_domain)) else 1
        generic = 0 if local.split(".")[0] in GENERIC_PREFIXES else 1
        free = 1 if dom in FREE_MAIL_DOMAINS else 0
        return (own, free, generic)
    # adressen van webbouwers/platformen eruit
    bad = ("wix", "squarespace", "wordpress", "sentry", "example", "jouwdomein", "domein.be", "yourdomain",
           "email.com", "godaddy", "one.com", "combell", "webnode", "jimdo", "strato")
    clean = [e for e in emails if not any(b in e.split("@")[1] for b in bad)]
    return sorted(dict.fromkeys(clean), key=key)


def find_emails(home_url: str, home_html: str, max_pages: int = 6) -> list[str]:
    """Zoek e-mailadressen op de startpagina en de belangrijkste andere pagina's van de site."""
    host = urlparse(home_url).netloc.lower()
    domain = host.removeprefix("www.")
    emails = emails_in_page(home_html, domain)

    soup = BeautifulSoup(home_html, "html.parser")
    candidates = []
    for a in soup.find_all("a", href=True):
        href, label = a["href"], a.get_text(" ", strip=True).lower()
        if href.startswith(("mailto:", "tel:", "#", "javascript:")):
            continue
        url = urljoin(home_url, href)
        if urlparse(url).netloc.lower() != host:
            continue
        if any(h in href.lower() or h in label for h in CONTACT_HINTS):
            candidates.append(url.split("#")[0])
    candidates += [urljoin(home_url, p) for p in COMMON_PATHS]

    visited = {home_url.rstrip("/")}
    pages = 0
    for url in dict.fromkeys(candidates):
        if pages >= max_pages:
            break
        if url.rstrip("/") in visited:
            continue
        visited.add(url.rstrip("/"))
        # stop zodra we een adres op het eigen domein hebben
        if any(e.endswith("@" + domain) for e in emails):
            break
        try:
            r = _get(url, 8)
        except requests.RequestException:
            continue
        pages += 1
        ctype = r.headers.get("content-type", "").lower()
        if r.status_code < 400 and not ctype.startswith(("image/", "video/", "audio/", "application/pdf", "application/zip")):
            emails += emails_in_page(r.text, domain)
    return rank_emails(emails, domain)


# ---------------------------------------------------------------- website opsporen

def _slugs(name: str) -> list[str]:
    words = [w for w in _norm(name).split() if w not in LEGAL_SUFFIXES]
    core = [w for w in words if w not in GENERIC_WORDS]
    out = []
    for ws in (words, core):
        if ws:
            out += ["".join(ws), "-".join(ws)]
    return [s for s in dict.fromkeys(out) if 4 <= len(s) <= 40]


def _looks_like(page_text: str, name: str, city: str | None, postcode: str | None, phone: str | None) -> bool:
    text = _norm(page_text)
    tokens = [w for w in _norm(name).split() if w not in LEGAL_SUFFIXES and w not in GENERIC_WORDS and len(w) >= 3]
    if not tokens or sum(t in text for t in tokens) < max(1, min(2, len(tokens))):
        return False
    digits = re.sub(r"\D", "", phone or "")[-8:]
    if digits and digits in re.sub(r"\D", "", page_text):
        return True
    return bool((city and _norm(city) in text) or (postcode and postcode in page_text))


def discover_website(name: str, city: str | None, postcode: str | None, phone: str | None) -> tuple[str, str] | None:
    """Raad de domeinnaam (bv. bakkerijpeeters.be) en controleer of het echt dit bedrijf is.
    Geeft (url, html) terug of None."""
    for slug in _slugs(name):
        for tld in (".be", ".com", ".eu"):
            url = f"https://{slug}{tld}"
            try:
                r = _get(url, 6)
            except requests.RequestException:
                try:
                    r = _get(f"http://{slug}{tld}", 6)
                except requests.RequestException:
                    continue
            if r.status_code >= 400 or len(r.text) < 200:
                continue
            final_host = urlparse(r.url).netloc.lower()
            if any(s in final_host for s in ("sedo", "parking", "godaddy", "dan.com", "afternic", "facebook")):
                continue
            if _looks_like(BeautifulSoup(r.text, "html.parser").get_text(" "), name, city, postcode, phone):
                return r.url, r.text
    return None
