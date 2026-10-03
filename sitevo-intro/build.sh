#!/usr/bin/env bash
# Builds the Sitevo intro video: frames (Chromium) + soundtrack (synth) -> MP4.
set -euo pipefail
cd "$(dirname "$0")"
FPS="${FPS:-60}"

rm -rf out/frames
node render.cjs frames "$FPS"
node audio.cjs out/sitevo-intro.wav

# Fine animated grain also dithers the dark gradients so they don't band after compression.
ffmpeg -y -v error -framerate "$FPS" -i out/frames/f%05d.jpg -i out/sitevo-intro.wav \
  -vf "noise=alls=3:allf=t,format=yuv420p" \
  -c:v libx264 -preset slow -crf 17 -profile:v high -pix_fmt yuv420p \
  -c:a aac -b:a 192k -shortest -movflags +faststart \
  out/sitevo-intro.mp4

# Silent variant for autoplaying background use on a website (browsers block autoplay with sound).
ffmpeg -y -v error -i out/sitevo-intro.mp4 -an -c:v copy -movflags +faststart out/sitevo-intro-muted.mp4

# Poster frame (last frame) for the <video poster> attribute.
ffmpeg -y -v error -sseof -0.05 -i out/sitevo-intro.mp4 -frames:v 1 -q:v 2 out/sitevo-intro-poster.jpg
echo "done -> out/sitevo-intro.mp4"
