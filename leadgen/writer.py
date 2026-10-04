"""Stel per bedrijf een passende mail op.

Er zijn drie soorten mails, elk met een eigen sjabloon dat je in de app kan aanpassen:
  no_website       - bedrijf zonder (eigen) website
  bad_website      - bedrijf met een slechte of kapotte website
  could_be_better  - bedrijf met een degelijke website die nog beter kan
Optioneel maakt Claude elke mail persoonlijker op basis van jouw sjabloon (WRITER_MODE=claude).
"""
import json
import os
import re

from pydantic import BaseModel

from . import config, db

TYPES = {
    "no_website": {
        "label": "Geen website",
        "desc": "Bedrijven zonder eigen website (of enkel een Facebook-/Instagrampagina), maar waarvan we wel een e-mailadres vonden.",
    },
    "bad_website": {
        "label": "Slechte website",
        "desc": "Bedrijven met een website die verouderd, onveilig, niet gsm-vriendelijk of niet bereikbaar is.",
    },
    "could_be_better": {
        "label": "Website kan beter",
        "desc": "Bedrijven met een degelijke website, waar nog enkele verbeteringen mogelijk zijn.",
    },
}

PLACEHOLDERS = {
    "bedrijf": "naam van het bedrijf",
    "gemeente": "gemeente van het bedrijf",
    "sector": "soort zaak (bv. kapsalon)",
    "website": "hun website",
    "problemen": "lijst met gevonden problemen",
    "afzender": "jouw naam + bedrijf",
    "mijn_naam": "jouw naam",
    "mijn_bedrijf": "jouw bedrijfsnaam",
    "mijn_telefoon": "jouw telefoonnummer",
}

SECTORS = {
    "kapper": ("kapsalon", "salon de coiffure"),
    "schoonheidssalon": ("schoonheidssalon", "institut de beauté"),
    "restaurant": ("restaurant", "restaurant"),
    "cafe": ("café", "café"),
    "frituur": ("frituur", "friterie"),
    "bakker": ("bakkerij", "boulangerie"),
    "slager": ("slagerij", "boucherie"),
    "bloemist": ("bloemenwinkel", "fleuriste"),
    "kledingwinkel": ("kledingwinkel", "magasin de vêtements"),
    "fietsenwinkel": ("fietsenwinkel", "magasin de vélos"),
    "garage": ("garage", "garage"),
    "loodgieter": ("loodgieter", "plombier"),
    "elektricien": ("elektricien", "électricien"),
    "schrijnwerker": ("schrijnwerker", "menuisier"),
    "schilder": ("schilder", "peintre"),
    "dakwerker": ("dakwerker", "couvreur"),
    "kinesist": ("kinesitherapeut", "kinésithérapeute"),
    "tandarts": ("tandarts", "dentiste"),
    "boekhouder": ("boekhouder", "comptable"),
    "immo": ("immokantoor", "agence immobilière"),
}

# ---------------------------------------------------------------- standaardsjablonen

