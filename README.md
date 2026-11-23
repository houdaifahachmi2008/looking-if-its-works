# ViralBot: rights-aware YouTube Shorts automation

ViralBot finds content that is gaining attention, picks the moments most likely to work as Shorts,
edits them into polished vertical clips, writes the metadata, uploads them to YouTube, and learns from
how they perform. It is built to be **cheap to run** (a $0 baseline on your own hardware), **reliable**
(crash-safe state machine, retries, quota guard) and **safe for a monetized channel** (rights gating,
attribution, duplicate detection, private-first publishing).

```
discover ─► rights gate ─► import ─► transcribe ─► score (AI) ─► edit ─► metadata ─► QC ─► approve ─► upload ─► analytics ─► learn
```

> **Copyright first.** A publicly accessible video is not free to reuse. ViralBot only downloads,
> processes or publishes sources marked **owned**, **licensed**, **creative_commons** or **authorized**.
> Everything else it discovers is stored as a **lead**: a trend signal, or a creator you could ask for
> permission. Subtitles, zooms and cuts do **not** make someone else's content yours or monetizable.
> See [Copyright & monetization](#copyright--monetization).

---

## Contents
- [Features](#features)
- [Quick start (5 minutes, no API keys)](#quick-start-5-minutes-no-api-keys)
- [Installation](#installation)
- [Configuration](#configuration)
- [Daily operation](#daily-operation)
- [How it works](#how-it-works)
- [Costs & quotas](#costs--quotas)
- [Copyright & monetization](#copyright--monetization)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Project structure](#project-structure)
- [Status: what is verified, what is not](#status-what-is-verified-what-is-not)
- [Roadmap](#roadmap)

## Features

| Area | What you get |
|---|---|
| **Discovery** | YouTube Data API: topic search (Creative Commons filter by default) and your own or authorized channels (cheap playlist reads). Twitch Helix: top recent clips per category and authorized broadcasters. A local inbox for your own recordings. Ranking by views/hour, engagement rate, recency and measured growth between snapshots. |
| **AI viral detection** | Sentence-aligned 20–45 s windows. Free heuristic scoring (hook cues, laughter, surprise and emotion words, speech rate, silence, audio energy), then **one batched Gemini or OpenAI call per source** for the 25/25/20/20/10 rubric. A context-integrity check, and a learning adjustment from your channel's own analytics. Clips need **≥ 75/100** by default. |
| **Editing** | Jump cuts that remove dead air without dropping words. **Smart 9:16 reframing** (face tracking with a dead-zone virtual camera; blurred-fill layout for gameplay or wide shots). Punch-in zoom on sentence changes or a subtle slow zoom. Animated subtitles with the **active word highlighted** and emphasis words coloured. Optional headline banner. **-14 LUFS** loudness normalisation. 1080×1920, 30 fps, H.264/AAC, faststart. |
| **Metadata** | Title, description, hashtags (always `#shorts`), category, thumbnail. An anti-clickbait guard removes misleading phrases and shouting caps. Attribution for CC and authorized sources is added automatically. |
| **Publishing** | Official YouTube Data API v3 resumable uploads with exponential-backoff retries. Daily cap and quota guard. **Private by default**, and public publishing needs a second explicit approval. Scheduled publishing into time slots. Duplicate protection (one upload per clip, content hash, perceptual hash, time overlap). Crash reconciliation through an invisible marker tag. |
| **Analytics & learning** | Views, likes, comments (Data API), and average view duration, % viewed and subscribers gained (YouTube Analytics API). Performance by topic, length, layout, zoom style and hook type feeds back into scoring, discovery topic order and target clip length. |
| **Dashboard** | Streamlit, dark theme. KPIs, start/stop, review queue with video player and approve/reject/edit, sources and rights (with an audit log), analytics, learned weights, costs, quota, error log, all settings, compliance page. |
| **Ops** | One worker process with heartbeat, a DB-driven start/stop flag, lease-based crash recovery, retry limits, rotating logs, and warnings and errors mirrored into the dashboard. |

## Quick start (5 minutes, no API keys)

```bash
git clone <this repo> viralbot && cd viralbot
python3.11 -m venv .venv && source .venv/bin/activate     # Python 3.11+
pip install -r requirements.txt
# FFmpeg is required; espeak-ng is only needed for the demo video
sudo apt install ffmpeg espeak-ng        # macOS: brew install ffmpeg espeak-ng

python -m viralbot demo                  # synthesises an owned demo podcast and runs the whole pipeline
streamlit run dashboard/app.py           # open http://localhost:8501 → "Review queue"
```

`demo` generates a 75-second text-to-speech "podcast" with captions. The scorer should find the two
genuinely punchy segments, a question about quitting the gym and a funny job-interview story, and skip
the housekeeping talk. They are rendered into Shorts under `data/clips/clip_*/short.mp4` and land in the review queue.

## Installation

### Requirements
- **Python 3.11 or newer** (tested on 3.11 and 3.13)
- **FFmpeg 5 or newer** built with `libass`, `libx264` and `libfreetype` (standard Linux, Homebrew and Windows "full" builds have these)
- 2+ CPU cores and 4 GB RAM. Whisper `base` transcribes about 5–10× faster than real time on a modern CPU.
- A bold font for subtitles. The default is `Inter Black`; set `SUBTITLE_FONT` to any installed family (`fc-list`), e.g. `DejaVu Sans`.

### Linux / macOS
```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then edit .env
python -m viralbot init
python -m viralbot status       # shows which integrations are configured
```

### Windows
Install Python 3.11+ (tick "Add Python to PATH"), then FFmpeg (e.g. `winget install Gyan.FFmpeg`).
**Easiest:** double-click `start_viralbot.bat`. The first time it installs everything, and every later
time it just opens the dashboard. Manual alternative:
```powershell
py -3.11 -m venv .venv; .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -m viralbot init
```

### Run as a service (Linux, systemd)
```ini
# /etc/systemd/system/viralbot.service
[Unit]
Description=ViralBot worker
After=network-online.target
[Service]
WorkingDirectory=/opt/viralbot
ExecStart=/opt/viralbot/.venv/bin/python -m viralbot run
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
```
Run the dashboard the same way with `ExecStart=… -m streamlit run dashboard/app.py --server.port 8501`.
Put it behind a reverse proxy with authentication if it is reachable from the internet: the dashboard has no login.

## Configuration

**Easiest:** open the dashboard → **🔌 Connections**. Log in to YouTube with one click, paste keys, and test each connection there; keys are written to your local `.env`.

Secrets live in `.env` (see `.env.example`). Behaviour settings live in the database and can be edited
in the dashboard's **Settings** page or with `python -m viralbot set <key> <json>`.

### 1. Your own content (recommended first source)
Drop videos into `data/inbox/`. Optional sidecars next to `episode.mp4`:
- `episode.srt` / `episode.vtt` / `episode.words.json`: existing captions (skips Whisper, free)
- `episode.rights.json`:
  ```json
  {"rights_status": "licensed", "evidence": "Contract #123, 2026-10-01", "attribution": "Footage: Jane Doe", "topic": "fitness podcast", "title": "Episode 12"}
  ```
Files without a rights sidecar default to `INBOX_DEFAULT_RIGHTS` (`owned`). Or register a file directly:
```bash
python -m viralbot import /path/video.mp4 --rights owned --topic "gaming highlight"
```

### 2. Gemini (or OpenAI) for better scoring and metadata
Create a key at Google AI Studio and set `GEMINI_API_KEY`. `LLM_PROVIDER=auto` prefers Gemini, then
OpenAI, then heuristics only. Model names and prices are configurable (`GEMINI_MODEL`, `LLM_PRICE_*`).

### 2b. No Google/OpenAI key? Free local AI with Ollama
1. Install Ollama from ollama.com (Windows, macOS, Linux) and start it.
2. `ollama pull qwen2.5:7b` (about 4.7 GB; needs about 8 GB RAM. On weaker machines use `qwen2.5:3b`.)
3. In `.env`: `LLM_PROVIDER=ollama` and `OLLAMA_MODEL=qwen2.5:7b`.

It is free and private, but slower than Gemini (roughly 20–60 s per source video on a CPU) and a little less sharp.

### 3. YouTube Data API (discovery and uploads)
1. Google Cloud Console: create a project and enable **YouTube Data API v3** and **YouTube Analytics API**.
2. **Credentials → API key** goes into `YOUTUBE_API_KEY` (used for discovery).
3. **OAuth consent screen**: External, add yourself as a test user, and add the scopes `youtube.upload`, `youtube.readonly` and `yt-analytics.readonly`.
4. **Credentials → OAuth client ID → Desktop app**: download the JSON as `./client_secret.json`.
5. `python -m viralbot auth-youtube` opens a browser and stores `data/youtube_token.json`.
   On a headless server, use `--no-browser` and forward port 8765 (`ssh -L 8765:localhost:8765 server`).

> **Important YouTube policy:** videos uploaded through the API by an **unverified API project** are
> locked to *private* until the project passes Google's API compliance audit. Uploads still work,
> which is fine for the testing phase. Apply for the audit (YouTube API Services, "Audit and Quota
> Extension" form) before you enable public publishing.

### 4. Twitch API
Register an app at dev.twitch.tv and set `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET`. Clips from
broadcasters in `AUTHORIZED_TWITCH_BROADCASTERS` are processable. All others are leads.

### 5. Clipping other creators (with permission)
Dashboard → **🤝 Creators**:
1. **Suggestions** lists popular channels the bot discovered. Add the ones you like to your outreach list.
2. **Message** generates a permission request (Dutch or English). Send it by e-mail, Discord or DM.
3. When a creator says yes, set them to **approved** and record the evidence (where and when they said yes).
   From then on, all their existing and future videos and clips are marked *authorized*, credited
   automatically, and clipped and posted by the bot. Set them to **revoked** and everything that isn't published yet is stopped.
4. To download their videos, turn on **🔌 Connections → 4 · Downloads** (yt-dlp). It is only ever used for approved creators.
   Platform terms restrict downloading, so asking creators for the files and using `data/inbox` is the cleanest route.

### 5b. Authorized creators via .env (legacy)
Put channel IDs or Twitch logins you have **written permission** from into `AUTHORIZED_*`, and record
the evidence per source in **Sources & rights**. Authorized sources with no evidence on file are treated as high risk and cannot be approved.

### Key runtime settings (defaults)
| Setting | Default | Meaning |
|---|---|---|
| `min_viral_score` | 75 | selection threshold |
| `max_clips_per_source` | 3 | |
| `min/max/target_clip_seconds` | 20 / 45 / 32 | |
| `zoom_style` | punch | `punch`, `subtle`, `none` |
| `layout` | auto | `face_crop`, `blur_fill` |
| `require_manual_approval` | true | turn off for auto-approval of **low-risk** clips |
| `max_daily_uploads` | 3 | the default quota allows about 5 per day |
| `default_privacy` | private | `unlisted`, `public` (needs `public_publishing_approved`) |
| `schedule_uploads` + `upload_slots_utc` | off | publishAt slots for public videos |
| `discovery_interval_hours` / `analytics_interval_hours` | 4 / 6 | |

## Daily operation

```bash
python -m viralbot run --enable          # continuous worker (or use Start in the dashboard)
streamlit run dashboard/app.py           # control panel
```
Useful commands:
```bash
python -m viralbot status                # counters, integrations, quota used today
python -m viralbot discover              # one discovery pass
python -m viralbot process --loops 3     # drain the processing queue
python -m viralbot clips                 # list clips
python -m viralbot approve 12 --commentary "Why this matters: …"
python -m viralbot reject 13 --reason "context"
python -m viralbot upload                # upload approved clips now
python -m viralbot analytics             # pull stats and update learning
python -m viralbot rights 42 authorized --evidence "email 2026-11-02 from creator"
python -m viralbot set min_viral_score 80
```

A suggested rollout:
1. **Week 1.** Inbox only, manual approval, `private` uploads. Watch the clips and tune `min_viral_score`.
2. **Week 2.** Add Gemini and switch to `unlisted`. Collect analytics.
3. **After the API audit.** Turn on `public_publishing_approved`, then `schedule_uploads`. Optionally turn off
   manual approval, which auto-approves only *low-risk* (owned or licensed) clips.

## How it works

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design. In short:

1. **Discovery** stores every item with metrics and a rights status. A metric snapshot is taken on each
   pass, so *growth per hour* is measured, not guessed. `discovery_score` (0–100) combines views per hour, engagement,
   recency and measured growth, plus learned topic performance.
2. **Rights gate.** Only rights-cleared sources move to `queued`.
3. **Import.** Local files, direct media URLs, or yt-dlp (opt-in, rights-cleared sources only).
4. **Transcription** (word timestamps). Sidecar captions first, then local faster-whisper, then the OpenAI Whisper API.
5. **Viral engine.** Windows start and end on sentence boundaries and must last 20–45 s *after* silence
   removal. Heuristics shortlist 10 diverse windows, the LLM scores them in one call, and the scores are blended, adjusted and de-overlapped.
6. **Render** in a single FFmpeg pass: trim/concat jump cuts, reframing, zoom, subtitles, loudness.
7. **Metadata and QC.** Duration, resolution, audio level, black frames, subtitles, rights, score and duplicates are checked, then the clip is routed to `pending_approval` or `approved`.
8. **Upload** within the limits and gates. **Analytics**, then **learning**.

Every stage is idempotent and driven by row status, so killing the process at any moment is safe.

## Costs & quotas

| Item | Cost |
|---|---|
| FFmpeg, OpenCV, faster-whisper, SQLite, Streamlit | free |
| Gemini Flash-class model | about 4k tokens per source plus about 1k per clip, roughly **$0.002–0.01 per source** at typical Flash pricing (check current prices; set `LLM_PRICE_*`) |
| OpenAI Whisper API (optional) | about $0.006 per audio minute |
| YouTube Data API | free; quota of **10,000 units per day**. Search costs 100, list calls 1, an **upload about 1,600**, a thumbnail 50 |
| Twitch API | free |

Discovery keeps a quota reserve of about 3 uploads. Owned and authorized channels are read through
the uploads playlist (2–3 units) instead of search (100). The dashboard shows estimated spend and today's quota.

## Copyright & monetization

Built-in safeguards:
- **Rights tracking.** Every source has `rights_status`, `license`, `attribution` and `rights_evidence`, and every change is written to `rights_log` (an audit trail).
- **Hard gates.** Import, render, approve and upload all re-check rights. High-risk clips cannot be approved or uploaded.
- **Attribution.** Added to the description automatically for CC BY and authorized sources.
- **Risk warnings** on every clip, covering the reused-content policy, missing permission evidence, Twitch music and game audio, and missing commentary.
- **Original contribution.** Add your own commentary in the review screen. It is placed in the description and can replace the headline banner.
- **Duplicate detection.** Content hash, perceptual hash, time overlap, and one upload per clip.
- **No circumvention.** There is no DRM bypass. yt-dlp is off by default and limited to rights-cleared sources.

YouTube Partner Program reality check:
- The **reused content** policy can demonetize a channel built mainly on other people's material, *even
  with permission or a CC license*, unless you add significant original commentary, education or creativity.
- Content ID can claim clips that contain music or game soundtracks.
- The most reliably monetizable setup is **your own long-form content** (podcast, streams, interviews you
  host) turned into Shorts. This is the inbox path, and the use case ViralBot is optimised for.

This is engineering guidance, not legal advice.

## Testing

```bash
pytest -q          # 51 tests, about 1 minute (uses FFmpeg; no network or API keys needed)
```
The suite covers the silence-removal timeline, subtitle generation, crop expressions, rights gates,
YouTube discovery (mocked API, CC detection, quota accounting), quota exhaustion, the inbox with rights
sidecars, heuristic ranking, LLM scoring (mocked), caption parsing, the metadata guard, privacy gates,
scheduled publishing, resumable upload retry/dedupe/limits (mocked client), learning, stale-lease
recovery, and a **full end-to-end render** from inbox to QC-passed 1080×1920 Short to approval.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `ffmpeg not found` / `No such filter: 'ass'` | Install an FFmpeg build with libass |
| Subtitles render in a fallback font | Set `SUBTITLE_FONT` to an installed family (`fc-list : family`) |
| Whisper model download fails | faster-whisper downloads from huggingface.co on first use. Allow that host, pre-download into `data/models`, or use sidecar captions or `TRANSCRIBE_ENGINE=openai` |
| No clips selected | Check the scores in **Clips** (status `below_threshold`). Lower `min_viral_score`, or add an LLM key for better judgment |
| Uploads stay private | Expected for unverified API projects (see the YouTube policy note above) |
| `quotaExceeded` | Lower `discovery_max_results_per_topic` or the number of topics, or request a quota extension |
| Approved clips never upload | Run `python -m viralbot auth-youtube`; check the daily limit and the quota |
| Worker shows "not running" | Start it from the dashboard or run `python -m viralbot run`; check `data/logs/` |

## Project structure
```
viralbot/
  config.py  db.py  logging_setup.py  rights.py  media.py
  discovery/   youtube.py twitch.py local_inbox.py ranking.py store.py
  importer.py  transcription.py
  analysis/    heuristics.py segmenter.py llm.py scorer.py learning.py
  editing/     timeline.py cropper.py subtitles.py renderer.py
  metadata.py  quality.py
  publishing/  youtube_auth.py uploader.py
  analytics.py pipeline.py scheduler.py cli.py demo.py
dashboard/app.py
tests/
docs/ARCHITECTURE.md
```

## Status: what is verified, what is not

**Verified in development** (Linux, Python 3.11 and 3.13, FFmpeg 6.1):
- The full local pipeline on a real rendered video: the demo source becomes 2 QC-passed 1080×1920 Shorts. Frames were inspected: subtitle sync across jump cuts, word highlighting, punch zoom, banner, blurred fill.
- Face tracking on a moving real face (detected in 93% of samples; the crop follows the subject).
- Dashboard: every page loads without errors, and Start/Stop launches and pauses the worker.
- All external APIs through mocked responses (YouTube discovery, LLM scoring, the upload client).

**Not verified against live services** (no credentials in the build environment):
- Real calls to the YouTube Analytics API, Twitch Helix, Gemini, OpenAI and Ollama (YouTube Data API discovery **was** verified live). The code follows the official REST and client-library contracts, but the first live run should be private uploads.
- faster-whisper model download and transcription (huggingface.co was not reachable). The integration follows faster-whisper's documented API, and the rest of the pipeline was tested with caption sidecars.
- yt-dlp import.

## Roadmap
- Speaker diarization, so the camera follows the *active* speaker in multi-person podcasts
- Multimodal scoring (sending sampled frames to Gemini) for gameplay without speech
- A/B title testing and per-hour posting-time learning
- PostgreSQL and multiple workers; multi-channel support
- Optional AI voice-over for original commentary (with disclosure)
