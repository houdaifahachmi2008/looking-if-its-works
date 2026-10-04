# Leadgen BE – klanten vinden voor websites in België

Dit systeem zoekt automatisch bedrijven in België die **geen website**, **enkel een Facebookpagina** of een
**verouderde website** hebben. Daarna stelt het voor elk bedrijf een **persoonlijke mail** op (in het Nederlands
of Frans, afhankelijk van de regio) en verstuurt het die mails, met een daglimiet.

```
zoek  →  controleer  →  schrijf  →  nakijken  →  verstuur
(OSM)    (website)      (mail)      (jij)        (SMTP)
```

## Installatie

```bash
pip install -r requirements.txt
cp .env.example .env      # vul je eigen gegevens + SMTP in
```

* **SMTP**: bij Gmail maak je een *app-wachtwoord* aan (Google-account → Beveiliging → App-wachtwoorden).
  Beter nog: gebruik een adres op je eigen domein (bv. via je hostingprovider), dat komt minder snel in spam.
* **Claude (optioneel)**: met een `ANTHROPIC_API_KEY` schrijft Claude per bedrijf een unieke mail op basis van wat
  er op hun website gevonden werd. Zonder sleutel worden de ingebouwde sjablonen gebruikt (gratis).

## Gebruik

```bash
# 1. Bedrijven zoeken (gratis data van OpenStreetMap)
python -m leadgen zoek --gemeente Gent Aalst Dendermonde --sector kapper schoonheidssalon bakker

# 2. Websites controleren (https, gsm-vriendelijk, verouderd, ...) en e-mailadressen opzoeken
python -m leadgen controleer

# 3. Mails opstellen voor de bedrijven met de hoogste nood (score 0-100)
python -m leadgen schrijf --min-score 40

# 4. Nakijken: elke mail goedkeuren, bewerken of overslaan
python -m leadgen nakijken
#    (of in één keer alle mails naar info@/contact@-adressen goedkeuren)
python -m leadgen keur-alles

# 5. Versturen – eerst een test, dan echt
python -m leadgen verstuur
python -m leadgen verstuur --echt

# Overzicht / Excel-export (ook bedrijven zonder e-mail: die kan je bellen)
python -m leadgen status
python -m leadgen export --bestand leads.csv

# Iemand antwoordt "uitschrijven"? Zet het adres (of heel het domein) op de afmeldlijst:
python -m leadgen afmelden info@bedrijf.be
python -m leadgen afmelden @bedrijf.be
```

Beschikbare sectoren: kapper, schoonheidssalon, restaurant, cafe, frituur, bakker, slager, bloemist,
kledingwinkel, fietsenwinkel, garage, loodgieter, elektricien, schrijnwerker, schilder, dakwerker, kinesist,
tandarts, boekhouder, immo. Een sector toevoegen = één regel in `leadgen/finder.py` (`CATEGORIES`).

### Automatisch elke dag

Met cron (Linux/Mac) – elke werkdag om 9u15 nieuwe leads zoeken, controleren en opstellen; versturen doe je na
het nakijken:

```cron
15 9 * * 1-5 cd /pad/naar/project && python -m leadgen zoek --gemeente Gent --sector kapper && python -m leadgen controleer && python -m leadgen schrijf
```

Wil je volledig automatisch versturen, voeg dan `python -m leadgen keur-alles && python -m leadgen verstuur --echt`
toe. Dat stuurt alleen naar algemene adressen (info@, contact@, ...) en houdt de daglimiet aan.

## Wettelijk (belangrijk!)

Ongevraagde reclamemails zijn in België streng geregeld (Wetboek Economisch Recht + KB 4 april 2003, en de AVG/GDPR).
Het systeem is daarom zo gebouwd:

| Regel | Hoe het systeem dat doet |
|---|---|
| Zonder toestemming mag je enkel naar **onpersoonlijke** adressen van bedrijven mailen (info@, contact@, …) | Adressen worden ingedeeld als `generic` / `uncertain` / `personal`. Alleen `generic` wordt automatisch goedgekeurd. Bij de andere vraagt `nakijken` of je toestemming hebt. |
| Je moet duidelijk zeggen wie je bent | Naam, e-mail en telefoon staan onderaan elke mail (verplicht in `.env`). Bedrijfsnaam, adres, btw-nummer en website komen erbij zodra je ze invult. |
| Elke mail moet een eenvoudige afmeldmogelijkheid hebben | Afmeldzin onderaan + `List-Unsubscribe`-header. Afmeldingen gaan naar een afmeldlijst die nooit meer gemaild wordt. |
| Gegevens niet langer bijhouden dan nodig | Alles staat lokaal in `leads.db`; verwijder oude leads als je er niets mee doet. |

Dit is geen juridisch advies – twijfel je, vraag het na bij je boekhouder of de FOD Economie.

## Tips voor betere resultaten

* Begin klein: 20–30 mails per dag (standaard `MAX_EMAILS_PER_DAY=30`) zodat je adres niet als spam wordt gezien.
* Stel SPF, DKIM en DMARC in voor je eigen domein (vraag het aan je hostingprovider).
* OpenStreetMap heeft niet elk bedrijf. Bedrijven zonder e-mail staan wel in de export – bellen werkt vaak nog beter.
* Voeg een paar voorbeelden van je eigen werk toe in de sjablonen (`leadgen/writer.py`).
