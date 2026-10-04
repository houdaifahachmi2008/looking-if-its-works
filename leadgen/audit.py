"""Controleer de website van elk bedrijf en bepaal hoe hard ze een nieuwe nodig hebben."""
import datetime as dt
import json
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from . import config, db
from .emails import classify_email, extract_emails

SOCIAL_HOSTS = ("facebook.com", "instagram.com", "linktr.ee", "business.site", "tiktok.com")
CONTACT_WORDS = ("contact", "over-ons", "about", "a-propos", "nous-contacter", "impressum")


def _get(url: str, timeout: int = 15):
    return requests.get(
        url, timeout=timeout, allow_redirects=True, headers={"User-Agent": config.USER_AGENT}
    )


def audit_site(url: str) -> dict:
    """Geeft situation, issues (lijst met korte NL-omschrijvingen), score en gevonden e-mails."""
    host = urlparse(url).netloc.lower()
    if any(s in host for s in SOCIAL_HOSTS):
        return {"situation": "facebook_only", "issues": ["enkel een sociale-mediapagina, geen eigen website"],
                "score": 90, "emails": []}

    issues: list[str] = []
    score = 0
    try:
        start = time.monotonic()
        r = _get(url)
        load = time.monotonic() - start
    except requests.exceptions.SSLError:
        return {"situation": "outdated", "issues": ["het SSL-certificaat is ongeldig (browsers tonen een waarschuwing)"],
                "score": 85, "emails": []}
    except requests.RequestException:
        return {"situation": "unreachable", "issues": ["de website is niet bereikbaar"], "score": 80, "emails": []}

    if r.status_code >= 400:
        return {"situation": "unreachable", "issues": [f"de website geeft een foutmelding ({r.status_code})"],
                "score": 80, "emails": []}

    html = r.text
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    lower = html.lower()

    if not r.url.startswith("https://"):
        issues.append("geen beveiligde verbinding (https)")
        score += 25
    if not soup.find("meta", attrs={"name": re.compile("viewport", re.I)}):
        issues.append("niet geoptimaliseerd voor gsm (geen responsive design)")
        score += 30
    if load > 4:
        issues.append(f"laadt traag ({load:.1f} s)")
        score += 10
    years = [int(y) for y in re.findall(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?((?:19|20)\d{2})", lower)]
    if years and max(years) <= dt.date.today().year - 4:
        issues.append(f"copyright-vermelding staat nog op {max(years)} (site lijkt niet onderhouden)")
        score += 15
    if "<table" in lower and lower.count("<table") >= 3 and "<div" not in lower[:5000]:
        issues.append("verouderde opbouw met tabellen")
        score += 15
    if re.search(r"jquery[-.]?1\.[0-9]", lower) or ".swf" in lower:
        issues.append("verouderde technologie (oude jQuery/Flash)")
        score += 10
    if not soup.title or not soup.title.get_text(strip=True):
        issues.append("geen paginatitel (slecht voor Google)")
        score += 10
    if not soup.find("meta", attrs={"name": re.compile("description", re.I)}):
        issues.append("geen meta-beschrijving (slecht voor Google)")
        score += 5
    if len(text) < 300:
        issues.append("heel weinig inhoud op de homepage")
        score += 10

    domain = host.removeprefix("www.")
    emails = extract_emails(html, domain)
    for a in soup.find_all("a", href=True):
        if a["href"].startswith("mailto:"):
            emails = extract_emails(a["href"][7:], domain) + emails
    if not emails:  # probeer de contactpagina
        for a in soup.find_all("a", href=True):
            if any(w in a["href"].lower() for w in CONTACT_WORDS):
                try:
                    emails = extract_emails(_get(urljoin(r.url, a["href"]), 10).text, domain)
                except requests.RequestException:
                    pass
                break

    situation = "outdated" if score >= 30 else "ok"
    return {"situation": situation, "issues": issues, "score": min(score, 100), "emails": list(dict.fromkeys(emails))}


def run(limit: int = 50) -> int:
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM leads WHERE status='new' LIMIT ?", (limit,)).fetchall()
        for row in rows:
            if row["website"]:
                res = audit_site(row["website"])
            else:
                res = {"situation": "no_website", "issues": ["geen website gevonden"], "score": 100, "emails": []}
            email, kind = row["email"], row["email_kind"]
            if not email and res["emails"]:
                email = res["emails"][0]
                kind = classify_email(email, row["name"])
            conn.execute(
                "UPDATE leads SET situation=?, issues=?, score=?, email=?, email_kind=?, status='audited' WHERE id=?",
                (res["situation"], json.dumps(res["issues"], ensure_ascii=False), res["score"], email, kind, row["id"]),
            )
            print(f"  {row['name'][:40]:40} {res['situation']:14} score {res['score']:3}  {email or '-'}")
            time.sleep(1)  # beleefd blijven tegenover andermans servers
    return len(rows)