DEFAULTS = {
    ("no_website", "nl"): (
        "Online zichtbaarheid voor {bedrijf}",
        """Geachte heer, mevrouw,

Mijn naam is {afzender}. Ik ontwerp en bouw websites voor zelfstandigen en kmo's in en rond {gemeente}.

Bij mijn zoektocht naar lokale ondernemingen viel het mij op dat {bedrijf} momenteel geen eigen website heeft. Steeds meer klanten zoeken eerst online naar een {sector} in hun buurt voordat zij een keuze maken. Zonder website loopt u die potentiële klanten mogelijk mis.

Graag help ik u aan een professionele website die:
  • goed vindbaar is in Google;
  • perfect werkt op smartphone, tablet en computer;
  • uw diensten, openingsuren en contactgegevens duidelijk weergeeft.

Ik maak graag vrijblijvend en kosteloos een eerste ontwerpvoorstel voor u op, zodat u kunt zien wat de mogelijkheden zijn.

Hebt u deze week een tiental minuten tijd voor een kort gesprek?

Met vriendelijke groeten,""",
    ),
    ("bad_website", "nl"): (
        "Uw website: enkele belangrijke aandachtspunten",
        """Geachte heer, mevrouw,

Mijn naam is {afzender}. Ik ontwerp en bouw websites voor zelfstandigen en kmo's in en rond {gemeente}.

Onlangs bezocht ik de website van {bedrijf} en stelde ik enkele punten vast die bezoekers kunnen afschrikken en uw vindbaarheid in Google verlagen:
{problemen}

Uw website is vaak de eerste indruk die een nieuwe klant van uw zaak krijgt. Met een moderne, snelle en beveiligde website zorgt u ervoor dat die eerste indruk meteen goed zit.

Graag maak ik vrijblijvend en kosteloos een ontwerpvoorstel voor een vernieuwde website, zodat u het verschil zelf kunt beoordelen.

Hebt u deze week een tiental minuten tijd voor een kort gesprek?

Met vriendelijke groeten,""",
    ),
    ("could_be_better", "nl"): (
        "Enkele ideeën voor de website van {bedrijf}",
        """Geachte heer, mevrouw,

Mijn naam is {afzender}. Ik ontwerp en bouw websites voor zelfstandigen en kmo's in en rond {gemeente}.

Ik heb de website van {bedrijf} bekeken en zie dat er al een goede basis is. Toch zijn er enkele verbeteringen mogelijk die u meer bezoekers en aanvragen kunnen opleveren:
{problemen}

Het gaat vaak om kleine aanpassingen met een groot effect, zoals een betere vindbaarheid in Google of een vlottere werking op de smartphone.

Indien u interesse hebt, bezorg ik u graag vrijblijvend een kort overzicht van concrete verbeterpunten voor uw website.

Mag ik u daarover contacteren?

Met vriendelijke groeten,""",
    ),
    ("no_website", "fr"): (
        "Visibilité en ligne pour {bedrijf}",
        """Madame, Monsieur,

Je m'appelle {afzender}. Je conçois et réalise des sites web pour les indépendants et les PME de la région de {gemeente}.

En recherchant des entreprises locales, j'ai remarqué que {bedrijf} ne dispose pas encore de son propre site web. De plus en plus de clients effectuent d'abord une recherche en ligne avant de choisir leur {sector}. Sans site web, vous risquez de passer à côté de ces clients potentiels.

Je vous propose la création d'un site web professionnel qui :
  • est facile à trouver sur Google ;
  • s'affiche parfaitement sur smartphone, tablette et ordinateur ;
  • présente clairement vos services, vos horaires et vos coordonnées.

Je serais heureux de vous préparer, gratuitement et sans engagement, une première proposition de design.

Seriez-vous disponible cette semaine pour un bref échange d'une dizaine de minutes ?

Je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations distinguées.""",
    ),
    ("bad_website", "fr"): (
        "Votre site web : quelques points d'attention importants",
        """Madame, Monsieur,

Je m'appelle {afzender}. Je conçois et réalise des sites web pour les indépendants et les PME de la région de {gemeente}.

J'ai récemment consulté le site web de {bedrijf} et j'ai relevé quelques points qui peuvent décourager les visiteurs et nuire à votre visibilité sur Google :
{problemen}

Votre site web est souvent la première impression qu'un nouveau client a de votre entreprise. Un site moderne, rapide et sécurisé vous permet de faire bonne impression dès le premier instant.

Je serais heureux de vous préparer, gratuitement et sans engagement, une proposition de design pour un site renouvelé, afin que vous puissiez juger vous-même de la différence.

Auriez-vous une dizaine de minutes cette semaine pour un bref échange ?

Je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations distinguées.""",
    ),
    ("could_be_better", "fr"): (
        "Quelques idées pour le site web de {bedrijf}",
        """Madame, Monsieur,

Je m'appelle {afzender}. Je conçois et réalise des sites web pour les indépendants et les PME de la région de {gemeente}.

J'ai consulté le site web de {bedrijf} et je constate qu'il dispose déjà d'une bonne base. Quelques améliorations pourraient toutefois vous apporter davantage de visiteurs et de demandes :
{problemen}

Il s'agit souvent de petites adaptations avec un effet important, comme une meilleure visibilité sur Google ou une utilisation plus agréable sur smartphone.

Si cela vous intéresse, je vous ferai volontiers parvenir, sans engagement, un bref aperçu des améliorations concrètes possibles pour votre site.

Puis-je vous recontacter à ce sujet ?

Je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations distinguées.""",
    ),
}

