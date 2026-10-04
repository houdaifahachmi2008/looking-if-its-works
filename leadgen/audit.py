"""Controleer de website van elk bedrijf en bepaal hoe hard ze een nieuwe nodig hebben."""
import datetime as dt
import json
import re
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from . import config, contacts, db
from .emails import classify_email

SOCIAL_HOSTS = ("facebook.com", "instagram.com", "linktr.ee", "business.site", "tiktok.com")


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
    if not soup.find("h1"):
        issues.append("geen duidelijke hoofdtitel op de startpagina")
        score += 5
    if not soup.find("a", href=re.compile(r"^tel:", re.I)):
        issues.append("telefoonnummer is niet aanklikbaar op gsm")
        score += 5
    if len(text) < 300:
        issues.append("heel weinig inhoud op de homepage")
        score += 10

    emails = contacts.find_emails(r.url, html)

    # outdated = slechte site, improvable = degelijke site die beter kan, ok = niets gevonden
    situation = "outdated" if score >= 30 else ("improvable" if issues else "ok")
    return {"situation": situation, "issues": issues, "score": min(score, 100), "emails": emails, "url": r.url}


NO_SITE = {"situation": "no_website", "issues": ["geen website gevonden"], "score": 100, "emails": []}


def _check(row, discover: bool = True) -> tuple[dict, str | None]:
    """Controleer één bedrijf. Geeft (resultaat, website) terug; zoekt zo nodig zelf de website."""
    website = row["website"]
    res = audit_site(website) if website else dict(NO_SITE)
    if discover and res["situation"] in ("no_website", "facebook_only"):
        found = contacts.discover_website(row["name"], row["city"], row["postcode"], row["phone"])
        if found:
            print(f"    🔎 eigen website gevonden: {found[0]}")
            website = found[0]
            res = audit_site(website)
    return res, website


def _best_email(row, found: list[str]) -> tuple[str | None, str | None]:
    email, kind = row["email"], row["email_kind"]
    for cand in found:
        cand_kind = classify_email(cand, row["name"])
        if not email or (kind != "generic" and cand_kind == "generic"):
            return cand, cand_kind
    return email, kind


def _save(conn, row, res, website, set_status: bool = True):
    email, kind = _best_email(row, res["emails"])
    conn.execute(
        "UPDATE leads SET situation=?, issues=?, score=?, email=?, email_kind=?, website=?"
        + (", status='audited'" if set_status else "") + " WHERE id=?",
        (res["situation"], json.dumps(res["issues"], ensure_ascii=False), res["score"], email, kind, website, row["id"]),
    )
    conn.commit()
    print(f"  {row['name'][:40]:40} {res['situation']:14} score {res['score']:3}  {email or '-'}")
    return email


def run(limit: int = 50, stop=None, discover: bool = True) -> int:
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM leads WHERE status='new' LIMIT ?", (limit,)).fetchall()
        for row in rows:
            if stop and stop.is_set():
                break
            res, website = _check(row, discover)
            _save(conn, row, res, website)
            time.sleep(1)  # beleefd blijven tegenover andermans servers
    return len(rows)


def rescan(limit: int = 200, stop=None) -> int:
    """Zoek opnieuw naar e-mails bij al gecontroleerde bedrijven waar we er nog geen vonden."""
    found = 0
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM leads WHERE status='audited' AND email IS NULL ORDER BY score DESC LIMIT ?", (limit,)
        ).fetchall()
        print(f"{len(rows)} bedrijven zonder e-mailadres worden opnieuw doorzocht ...")
        for row in rows:
            if stop and stop.is_set():
                break
            res, website = _check(row, discover=True)
            if _save(conn, row, res, website, set_status=False):
                found += 1
            time.sleep(1)
    print(f"Nieuwe e-mailadressen gevonden: {found}")
    return found
