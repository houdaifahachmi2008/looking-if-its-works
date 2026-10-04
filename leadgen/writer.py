"""Stel per bedrijf een passende mail op: met Claude (als ANTHROPIC_API_KEY is ingesteld) of via sjablonen."""
import json
import os

from pydantic import BaseModel

from . import config, db

ISSUES_FR = {
    "geen website gevonden": "aucun site web trouvé",
    "enkel een sociale-mediapagina, geen eigen website": "seulement une page sur les réseaux sociaux, pas de site propre",
    "het SSL-certificaat is ongeldig (browsers tonen een waarschuwing)": "le certificat SSL n'est pas valide (les navigateurs affichent un avertissement)",
    "de website is niet bereikbaar": "le site web n'est pas accessible",
    "de website geeft een foutmelding": "le site web affiche une erreur",
    "geen beveiligde verbinding (https)": "pas de connexion sécurisée (https)",
    "niet geoptimaliseerd voor gsm (geen responsive design)": "pas adapté aux smartphones (pas de design responsive)",
    "laadt traag": "se charge lentement",
    "copyright-vermelding staat nog op": "la mention de copyright date encore de",
    "verouderde opbouw met tabellen": "structure dépassée basée sur des tableaux",
    "verouderde technologie (oude jQuery/Flash)": "technologie dépassée (ancien jQuery/Flash)",
    "geen paginatitel (slecht voor Google)": "pas de titre de page (mauvais pour Google)",
    "geen meta-beschrijving (slecht voor Google)": "pas de méta-description (mauvais pour Google)",
    "heel weinig inhoud op de homepage": "très peu de contenu sur la page d'accueil",
}


def _issue_fr(issue: str) -> str:
    for nl, fr in ISSUES_FR.items():
        if issue.startswith(nl):
            return fr + issue[len(nl):].split("(site")[0].rstrip()
    return issue


def footer(lang: str) -> str:
    s = config.sender()
    vat_label = "TVA" if lang == "fr" else "btw"
    if lang == "fr":
        optout = "Vous ne souhaitez plus recevoir de messages de notre part ? Répondez simplement « désinscrire » et nous ne vous contacterons plus."
    else:
        optout = "Liever geen berichten meer van ons? Antwoord gewoon met ‘uitschrijven’ en we contacteren u niet meer."
    # lege velden (geen website, btw-nummer of adres) worden gewoon weggelaten
    contact = " · ".join(filter(None, [s.phone, s.email, s.website]))
    ident = " · ".join(filter(None, [s.company, s.address, f"{vat_label} {s.vat}" if s.vat else ""]))
    lines = [s.name, s.company, contact]
    out = "\n\n--\n" + "\n".join(filter(None, lines))
    if ident and ident != s.company:
        out += "\n\n" + ident
    return out + "\n" + optout


def _who(lang: str) -> str:
    s = config.sender()
    if not s.company:
        return s.name
    return f"{s.name} de {s.company}" if lang == "fr" else f"{s.name} van {s.company}"


# ---------------------------------------------------------------- sjablonen

def _template(lead) -> tuple[str, str]:
    s = config.sender()
    issues = json.loads(lead["issues"] or "[]")
    name, cat, city = lead["name"], lead["category"], lead["city"]

    if lead["lang"] == "fr":
        greet = f"Bonjour,\n\n"
        intro = f"Je suis {_who('fr')}, je crée des sites web pour les indépendants et PME de la région de {city}."
        if lead["situation"] == "no_website":
            subject = f"Un site web pour {name} ?"
            hook = (f"En cherchant des entreprises à {city}, je suis tombé sur {name}, mais je n'ai pas trouvé de site web. "
                    "Aujourd'hui, la plupart des clients cherchent d'abord sur Google avant de choisir — sans site, ils ne vous trouvent pas.")
        elif lead["situation"] == "facebook_only":
            subject = f"{name} : plus de clients via Google"
            hook = (f"J'ai vu que {name} est surtout présent sur les réseaux sociaux. C'est un bon début, mais un site propre "
                    "vous rend beaucoup plus visible sur Google et inspire davantage confiance.")
        else:
            subject = f"Quelques remarques sur le site de {name}"
            pts = "\n".join(f"  • {_issue_fr(i)}" for i in issues[:4])
            hook = f"J'ai jeté un œil au site de {name} et j'ai remarqué quelques points qui peuvent vous coûter des clients :\n{pts}"
        offer = ("Je réalise un site moderne, rapide et adapté aux smartphones, à un prix fixe et sans surprises. "
                 "Je peux vous montrer gratuitement et sans engagement une proposition de design.\n\n"
                 "Seriez-vous ouvert à un court appel de 10 minutes cette semaine ?")
        return subject, f"{greet}{intro}\n\n{hook}\n\n{offer}\n\nBien à vous,"

    greet = "Goeiedag,\n\n"
    intro = f"Ik ben {_who('nl')}. Ik maak websites voor zelfstandigen en KMO's in de regio {city}."
    if lead["situation"] == "no_website":
        subject = f"Een website voor {name}?"
        hook = (f"Toen ik op zoek ging naar bedrijven in {city}, kwam ik {name} tegen, maar ik vond geen website. "
                "De meeste klanten zoeken vandaag eerst op Google voor ze kiezen — zonder website vinden ze u niet.")
    elif lead["situation"] == "facebook_only":
        subject = f"{name}: meer klanten via Google"
        hook = (f"Ik zag dat {name} vooral actief is via sociale media. Dat is een goed begin, maar een eigen website "
                "maakt u veel beter vindbaar op Google en wekt meer vertrouwen bij nieuwe klanten.")
    else:
        subject = f"Enkele opmerkingen over de website van {name}"
        pts = "\n".join(f"  • {i}" for i in issues[:4])
        hook = f"Ik heb even naar de website van {name} gekeken en zag enkele punten die u klanten kunnen kosten:\n{pts}"
    offer = ("Ik bouw een moderne, snelle website die perfect werkt op gsm, aan een vaste prijs zonder verrassingen. "
             "Ik toon u graag gratis en vrijblijvend een ontwerpvoorstel.\n\n"
             "Heeft u deze week 10 minuutjes voor een kort gesprek?")
    return subject, f"{greet}{intro}\n\n{hook}\n\n{offer}\n\nMet vriendelijke groeten,"


