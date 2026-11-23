# ViralBot architecture

## Design goals
1. **Legal by construction.** Rights status is a column on every source and a gate in
   every stage (import, render, approve, upload). Unknown-rights content is only ever a *lead*.
2. **$0 baseline.** Local Whisper, OpenCV, FFmpeg, SQLite and heuristic scoring work with no paid API.
   An LLM adds better judgment for about one small call per source and one per clip.
3. **Crash-safe and resumable.** Each row's `status` is the state machine. Stages claim rows with a lease;
   stale leases are released on the next tick, so `kill -9` at any point is safe.
4. **Quota-aware.** The YouTube Data API's default quota is 10,000 units per day, and one upload costs about 1,600. Discovery keeps a reserve for uploads.
5. **Simple to operate.** One worker process (`python -m viralbot run`) and one dashboard (`streamlit run`).
   They talk only through SQLite (WAL mode).

## Data flow

```
                ┌──────────────────────── discovery (every N hours) ─────────────────────────┐
 YouTube API ──►│ search (CC-only by default) + owned/authorized channel uploads            │
 Twitch API  ──►│ top clips per category + authorized broadcasters                           │──► sources
 data/inbox  ──►│ your own files (+ .rights.json / .srt sidecars)                             │   (rights_status,
                └─────────────────────────────────────────────────────────────────────────────┘    discovery_score)
                                    │ rights ∈ {owned, licensed, creative_commons, authorized}?
                         no ──► status = lead (never downloaded)      yes ──► queued
                                                                                │
 importer ── local path / direct URL / yt-dlp (opt-in, rights-cleared only) ───► imported
 transcription ── sidecar captions › faster-whisper › OpenAI Whisper ──────────► transcribed (words + timings)
 viral engine ── sentence windows → heuristics → LLM shortlist → blend → learning adj. → NMS ──► clips: candidate (≥ min score)
 renderer ── jump-cut silence removal → face/motion reframe 9:16 → punch zoom → ASS subs → loudnorm ──► rendered
 metadata ── LLM / template title, description (+attribution), hashtags, category
 QC ── duration, 1080x1920, audio level, black frames, subtitles, rights, score, duplicates (sha256 + pHash + overlap)
        → qc_failed | pending_approval | approved (auto-approve only for low risk when manual approval is off)
 uploader ── daily cap, quota, privacy gates, scheduled publishAt, resumable upload + retries ──► uploaded
 analytics ── videos.list stats + YouTube Analytics (avg view %, subs) ──► learning_weights
                                                                          └─► score adjustments, topic order,
                                                                              preferred clip length
```

## Modules

| Module | Responsibility |
|---|---|
| `viralbot/config.py` | Secrets from env (`Env`) and runtime defaults (`DEFAULT_SETTINGS`) |
| `viralbot/db.py` | SQLite schema, settings store, events, cost/quota ledger |
| `viralbot/logging_setup.py` | Console + rotating file + WARNING+ mirrored to DB for the dashboard |
| `viralbot/rights.py` | Rights statuses, audit log, attribution, risk assessment, monetization notes |
| `viralbot/discovery/` | `youtube.py`, `twitch.py`, `local_inbox.py`, `ranking.py` (velocity/engagement/growth), `store.py` |
| `viralbot/importer.py` | Rights-gated media import |
| `viralbot/transcription.py` | Sidecar captions / faster-whisper / OpenAI Whisper → word timestamps |
| `viralbot/analysis/heuristics.py` | Free signals: hook cues, laughter/surprise/emotion lexicons, speech rate, silence, audio energy |
| `viralbot/analysis/segmenter.py` | Sentence-aligned candidate windows, energy fallback, non-max suppression |
| `viralbot/analysis/llm.py` | Gemini / OpenAI JSON REST clients with retries + cost accounting |
| `viralbot/analysis/scorer.py` | The 25/25/20/20/10 rubric, blending, thresholding, persistence |
| `viralbot/analysis/learning.py` | Shrunken per-feature performance → score adjustments |
| `viralbot/editing/timeline.py` | Edit decision list (silence removal that never drops words) and time remapping |
| `viralbot/editing/cropper.py` | Haar face tracking + motion fallback, dead-zone camera, FFmpeg x(t) expression |
| `viralbot/editing/subtitles.py` | ASS karaoke-style subtitles with active-word and emphasis highlighting |
| `viralbot/editing/renderer.py` | Single-pass FFmpeg filter graph + thumbnail |
| `viralbot/metadata.py` | Titles/descriptions/hashtags/category with anti-clickbait guard |
| `viralbot/quality.py` | QC checks, duplicate detection, risk gate → approval routing |
| `viralbot/publishing/` | OAuth, resumable uploads, retries, scheduling, crash reconciliation |
| `viralbot/analytics.py` | Stats collection → learning refresh |
| `viralbot/pipeline.py` | Stage orchestration, leases, retries |
| `viralbot/scheduler.py` | Continuous worker with heartbeat, DB start/stop flag, back-off recovery |
| `viralbot/cli.py` | Command-line entry point |
| `dashboard/app.py` | Streamlit control panel |

## Scoring details
* **Heuristics (always on, free).** Hook: questions, second person, numbers, hook phrases, short
  first sentence, a loud opening compared with the source median, and a penalty for opening on a filler word. Entertainment: laughter and
  surprise lexicons, exclamations, loudness dynamics. Emotion: emotion lexicon and energy. Retention:
  2.2–3.8 words per second, little silence, ending on a sentence boundary, length near the target. Quality: resolution, level, fps.
* **LLM (optional).** One batched call ranks the top 10 heuristic windows. The model is asked to be
  calibrated (an average clip scores about 50) and to flag `context_ok=false` for excerpts that would mislead out of context.
  Those clips are capped at 40.
* **Blend.** `0.65·LLM + 0.35·heuristic` for each rubric component. Quality is always measured, not
  guessed.
* **Learning.** `adjusted = viral + clip(Σ adj / √k, ±8)`. The threshold (default 75) applies to the
  adjusted score.

## Integrity rules in the editor
* Only pauses between words are shortened (to ≤ 0.45 s by default). No spoken word is cut and nothing is reordered.
* A clip starts on a sentence start and preferably ends on a sentence end.
* The headline banner comes from the LLM (instructed to be factual) or from your own commentary.
* An LLM context check rejects excerpts that change the speaker's meaning.

## Scaling path
* SQLite → PostgreSQL: `db.py` is the only module that touches SQL connections.
* Several workers: leases already use compare-and-swap `UPDATE … WHERE status=?`. Run one worker per
  stage, or several render workers.
* GPU Whisper: `WHISPER_DEVICE=cuda WHISPER_COMPUTE_TYPE=float16`.
* Multiple channels: add a `channel` column to settings and uploads, with one token file per channel.
