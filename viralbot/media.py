"""Thin FFmpeg / ffprobe helpers and audio feature extraction."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

from .logging_setup import get_logger

log = get_logger("media")


class MediaError(RuntimeError):
    pass


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise MediaError(f"{tool} not found on PATH – install FFmpeg (see README).")


def run(cmd: list[str], cwd: str | Path | None = None, timeout: int = 3600) -> subprocess.CompletedProcess:
    log.debug("run: %s", " ".join(map(str, cmd)))
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        tail = (p.stderr or "")[-2500:]
        raise MediaError(f"command failed ({p.returncode}): {' '.join(map(str, cmd[:6]))} ...\n{tail}")
    return p


def probe(path: str | Path) -> dict:
    p = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    data = json.loads(p.stdout)
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    dur = float(data.get("format", {}).get("duration") or (v or {}).get("duration") or 0)
    info = {"duration": dur, "has_audio": a is not None, "has_video": v is not None}
    if v:
        w, h = int(v["width"]), int(v["height"])
        rot = 0
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(sd["rotation"])
        if abs(rot) in (90, 270):
            w, h = h, w
        num, _, den = (v.get("avg_frame_rate") or "30/1").partition("/")
        fps = float(num) / float(den or 1) if float(den or 1) else 30.0
        info.update(width=w, height=h, fps=fps or 30.0, vcodec=v.get("codec_name"))
    if a:
        info.update(acodec=a.get("codec_name"), sample_rate=int(a.get("sample_rate") or 0))
    return info


def extract_audio_wav(src: str | Path, dst: str | Path, sr: int = 16000) -> Path:
    dst = Path(dst)
    if dst.exists() and dst.stat().st_size > 1000:
        return dst
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(sr), "-f", "wav", str(dst)])
    return dst


def rms_envelope(wav_path: str | Path, hop_s: float = 0.25) -> tuple[np.ndarray, float]:
    """Return (rms_db per hop, hop seconds) for a mono 16-bit wav."""
    with wave.open(str(wav_path), "rb") as wf:
        sr = wf.getframerate()
        n = wf.getnframes()
        raw = wf.readframes(n)
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    hop = max(1, int(sr * hop_s))
    m = len(x) // hop
    if m == 0:
        return np.array([-90.0]), hop_s
    frames = x[: m * hop].reshape(m, hop)
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    return 20 * np.log10(rms + 1e-9), hop_s


def loudness_stats(path: str | Path) -> dict:
    p = subprocess.run(["ffmpeg", "-v", "info", "-i", str(path), "-af", "volumedetect", "-vn", "-f", "null", "-"],
                       capture_output=True, text=True)
    out = {}
    for key in ("mean_volume", "max_volume"):
        m = re.search(rf"{key}:\s*(-?[\d.]+) dB", p.stderr)
        if m:
            out[key] = float(m.group(1))
    return out


def black_ratio(path: str | Path, duration: float) -> float:
    p = subprocess.run(["ffmpeg", "-v", "info", "-i", str(path), "-vf", "blackdetect=d=0.5:pix_th=0.10",
                        "-an", "-f", "null", "-"], capture_output=True, text=True)
    total = sum(float(x) for x in re.findall(r"black_duration:([\d.]+)", p.stderr))
    return total / duration if duration else 0.0


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def video_phash(path: str | Path, samples: int = 6) -> str:
    """Perceptual fingerprint: concatenated 64-bit dHash of evenly spaced frames (hex)."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    hashes = []
    for i in range(samples):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int((i + 0.5) * n / samples))
        ok, frame = cap.read()
        if not ok:
            hashes.append("0" * 16)
            continue
        g = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (9, 8), interpolation=cv2.INTER_AREA)
        bits = (g[:, 1:] > g[:, :-1]).flatten()
        hashes.append(f"{int(''.join('1' if b else '0' for b in bits), 2):016x}")
    cap.release()
    return "".join(hashes)


def phash_distance(a: str, b: str) -> float:
    """Mean Hamming distance (0-64) between two fingerprints of equal length."""
    if not a or not b or len(a) != len(b):
        return 64.0
    ds = []
    for i in range(0, len(a), 16):
        ds.append(bin(int(a[i:i + 16], 16) ^ int(b[i:i + 16], 16)).count("1"))
    return float(np.mean(ds))