# ---------------------------------------------------------------- Claude

class Email(BaseModel):
    subject: str
    body: str


SYSTEM = """Je schrijft korte, persoonlijke prospectiemails voor een freelance webdesigner in België.
Regels:
- Schrijf in de gevraagde taal (nl = Belgisch Nederlands met 'u', fr = Belgisch Frans met 'vous').
- Maximaal 130 woorden in de body. Geen marketingclichés, geen overdreven beloftes, geen emoji's.
- Gebruik ALLEEN de feiten die je krijgt. Verzin geen reviews, cijfers, klanten of details over het bedrijf.
- Noem 1 tot 3 concrete problemen of kansen, vertaald naar wat het de zaak oplevert (meer klanten, vertrouwen, vindbaarheid).
- Eindig met één eenvoudige vraag (bv. een kort gesprek of een gratis ontwerpvoorstel).
- Sluit af met de groet ('Met vriendelijke groeten,' of 'Bien à vous,') maar ZONDER naam of handtekening: die wordt automatisch toegevoegd.
- Onderwerpregel: kort (max 8 woorden), persoonlijk, geen hoofdletters-spam."""


def _claude(lead) -> tuple[str, str] | None:
    import anthropic

    s = config.sender()
    facts = {
        "taal": lead["lang"],
        "bedrijfsnaam": lead["name"],
        "sector": lead["category"],
        "gemeente": lead["city"],
        "situatie": lead["situation"],
        "gevonden_problemen": json.loads(lead["issues"] or "[]"),
        "website": lead["website"],
        "afzender": {"naam": s.name, "bedrijf": s.company or None, "heeft_eigen_website": bool(s.website)},
    }
    client = anthropic.Anthropic()
    response = client.beta.messages.parse(
        model="claude-opus-5-5",
        max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium"},
        system=SYSTEM,
        messages=[{"role": "user", "content": "Schrijf de mail voor dit bedrijf:\n" + json.dumps(facts, ensure_ascii=False, indent=2)}],
        output_format=Email,
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        return None
    return response.parsed_output.subject, response.parsed_output.body.strip()


def run(min_score: int = 30, limit: int = 50) -> int:
    use_claude = bool(os.getenv("ANTHROPIC_API_KEY"))
    print("Schrijven met", "Claude" if use_claude else "ingebouwde sjablonen")
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT * FROM leads WHERE status='audited' AND email IS NOT NULL AND score>=?
               ORDER BY score DESC LIMIT ?""",
            (min_score, limit),
        ).fetchall()
        for lead in rows:
            result = None
            if use_claude:
                try:
                    result = _claude(lead)
                except Exception as e:  # val terug op sjabloon zodat de batch doorloopt
                    print(f"  Claude-fout bij {lead['name']}: {e} -> sjabloon")
            subject, body = result or _template(lead)
            conn.execute(
                "UPDATE leads SET subject=?, body=?, status='drafted' WHERE id=?",
                (subject, body + footer(lead["lang"]), lead["id"]),
            )
            print(f"  ✉ {lead['name'][:40]:40} {subject}")
    return len(rows)
