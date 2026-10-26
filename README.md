# MediaHub

Eén app met al je streamingdiensten: Netflix, YouTube, Disney+, Prime Video, Videoland,
NPO Start, Spotify, YouTube Music en meer. Je kunt ook je eigen apps/websites toevoegen.

## Installeren op je iPhone

1. Zet GitHub Pages aan voor deze repository (Settings → Pages → branch kiezen).
2. Open de Pages-link in **Safari** op je iPhone.
3. Tik op **Deel** → **Zet op beginscherm**.

MediaHub staat nu als app op je beginscherm. Tik op een tegel om de dienst te openen
(heb je de echte app geïnstalleerd, dan opent iOS meestal die app).

## En CarPlay?

- **Muziek & podcasts** (Spotify, YouTube Music, Apple Music) werken al op CarPlay via hun eigen apps.
- **Video** (Netflix, YouTube) laat Apple niet toe op het CarPlay-scherm tijdens het rijden.
  Geen enkele app in de App Store kan dat, en een zelfgemaakte webapp ook niet.
- Vanaf iOS 26 kunnen sommige auto's video via AirPlay op het CarPlay-scherm tonen,
  maar alleen als de auto geparkeerd staat.

## Bestanden

- `index.html` – de app
- `manifest.webmanifest`, `icon.svg` – zodat je hem als app op je beginscherm kunt zetten
- `sw.js` – laat de app ook offline openen
