"""Generate a synthetic, fully-owned demo source (text-to-speech + generated
video + sidecar captions) so the whole pipeline can be tested end-to-end
without API keys or third-party content. Requires espeak-ng for speech."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .config import env
from .media import probe, run

SCRIPT = [
    ("So yeah, welcome back to the show, today we have a lot to talk about.", 0.4),
    ("Let's start with some housekeeping and a few announcements.", 1.6),
    ("Why do ninety percent of people quit the gym in the first month?", 0.3),
    ("Honestly, the truth is they never had a plan. They had a feeling.", 0.5),
    ("I failed three times before it finally clicked for me.", 1.4),
    ("And here's the thing, nobody tells you that motivation is the worst strategy.", 0.4),
    ("Wait, what? Motivation is the worst strategy? That's crazy!", 0.6),
    ("No way. You build a system, and the system carries you on the bad days.", 0.4),
    ("That changed my entire life, and I'm so proud of it!", 1.8),
    ("Okay, moving on, the weather has been pretty average this week.", 0.6),
    ("We might record the next episode on Tuesday or Wednesday.", 0.7),
    ("Anyway, the sponsor of today's episode is our own newsletter.", 0.5),
    ("Have you ever laughed so hard you cried during a job interview?", 0.3),
    ("Haha, because I did, and it was hilarious! The manager laughed too!", 0.5),
    ("I walked in with my shirt on backwards, completely backwards.", 0.4),
    ("Seriously, nobody said anything for twenty minutes. Unbelievable!", 0.5),
    ("And I still got the job. Can you believe that? I love that story.", 1.5),
    ("Alright, that's it for today, thanks for listening.", 0.5),
]


def _srt_ts(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def make_demo_video(out_dir: Path | None = None) -> Path:
    if shutil.which("espeak-ng") is None and shutil.which("espeak") is None:
        raise RuntimeError("espeak-ng is required for the demo (apt install espeak-ng / brew install espeak-ng)")
    tts = shutil.which("espeak-ng") or shutil.which("espeak")
    out_dir = out_dir or env().dir("inbox")
    work = env().dir("work") / "demo"
    work.mkdir(parents=True, exist_ok=True)
    pieces, srt, t = [], [], 0.0
    for i, (line, pause) in enumerate(SCRIPT):
        wav = work / f"l{i:02d}.wav"
        subprocess.run([tts, "-v", "en-us", "-s", "165", "-w", str(wav), line], check=True, capture_output=True)
        # trim espeak's leading/trailing silence so caption timings are tight
        trimmed = work / f"t{i:02d}.wav"
        run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-af",
             "silenceremove=start_periods=1:start_threshold=-45dB,areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse",
             "-ar", "48000", "-ac", "1", str(trimmed)])
        d = probe(trimmed)["duration"]
        sil = work / f"p{i:02d}.wav"
        run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", f"{pause}", str(sil)])
        pieces += [trimmed, sil]
        srt.append((t, t + d, line))
        t += d + pause
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in pieces))
    audio = work / "speech.wav"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(audio)])
    total = probe(audio)["duration"]
    out = out_dir / "demo_podcast.mp4"
    # landscape 1280x720 "studio" scene with a moving subject for the reframer
    vf = ("[0:v]drawbox=x=0:y=560:w=1280:h=160:color=0x1b1b2b@1:t=fill,"
          "drawtext=text='VIRALBOT DEMO PODCAST':x=40:y=610:fontsize=48:fontcolor=white,"
          "drawtext=text='%{pts\\:hms}':x=1040:y=620:fontsize=36:fontcolor=0x9a9ab0[bg];"
          "[1:v]format=rgba,geq=r='255':g='180':b='90':a='if(lt((X-60)^2+(Y-60)^2,3600),255,0)'[dot];"
          "[bg][dot]overlay=x='320+250*sin(t/6)':y=220[v]")
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"gradients=s=1280x720:c0=0x2b2d42:c1=0x8d99ae:speed=0.01:d={total:.2f}:r=30",
         "-f", "lavfi", "-i", f"color=c=black:s=120x120:d={total:.2f}:r=30", "-i", str(audio),
         "-filter_complex", vf, "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-shortest", str(out)])
    out.with_suffix(".srt").write_text("\n".join(
        f"{i + 1}\n{_srt_ts(a)} --> {_srt_ts(b)}\n{txt}\n" for i, (a, b, txt) in enumerate(srt)))
    out.with_suffix(".rights.json").write_text(json.dumps({
        "rights_status": "owned", "evidence": "Synthetic demo generated locally by ViralBot (TTS + generated video)",
        "title": "ViralBot demo podcast", "topic": "motivation podcast"}, indent=1))
    return out
