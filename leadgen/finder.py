"""Zoek bedrijven in België via OpenStreetMap (Overpass API, gratis en legaal te gebruiken)."""
import time

import requests

from . import config, db
from .emails import classify_email

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# Nederlandse sectornaam -> OpenStreetMap-tag
CATEGORIES = {
    "kapper": ("shop", "hairdresser"),
    "schoonheidssalon": ("shop", "beauty"),
    "restaurant": ("amenity", "restaurant"),
    "cafe": ("amenity", "cafe"),
    "frituur": ("amenity", "fast_food"),
    "bakker": ("shop", "bakery"),
    "slager": ("shop", "butcher"),
    "bloemist": ("shop", "florist"),
    "kledingwinkel": ("shop", "clothes"),
    "fietsenwinkel": ("shop", "bicycle"),
    "garage": ("shop", "car_repair"),
    "loodgieter": ("craft", "plumber"),
    "elektricien": ("craft", "electrician"),
    "schrijnwerker": ("craft", "carpenter"),
    "schilder": ("craft", "painter"),
    "dakwerker": ("craft", "roofer"),
    "kinesist": ("healthcare", "physiotherapist"),
    "tandarts": ("amenity", "dentist"),
    "boekhouder": ("office", "accountant"),
    "immo": ("office", "estate_agent"),
}


def lang_for_postcode(postcode: str | None) -> str:
    """Vlaanderen -> nl, Wallonië en Brussel -> fr (Brussel kan je manueel aanpassen)."""
    try:
        pc = int(postcode or "")
    except ValueError:
        return "nl"
    if 1000 <= pc <= 1299 or 1300 <= pc <= 1499 or 4000 <= pc <= 7999:
        return "fr"
    return "nl"


def _query(city: str, key: str, value: str) -> str:
    return f"""
[out:json][timeout:90];
area["ISO3166-1"="BE"][admin_level=2]->.be;
rel["name"="{city}"]["boundary"="administrative"](area.be);
map_to_area->.city;
nwr["{key}"="{value}"]["name"](area.city);
out center tags;
"""


def _overpass(query: str) -> list[dict]:
    last_err = None
    for url in OVERPASS_URLS:
        for attempt in range(3):
            try:
                r = requests.post(
                    url, data={"data": query}, timeout=120,
                    headers={"User-Agent": config.USER_AGENT},
                )
                if r.status_code in (429, 504):
                    time.sleep(10 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json().get("elements", [])
            except requests.RequestException as e:
                last_err = e
                time.sleep(3)
    raise RuntimeError(f"Overpass niet bereikbaar: {last_err}")


def _clean_url(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip().split(";")[0]
    if not url.startswith("http"):
        url = "https://" + url
    return url


def find(city: str, category: str) -> tuple[int, int]:
    """Haalt bedrijven op en bewaart nieuwe in de database. Geeft (gevonden, nieuw)."""
    if category not in CATEGORIES:
        raise ValueError(f"Onbekende sector '{category}'. Kies uit: {', '.join(CATEGORIES)}")
    key, value = CATEGORIES[category]
    elements = _overpass(_query(city, key, value))

    new = 0
    with db.connect() as conn:
        for el in elements:
            t = el.get("tags", {})
            email = (t.get("email") or t.get("contact:email") or "").split(";")[0].strip() or None
            postcode = t.get("addr:postcode")
            street = " ".join(filter(None, [t.get("addr:street"), t.get("addr:housenumber")])) or None
            cur = conn.execute(
                """INSERT OR IGNORE INTO leads
                   (osm_id, name, category, street, postcode, city, phone, email, email_kind,
                    website, facebook, lang)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"{el['type']}/{el['id']}",
                    t["name"],
                    category,
                    street,
                    postcode,
                    t.get("addr:city") or city,
                    t.get("phone") or t.get("contact:phone"),
                    email,
                    classify_email(email, t["name"]) if email else None,
                    _clean_url(t.get("website") or t.get("contact:website") or t.get("url")),
                    t.get("contact:facebook") or t.get("facebook"),
                    lang_for_postcode(postcode),
                ),
            )
            new += cur.rowcount
    return len(elements), new
