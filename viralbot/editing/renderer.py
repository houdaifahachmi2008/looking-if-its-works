"""Clip renderer: one FFmpeg pass per clip.

trim/concat (jump cuts that remove dead air) → smart 9:16 reframing →
dynamic punch-in zoom on sentence changes → burned animated subtitles →
EBU R128 loudness normalisation (-14 LUFS, YouTube's playback target) →
H.264 1080x1920 30 fps + AAC, faststart.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .. import db
from ..config import env
from ..logging_setup import get_logger
from ..media import probe, run
from .cropper import plan_crop, x_expression
from .subtitles import build_ass
from .timeline import Timeline, build_timeline

log = get_logger("renderer")
OUT_W, OUT_H, FPS = 1080, 1920, 30


@dataclass
class RenderResult:
    path: Path
    duration: float
    layout: str
    zoom_style: str
    timeline: Timeline
    face_ratio: float


def _zoom_expr(style: str, switch_points: list[float], total: float) -> str | None:
    if style == "none":
        return None
    ramp = f"0.035*(on/{FPS})/{max(1.0, total):.3f}"
    if style == "subtle" or not switch_points:
        return f"1.0+{ramp}"
    # alternate 1.00 / 1.07 between sentence changes and cuts ("punch-in" jump-cut look)
    pts = [0.0] + switch_points + [total + 1]
    terms = []
    for i, (a, b) in enumerate(zip(pts, pts[1:])):
        if i % 2 == 1:
            terms.append(f"gte(on/{FPS},{a:.3f})*lt(on/{FPS},{b:.3f})*0.07")
    return f"1.0+{ramp}" + ("+" + "+".join(terms) if terms else "")


def _switch_points(timeline: Timeline, segs: list[dict], min_gap: float = 2.5) -> list[float]:
    cands = set(round(p, 3) for p in timeline.cut_points())
    for s in segs:
        t = timeline.to_output(s["s"])
        if t is not None:
            cands.add(round(t, 3))
    out: list[float] = []
    for p in sorted(cands):
        if p < 1.0 or p > timeline.duration - 1.0:
            continue
        if not out or p - out[-1] >= min_gap:
            out.append(p)
    return out


def render_clip(clip: dict, source: dict, transcript: dict, settings: dict) -> RenderResult:
    src = source["local_path"]
    info = probe(src)
    words = transcript.get("words", []) if transcript else []
    segs = transcript.get("segments", []) if transcript else []
    timeline = build_timeline(
        words, clip["start_s"], clip["end_s"], info["duration"],
        remove_silence=settings.get("remove_silence", True), max_pause=settings["max_pause_seconds"],
        min_len=settings["min_clip_seconds"], max_len=settings["max_clip_seconds"])

    out_dir = env().dir("clips") / f"clip_{clip['id']}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "short.mp4"

    src_w, src_h = info["width"], info["height"]
    plan = plan_crop(src, timeline, src_w, src_h, settings.get("layout", "auto"))
    zoom_style = settings.get("zoom_style", "punch")

    emphasis = json.loads(clip.get("emphasis_words") or "[]")
    banner = (clip.get("commentary") or clip.get("hook_text") or "").strip() if settings.get("add_hook_banner", True) else ""
    ass = build_ass(words, timeline, emphasis, font=env().subtitle_font, banner=banner,
                    words_per_line=int(settings.get("subtitle_words_per_line", 3)))
    (out_dir / "subs.ass").write_text(ass, encoding="utf-8")

    seek = max(0.0, timeline.keeps[0][0] - 2.0)
    n = len(timeline.keeps)
    has_audio = info["has_audio"]
    fg: list[str] = []
    fg.append(f"[0:v]split={n}" + "".join(f"[vs{i}]" for i in range(n)) if n > 1 else "[0:v]null[vs0]")
    if has_audio:
        fg.append(f"[0:a]asplit={n}" + "".join(f"[as{i}]" for i in range(n)) if n > 1 else "[0:a]anull[as0]")
    concat_in = ""
    for i, (a, b) in enumerate(timeline.keeps):
        ra, rb = a - seek, b - seek
        fg.append(f"[vs{i}]trim=start={ra:.3f}:end={rb:.3f},setpts=PTS-STARTPTS[v{i}]")
        if has_audio:
            d = rb - ra
            fg.append(f"[as{i}]atrim=start={ra:.3f}:end={rb:.3f},asetpts=PTS-STARTPTS,"
                      f"afade=t=in:d=0.02,afade=t=out:st={max(0, d - 0.03):.3f}:d=0.03[a{i}]")
            concat_in += f"[v{i}][a{i}]"
        else:
            concat_in += f"[v{i}]"
    fg.append(f"{concat_in}concat=n={n}:v=1:a={1 if has_audio else 0}[vc]" + ("[ac]" if has_audio else ""))

    # --- reframe to 9:16
    if plan.layout == "face_crop":
        crop_w = int(src_h * 9 / 16) // 2 * 2
        xexpr = x_expression(plan.keyframes, src_w, crop_w)
        fg.append(f"[vc]fps={FPS},crop={crop_w}:{src_h // 2 * 2}:x='{xexpr}':y=0,"
                  f"scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1[vr]")
    else:
        fg.append(f"[vc]fps={FPS},split[bgi][fgi]")
        fg.append(f"[bgi]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},"
                  f"boxblur=24:3,eq=brightness=-0.10:saturation=1.1[bg]")
        fg.append(f"[fgi]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease:flags=lanczos,setsar=1[fgs]")
        fg.append("[bg][fgs]overlay=x='(W-w)/2':y='max(0,(H-h)/2-110)',setsar=1[vr]")

    # --- dynamic zoom
    zexpr = _zoom_expr(zoom_style, _switch_points(timeline, segs), timeline.duration)
    if zexpr:
        fg.append(f"[vr]zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:"
                  f"s={OUT_W}x{OUT_H}:fps={FPS}[vz]")
    else:
        fg.append("[vr]null[vz]")
    fg.append("[vz]ass=subs.ass,format=yuv420p[vout]")
    if has_audio:
        fg.append("[ac]highpass=f=70,loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")

    (out_dir / "filter.txt").write_text(";\n".join(fg), encoding="utf-8")

    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{seek:.3f}", "-i", str(Path(src).resolve())]
    if not has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    cmd += ["-filter_complex_script", "filter.txt", "-map", "[vout]"]
    cmd += ["-map", "[aout]"] if has_audio else ["-map", "1:a", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-r", str(FPS), "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
            "-t", f"{min(timeline.duration, settings['max_clip_seconds']):.3f}",
            "-movflags", "+faststart", "short.mp4"]
    run(cmd, cwd=out_dir, timeout=3600)

    out_info = probe(out_path)
    edl = {"keeps": timeline.keeps, "layout": plan.layout, "face_ratio": round(plan.face_ratio, 2),
           "crop_keyframes": plan.keyframes, "zoom": zoom_style, "seek": seek}
    (out_dir / "edl.json").write_text(json.dumps(edl, indent=1))
    db.update("clips", clip["id"], duration_s=round(out_info["duration"], 2))
    log.info("clip %s rendered: %.1fs, %s, %d cuts", clip["id"], out_info["duration"], plan.layout, n - 1)
    return RenderResult(out_path, out_info["duration"], plan.layout, zoom_style, timeline, plan.face_ratio)


def make_thumbnail(clip_path: Path, out_path: Path) -> Path:
    """Pick the sharpest of several frames (Laplacian variance) as the thumbnail."""
    import cv2

    cap = cv2.VideoCapture(str(clip_path))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    best, best_score = None, -1.0
    for i in range(1, 10):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * i / 10))
        ok, frame = cap.read()
        if not ok:
            continue
        score = cv2.Laplacian(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
        if score > best_score:
            best, best_score = frame, score
    cap.release()
    if best is None:
        raise RuntimeError("could not read frames for thumbnail")
    cv2.imwrite(str(out_path), best, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return out_path