# gevonden problemen (zoals de controle ze opslaat) -> nette zin voor de klant
ISSUE_TEXT = [
    (r"^geen beveiligde verbinding",
     "de website is niet beveiligd (geen https), waardoor browsers de melding ‘niet veilig’ tonen",
     "le site n'est pas sécurisé (pas de https), ce qui fait apparaître la mention « non sécurisé » dans les navigateurs"),
    (r"^niet geoptimaliseerd voor gsm",
     "de website is niet aangepast aan smartphones, terwijl de meeste bezoekers via hun gsm surfen",
     "le site n'est pas adapté aux smartphones, alors que la plupart des visiteurs naviguent depuis leur téléphone"),
    (r"^laadt traag \(([\d.,]+) s\)",
     "de website laadt traag ({0} seconden), waardoor bezoekers afhaken",
     "le site se charge lentement ({0} secondes), ce qui fait fuir les visiteurs"),
    (r"^copyright-vermelding staat nog op (\d{4})",
     "de website lijkt sinds {0} niet meer te zijn bijgewerkt",
     "le site ne semble plus avoir été mis à jour depuis {0}"),
    (r"^verouderde opbouw",
     "de website is opgebouwd met een verouderde techniek",
     "le site repose sur une technique de construction dépassée"),
    (r"^verouderde technologie",
     "de website gebruikt verouderde technologie die niet langer wordt ondersteund",
     "le site utilise une technologie dépassée qui n'est plus prise en charge"),
    (r"^geen paginatitel",
     "de pagina heeft geen titel, wat nadelig is voor uw vindbaarheid in Google",
     "la page n'a pas de titre, ce qui nuit à votre visibilité sur Google"),
    (r"^geen meta-beschrijving",
     "er ontbreekt een beschrijving voor Google, waardoor uw zoekresultaat minder aantrekkelijk oogt",
     "il manque une description pour Google, ce qui rend votre résultat de recherche moins attrayant"),
    (r"^geen duidelijke hoofdtitel",
     "de startpagina heeft geen duidelijke hoofdtitel, wat zowel bezoekers als Google helpt",
     "la page d'accueil n'a pas de titre principal clair, qui aide pourtant les visiteurs comme Google"),
    (r"^telefoonnummer is niet aanklikbaar",
     "uw telefoonnummer is niet aanklikbaar, zodat bezoekers u niet met één klik kunnen bellen",
     "votre numéro de téléphone n'est pas cliquable, les visiteurs ne peuvent donc pas vous appeler en un clic"),
    (r"^heel weinig inhoud",
     "de startpagina bevat weinig informatie over uw zaak",
     "la page d'accueil contient peu d'informations sur votre entreprise"),
    (r"^het SSL-certificaat is ongeldig",
     "het beveiligingscertificaat van de website is ongeldig, waardoor bezoekers een waarschuwing krijgen",
     "le certificat de sécurité du site n'est pas valide, les visiteurs reçoivent donc un avertissement"),
    (r"^de website is niet bereikbaar",
     "de website was niet bereikbaar toen ik ze probeerde te openen",
     "le site n'était pas accessible lorsque j'ai essayé de l'ouvrir"),
    (r"^de website geeft een foutmelding",
     "de website toont een foutmelding in plaats van uw pagina",
     "le site affiche un message d'erreur au lieu de votre page"),
]


def issue_sentence(issue: str, lang: str) -> str | None:
    for pattern, nl, fr in ISSUE_TEXT:
        m = re.match(pattern, issue)
        if m:
            return (fr if lang == "fr" else nl).format(*m.groups())
    return None


def template_type(lead) -> str | None:
    """Welke van de drie soorten mail past bij dit bedrijf (None = niets te melden)."""
    situation = lead["situation"]
    if situation in ("no_website", "facebook_only"):
        return "no_website"
    if situation in ("outdated", "unreachable"):
        return "bad_website"
    if situation in ("improvable", "ok") and json.loads(lead["issues"] or "[]"):
        return "could_be_better"
    return None


