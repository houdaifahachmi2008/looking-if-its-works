#!/usr/bin/env bash
# Office scene -> falling keycaps transition -> Sitevo intro.
# Requires: node, ffmpeg, Playwright Chromium, and ../sitevo-intro already built (out/sitevo-intro.mp4 + .wav).
set -euo pipefail
cd "$(dirname "$0")"
[ -d node_modules ] || npm install

node sim.mjs                                   # physics: keycaps, desk, floor -> out/sim.bin, out/contacts.json
rm -rf out/frames
WORKERS="${WORKERS:-2}" node render.cjs frames 0 9.0 out/frames   # 540 frames, 60 fps (slow: software WebGL)
node audio.mjs                                 # impacts from the physics contacts + intro soundtrack -> out/keys-full.wav

# office segment (with fine grain to match the photo) + intro from 0.3 s, one continuous encode
ffmpeg -y -v error \
  -framerate 60 -i out/frames/f%05d.jpg \
  -ss 0.3 -i ../sitevo-intro/out/sitevo-intro.mp4 \
  -i out/keys-full.wav \
  -filter_complex "[0:v]noise=alls=4:allf=t,format=yuv420p,setpts=PTS-STARTPTS[a];[1:v]format=yuv420p,setpts=PTS-STARTPTS[b];[a][b]concat=n=2:v=1:a=0[v]" \
  -map "[v]" -map 2:a -c:v libx264 -preset slow -crf 17 -pix_fmt yuv420p -c:a aac -b:a 192k -shortest -movflags +faststart \
  out/sitevo-keys-intro.mp4

# standalone transition clip (office + keys, ends on black)
ffmpeg -y -v error -i out/sitevo-keys-intro.mp4 -t 9.0 -c:v libx264 -preset slow -crf 17 -c:a aac -b:a 192k -movflags +faststart out/sitevo-keys-transition.mp4
echo "done -> out/sitevo-keys-intro.mp4"
