# Sitevo — cinematic website intro

A 30-second, 1920×1080, 60 fps intro for the Sitevo website. A client message arrives,
Sitevo replies, and the chat interface physically transforms into the Sitevo website,
which then builds itself, splits into desktop / tablet / mobile, and resolves on the logo.

Everything is generated from code: no stock footage, no AI video, no people.

| File | What it is |
| --- | --- |
| `out/sitevo-intro.mp4` | Final video with sound (H.264 + AAC) |
| `out/sitevo-intro-muted.mp4` | Same video without audio, for autoplay on a website |
| `out/sitevo-intro-poster.jpg` | Last frame, for the `poster` attribute |
| `index.html` | The scene. Every element is a pure function of time (`renderAt(t)`) |
| `render.cjs` | Drives headless Chromium frame by frame (parallel workers) |
| `audio.cjs` | Deterministic synthesizer for the sound design and score |
| `build.sh` | Renders frames + audio and encodes the MP4s |

## Timeline

| Time | Scene |
| --- | --- |
| 0–2 s | Black, blue glow, glass "NEW MESSAGE" notification that expands |
| 2–5 s | Notification morphs into the chat; client message is typed and sent |
| 5–7 s | Sitevo typing indicator, "Natuurlijk.", then "Laten we beginnen." |
| 7–9 s | "BUILD WEBSITE →" button, cursor, click, short freeze |
| 9–12 s | Chat breaks apart: window → browser frame, message → headline, replies → subline, button → CTA, bubbles → cards, "SITEVO" → logo |
| 12–17 s | Nav, "WE BUILD WEBSITES.", subline, "START YOUR PROJECT →", cards build in |
| 17–21 s | Exploded layer view: DESIGN, CODE, MOBILE, SEO, PERFORMANCE, LAUNCH, then it reassembles |
| 21–24 s | The site separates into DESKTOP / TABLET / MOBILE; the mobile layout reflows live |
| 24–27 s | Push-in to full screen: "YOUR BUSINESS. ONLINE.", "Built by Sitevo.", blue beam, CTA |
| 27–30 s | Fade to black, logo flies to center, "Websites that make people stop scrolling." |

## Rebuild

Requires Node 18+, Playwright with Chromium, and ffmpeg.

```bash
./build.sh            # 60 fps (about 3–4 minutes on 4 cores)
FPS=30 ./build.sh     # faster
```

Preview a single moment in a browser: open `index.html?t=12.5`.
Open `index.html` without parameters to see a live, looping preview (no sound).

Edit text, timing, or colors in `index.html`. Cue times in `audio.cjs` mirror the visual timeline.

## Embedding on the website

```html
<video src="sitevo-intro-muted.mp4" poster="sitevo-intro-poster.jpg"
       autoplay muted playsinline preload="auto"
       style="width:100%;height:auto;background:#000"></video>
```

Browsers only autoplay muted video. Use `sitevo-intro.mp4` with a play button if you want sound.

Fonts: Inter and JetBrains Mono (SIL Open Font License, see `fonts/`).