# ---------------------------------------------------------------- sjablonen bewaren

def get_template(conn, ttype: str, lang: str) -> dict:
    row = conn.execute("SELECT subject, body FROM templates WHERE type=? AND lang=?", (ttype, lang)).fetchone()
    if row:
        return {"subject": row["subject"], "body": row["body"], "custom": True}
    subject, body = DEFAULTS[(ttype, lang)]
    return {"subject": subject, "body": body, "custom": False}


def save_template(conn, ttype: str, lang: str, subject: str, body: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO templates (type, lang, subject, body, updated_at) VALUES (?,?,?,?,CURRENT_TIMESTAMP)",
        (ttype, lang, subject.strip(), body.strip()),
    )


def reset_template(conn, ttype: str, lang: str) -> None:
    conn.execute("DELETE FROM templates WHERE type=? AND lang=?", (ttype, lang))


# ---------------------------------------------------------------- invullen

def footer(lang: str) -> str:
    s = config.sender()
    vat_label = "TVA" if lang == "fr" else "ondernemingsnr."
    if lang == "fr":
        optout = ("Vous ne souhaitez plus recevoir de messages de notre part ? Il vous suffit de répondre "
                  "« désinscrire » et nous ne vous contacterons plus.")
    else:
        optout = ("Wenst u in de toekomst geen berichten meer van ons te ontvangen? Antwoord dan met "
                  "‘uitschrijven’ en wij contacteren u niet meer.")
    # lege velden (geen website, ondernemingsnummer of adres) worden gewoon weggelaten
    contact = " · ".join(filter(None, [s.phone, s.email, s.website]))
    ident = " · ".join(filter(None, [s.company, s.address, f"{vat_label} {s.vat}" if s.vat else ""]))
    out = "\n\n" + "\n".join(filter(None, [s.name, s.company, contact]))
    if ident and ident != s.company:
        out += "\n" + ident
    return out + "\n\n" + optout


def _field(lead, key: str):
    try:
        return lead[key]
    except (KeyError, IndexError):
        return None


def _values(lead) -> dict:
    s = config.sender()
    lang = _field(lead, "lang") or "nl"
    issues = json.loads(_field(lead, "issues") or "[]")
    sentences = [t for t in (issue_sentence(i, lang) for i in issues) if t][:4]
    if not sentences:
        sentences = ["une présentation plus moderne et une meilleure visibilité sur Google" if lang == "fr"
                     else "een modernere uitstraling en een betere vindbaarheid in Google"]
    category = _field(lead, "category") or ""
    sector = SECTORS.get(category, (category, category))[1 if lang == "fr" else 0]
    if s.company:
        afzender = f"{s.name} de {s.company}" if lang == "fr" else f"{s.name} van {s.company}"
    else:
        afzender = s.name
    return {
        "bedrijf": lead["name"],
        "gemeente": _field(lead, "city") or "",
        "sector": sector or "",
        "website": _field(lead, "website") or "",
        "problemen": "\n".join(f"  • {t}" for t in sentences),
        "afzender": afzender,
        "mijn_naam": s.name,
        "mijn_bedrijf": s.company or s.name,
        "mijn_telefoon": s.phone,
    }


def fill(text: str, values: dict) -> str:
    """Vervang {plaatshouders}; onbekende {woorden} blijven gewoon staan."""
    return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), m.group(0))), text)


def render(lead, template: dict) -> tuple[str, str]:
    values = _values(lead)
    return fill(template["subject"], values), fill(template["body"], values)


def _template(lead) -> tuple[str, str]:
    """Mail volgens het (eigen of standaard) sjabloon dat bij dit bedrijf past."""
    ttype = template_type(lead) or "could_be_better"
    with db.connect() as conn:
        tpl = get_template(conn, ttype, lead["lang"] or "nl")
    return render(lead, tpl)


# ---------------------------------------------------------------- Claude

class Email(BaseModel):
    subject: str
    body: str


