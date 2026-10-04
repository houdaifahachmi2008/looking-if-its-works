"""Command line: python -m leadgen <commando>"""
import argparse
import csv
import json
import os
import subprocess
import tempfile

from . import audit, db, finder, sender, writer

SENDABLE = ("generic",)


def cmd_zoek(a):
    for city in a.gemeente:
        for cat in a.sector:
            total, new = finder.find(city, cat)
            print(f"{city:15} {cat:18} {total:4} gevonden, {new:4} nieuw")


def cmd_controleer(a):
    n = audit.run(a.limiet)
    print(f"{n} bedrijven gecontroleerd.")


def cmd_schrijf(a):
    n = writer.run(a.min_score, a.limiet)
    print(f"{n} mails opgesteld. Kijk ze na met: python -m leadgen nakijken")


def _edit(text: str) -> str:
    with tempfile.NamedTemporaryFile("w+", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(text)
        path = f.name
    subprocess.call([os.getenv("EDITOR", "nano"), path])
    with open(path, encoding="utf-8") as f:
        new = f.read()
    os.unlink(path)
    return new


def cmd_nakijken(a):
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM leads WHERE status='drafted' ORDER BY score DESC").fetchall()
        for lead in rows:
            print("\n" + "=" * 72)
            print(f"{lead['name']} — {lead['category']}, {lead['city']} (score {lead['score']})")
            print(f"Aan: {lead['email']}  [{lead['email_kind']}]   site: {lead['website'] or '-'}")
            if lead["email_kind"] not in SENDABLE:
                print("⚠  Dit lijkt een persoonlijk adres. Volgens de Belgische regels mag je hier "
                      "enkel mailen met voorafgaande toestemming. Bel liever op: " + (lead["phone"] or "-"))
            print(f"Onderwerp: {lead['subject']}\n")
            print(lead["body"])
            while True:
                keuze = input("\n[g]oedkeuren  [b]ewerken  [o]verslaan  [s]top > ").strip().lower()
                if keuze == "g":
                    if lead["email_kind"] not in SENDABLE and input(
                        "Heb je toestemming van deze persoon? (ja/nee) > ").strip().lower() != "ja":
                        conn.execute("UPDATE leads SET status='skipped' WHERE id=?", (lead["id"],))
                        break
                    conn.execute("UPDATE leads SET status='approved' WHERE id=?", (lead["id"],))
                    break
                if keuze == "b":
                    new = _edit(f"{lead['subject']}\n\n{lead['body']}")
                    subject, _, body = new.partition("\n\n")
                    conn.execute("UPDATE leads SET subject=?, body=? WHERE id=?",
                                 (subject.strip(), body.strip(), lead["id"]))
                    conn.commit()
                    lead = conn.execute("SELECT * FROM leads WHERE id=?", (lead["id"],)).fetchone()
                    print(f"\nOnderwerp: {lead['subject']}\n\n{lead['body']}")
                    continue
                if keuze == "o":
                    conn.execute("UPDATE leads SET status='skipped' WHERE id=?", (lead["id"],))
                    break
                if keuze == "s":
                    return
            conn.commit()


def cmd_keur_alles(a):
    with db.connect() as conn:
        n = conn.execute(
            "UPDATE leads SET status='approved' WHERE status='drafted' AND email_kind='generic' AND score>=?",
            (a.min_score,),
        ).rowcount
    print(f"{n} mails naar algemene adressen (info@, contact@, ...) goedgekeurd.")


def cmd_verstuur(a):
    sender.run(really_send=a.echt)


def cmd_afmelden(a):
    with db.connect() as conn:
        db.suppress(conn, a.adres)
    print(f"{a.adres} staat op de afmeldlijst en wordt nooit meer gemaild.")


def cmd_status(a):
    with db.connect() as conn:
        for row in conn.execute("SELECT status, COUNT(*) n FROM leads GROUP BY status"):
            print(f"{row['status']:10} {row['n']}")
        print(f"vandaag verstuurd: {sender.sent_today(conn)}")


def cmd_export(a):
    with db.connect() as conn, open(a.bestand, "w", newline="", encoding="utf-8-sig") as f:
        rows = conn.execute("SELECT * FROM leads ORDER BY score DESC").fetchall()
        w = csv.writer(f, delimiter=";")
        cols = ["name", "category", "street", "postcode", "city", "phone", "email", "email_kind",
                "website", "situation", "score", "issues", "status", "sent_at"]
        w.writerow(cols)
        for r in rows:
            w.writerow([", ".join(json.loads(r[c] or "[]")) if c == "issues" else r[c] for c in cols])
    print(f"{len(rows)} leads geëxporteerd naar {a.bestand} (opent in Excel).")


def main():
    p = argparse.ArgumentParser(prog="leadgen", description="Vind en mail Belgische bedrijven die een website nodig hebben.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("zoek", help="bedrijven zoeken via OpenStreetMap")
    s.add_argument("--gemeente", nargs="+", required=True, help="bv. Gent Aalst Brugge")
    s.add_argument("--sector", nargs="+", required=True, help="kies uit: " + ", ".join(finder.CATEGORIES))
    s.set_defaults(f=cmd_zoek)

    s = sub.add_parser("controleer", help="websites controleren en e-mailadressen zoeken")
    s.add_argument("--limiet", type=int, default=100)
    s.set_defaults(f=cmd_controleer)

    s = sub.add_parser("schrijf", help="persoonlijke mails opstellen")
    s.add_argument("--min-score", type=int, default=30)
    s.add_argument("--limiet", type=int, default=50)
    s.set_defaults(f=cmd_schrijf)

    sub.add_parser("nakijken", help="mails één voor één nakijken/goedkeuren").set_defaults(f=cmd_nakijken)

    s = sub.add_parser("keur-alles", help="alle mails naar info@/contact@-adressen goedkeuren")
    s.add_argument("--min-score", type=int, default=30)
    s.set_defaults(f=cmd_keur_alles)

    s = sub.add_parser("verstuur", help="goedgekeurde mails versturen (standaard een test)")
    s.add_argument("--echt", action="store_true", help="echt versturen i.p.v. test")
    s.set_defaults(f=cmd_verstuur)

    s = sub.add_parser("afmelden", help="adres of @domein op de afmeldlijst zetten")
    s.add_argument("adres")
    s.set_defaults(f=cmd_afmelden)

    sub.add_parser("status", help="overzicht").set_defaults(f=cmd_status)

    s = sub.add_parser("export", help="alle leads naar CSV (ook die zonder e-mail, om te bellen)")
    s.add_argument("--bestand", default="leads.csv")
    s.set_defaults(f=cmd_export)

    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
