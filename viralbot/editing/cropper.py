"""Smart 9:16 reframing.

Samples frames, detects faces (OpenCV Haar cascades – free, CPU, no model
downloads) and falls back to motion saliency. The horizontal track is median
filtered and stabilised with a dead-zone so the virtual camera holds still and
re-frames smoothly only when the subject really moves.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .timeline import Timeline

_cascades: dict = {}


def _cascade(name: str):
    if name not in _cascades:
        _cascades[name] = cv2.CascadeClassifier(cv2.data.haarcascades + name)
    return _cascades[name]


@dataclass
class CropPlan:
    layout: str                     # face_crop | blur_fill
    face_ratio: float
    keyframes: list[tuple[float, float]]  # (output time, x center 0..1)


def detect_track(video_path: str, keeps: list[tuple[float, float]], sample_fps: float = 2.0) -> tuple[list[tuple[float, float | None]], float]:
    cap = cv2.VideoCapture(video_path)
    frontal = _cascade("haarcascade_frontalface_default.xml")
    profile = _cascade("haarcascade_profileface.xml")
    samples: list[tuple[float, float | None]] = []
    prev_gray = None
    faces_found = 0
    for a, b in keeps:
        t = a
        while t <= b:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                break
            h, w = frame.shape[:2]
            scale = 480.0 / w if w > 480 else 1.0
            small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale != 1.0 else frame
            gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
            min_side = max(24, int(gray.shape[0] * 0.08))
            faces = frontal.detectMultiScale(gray, 1.1, 4, minSize=(min_side, min_side))
            if len(faces) == 0:
                faces = profile.detectMultiScale(gray, 1.1, 4, minSize=(min_side, min_side))
            cx = None
            if len(faces):
                x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
                cx = (x + fw / 2) / gray.shape[1]
                faces_found += 1
            elif prev_gray is not None and prev_gray.shape == gray.shape:
                diff = cv2.absdiff(gray, prev_gray)
                _, th = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
                m = cv2.moments(th)
                if m["m00"] > gray.size * 255 * 0.01:
                    cx = m["m10"] / m["m00"] / gray.shape[1]
            prev_gray = gray
            samples.append((t, cx))
            t += 1.0 / sample_fps
    cap.release()
    ratio = faces_found / max(1, len(samples))
    return samples, ratio


def plan_crop(video_path: str, timeline: Timeline, src_w: int, src_h: int, layout_pref: str = "auto",
              deadzone: float = 0.045) -> CropPlan:
    src_aspect = src_w / max(1, src_h)
    if src_aspect <= 9 / 16 + 0.02:
        return CropPlan("blur_fill", 0.0, [])  # already vertical: fit + blurred fill if needed
    samples, ratio = detect_track(video_path, timeline.keeps)
    layout = layout_pref
    if layout == "auto":
        layout = "face_crop" if ratio >= 0.35 else "blur_fill"
    if layout != "face_crop":
        return CropPlan("blur_fill", ratio, [])

    # crop window width as a fraction of source width; keep centre inside valid range
    half = (src_h * 9 / 16) / src_w / 2
    xs = [cx for _, cx in samples]
    # fill gaps with last known value (or centre)
    last = next((x for x in xs if x is not None), 0.5)
    filled = []
    for x in xs:
        last = x if x is not None else last
        filled.append(last)
    arr = np.array(filled, dtype=float)
    if len(arr) >= 5:
        k = 5
        padded = np.pad(arr, (k // 2, k // 2), mode="edge")
        arr = np.array([np.median(padded[i:i + k]) for i in range(len(arr))])
    arr = np.clip(arr, half, 1 - half)

    keyframes: list[tuple[float, float]] = []
    pos = float(arr[0]) if len(arr) else 0.5
    keyframes.append((0.0, pos))
    move_dur = 0.6
    for (t_src, _), x in zip(samples, arr):
        t_out = timeline.to_output(t_src)
        if t_out is None:
            continue
        if abs(x - pos) > deadzone and t_out > keyframes[-1][0] + 0.3:
            keyframes.append((t_out, pos))
            keyframes.append((t_out + move_dur, float(x)))
            pos = float(x)
    return CropPlan("face_crop", ratio, keyframes)


def x_expression(keyframes: list[tuple[float, float]], src_w: int, crop_w: int) -> str:
    """Piecewise-linear crop x(t) for FFmpeg's crop filter (pixels, left edge)."""
    def px(c: float) -> float:
        return max(0.0, min(src_w - crop_w, c * src_w - crop_w / 2))

    if not keyframes:
        return str(int((src_w - crop_w) / 2))
    if len(keyframes) == 1:
        return str(int(px(keyframes[0][1])))
    terms = []
    for (t0, c0), (t1, c1) in zip(keyframes, keyframes[1:]):
        x0, x1 = px(c0), px(c1)
        if t1 - t0 <= 1e-3:
            continue
        if abs(x1 - x0) < 0.5:
            terms.append(f"gte(t,{t0:.3f})*lt(t,{t1:.3f})*{x0:.1f}")
        else:
            terms.append(f"gte(t,{t0:.3f})*lt(t,{t1:.3f})*({x0:.1f}+({x1 - x0:.1f})*(t-{t0:.3f})/{t1 - t0:.3f})")
    tl, cl = keyframes[-1]
    terms.append(f"gte(t,{tl:.3f})*{px(cl):.1f}")
    return "+".join(terms)