SYSTEM = """Je verfijnt formele prospectiemails voor een webdesigner in België.
Je krijgt een sjabloon dat de webdesigner zelf schreef, al ingevuld voor één bedrijf, plus de feiten over dat bedrijf.
Regels:
- Behoud de toon, de structuur, de aanspreking en de afsluitende groet van het sjabloon. Het blijft formeel (u-vorm in het Nederlands, vous in het Frans).
- Maak de mail persoonlijker voor dit specifieke bedrijf en deze sector, maar houd ze even lang of korter.
- Gebruik ALLEEN de feiten die je krijgt. Verzin geen reviews, cijfers, klanten, prijzen of details over het bedrijf.
- Schrijf in de taal van het sjabloon. Geen emoji's, geen overdreven beloftes.
- Voeg GEEN naam, handtekening of afmeldzin toe: die worden automatisch onderaan gezet."""


def _claude(lead, subject: str, body: str) -> tuple[str, str] | None:
    import anthropic

    facts = {
        "bedrijfsnaam": lead["name"],
        "sector": lead["category"],
        "gemeente": lead["city"],
        "soort_mail": TYPES[template_type(lead) or "could_be_better"]["label"],
        "gevonden_problemen": json.loads(lead["issues"] or "[]"),
        "website": lead["website"],
    }
    prompt = (
        "Feiten over het bedrijf:\n" + json.dumps(facts, ensure_ascii=False, indent=2)
        + f"\n\nIngevuld sjabloon:\nOnderwerp: {subject}\n\n{body}"
    )
    client = anthropic.Anthropic()
    response = client.beta.messages.parse(
        model="claude-opus-5-5",
        max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium"},
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_format=Email,
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        return None
    return response.parsed_output.subject, response.parsed_output.body.strip()


def use_claude_default() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY")) and os.getenv("WRITER_MODE", "sjabloon") == "claude"


def draft_one(conn, lead, use_claude: bool | None = None) -> str:
    """Schrijf (of herschrijf) de mail voor één lead en bewaar ze. Geeft het onderwerp terug."""
    if use_claude is None:
        use_claude = use_claude_default()
    ttype = template_type(lead) or "could_be_better"
    subject, body = render(lead, get_template(conn, ttype, lead["lang"] or "nl"))
    if use_claude:
        try:
            subject, body = _claude(lead, subject, body) or (subject, body)
        except Exception as e:  # val terug op het sjabloon zodat de batch doorloopt
            print(f"  Claude-fout bij {lead['name']}: {e} -> sjabloon gebruikt")
    conn.execute(
        "UPDATE leads SET subject=?, body=?, status='drafted' WHERE id=?",
        (subject, body + footer(lead["lang"]), lead["id"]),
    )
    return subject


def ready_leads(conn, types=None, min_score: int = 0) -> list:
    """Gecontroleerde bedrijven met e-mailadres waarvoor nog geen mail is opgesteld."""
    rows = conn.execute(
        "SELECT * FROM leads WHERE status='audited' AND email IS NOT NULL AND coalesce(score,0)>=? "
        "ORDER BY score DESC",
        (min_score,),
    ).fetchall()
    types = set(types or TYPES)
    return [r for r in rows if template_type(r) in types]


def run(min_score: int = 0, limit: int = 50, stop=None, types=None) -> int:
    use_claude = use_claude_default()
    print("Schrijven met", "Claude (op basis van je sjablonen)" if use_claude else "je sjablonen")
    with db.connect() as conn:
        rows = ready_leads(conn, types, min_score)[:limit]
        if not rows:
            print("Geen bedrijven gevonden die klaar zijn voor een mail.")
        for lead in rows:
            if stop and stop.is_set():
                break
            subject = draft_one(conn, lead, use_claude)
            conn.commit()
            print(f"  ✉ [{TYPES[template_type(lead)]['label']}] {lead['name'][:36]:36} {subject}")
    return len(rows)


def reapply(ttype: str) -> int:
    """Maak alle concept-mails van dit type opnieuw op met het huidige sjabloon (zonder Claude)."""
    n = 0
    with db.connect() as conn:
        for lead in conn.execute("SELECT * FROM leads WHERE status='drafted'").fetchall():
            if template_type(lead) == ttype:
                draft_one(conn, lead, use_claude=False)
                n += 1
    return n
