# Sitevo — falling keycaps transition

A cinematic opening built on the office photo (`office.webp`): the camera slowly pushes in,
the cursor on the monitor clicks **LET'S BUILD →**, time slows down, and real 3D keycaps start
falling. First two or three, then more, then a wave of more than two thousand that covers
about 97% of the frame. Behind that wall of keys the scene changes, and the keys fall away into
the Sitevo intro (`../sitevo-intro`).

| File | What it is |
| --- | --- |
| `out/sitevo-keys-intro.mp4` | Full film: office → keys transition → Sitevo intro (≈ 38.7 s, 1080p60) |
| `out/sitevo-keys-transition.mp4` | Only the office + keys part (9 s), ends on black |
| `index.html` | Scene: photo plate, perspective-mapped monitor UI, three.js keycaps |
| `geom.mjs` | Camera calibration to the photo, desk plane, timeline, slow-motion ramp |
| `sim.mjs` | Deterministic rigid-body physics (cannon-es): keys, desk, floor, collisions |
| `audio.mjs` | Every physics contact becomes a plastic impact sound; mixed into the intro audio |
| `render.cjs` | Frame renderer (headless Chromium, WebGL) |

## How it is made

- **Camera match.** The 3D camera is calibrated to the photo (height 1.35 m, 7° down, ~62° FOV),
  so keys that fall on the desk land on the real desk surface and cast soft shadows on it.
- **Physics.** About 2,200 keycaps with real sizes (1u, 1.25u, 1.5u, 2u, spacebar) and gram-level
  masses fall under gravity, collide with each other, bounce on the desk, and drop to the floor.
- **Keycaps.** Sculpted caps: tapered sides, dished tops, hollow undersides, printed legends.
- **Camera optics.** Every frame averages 16–22 renders across a 180° shutter, a lens aperture
  and sub-pixel offsets. That gives true motion blur, depth of field and anti-aliasing.
  Focus racks onto the first keys, then back to the desk.
- **Slow motion.** At the click the action ramps to half speed, like a high-frame-rate shot.

## Limits

The man in the photo is a still image, so he does not move. The click happens on screen,
with the cursor pressing the button. Animating a real person photorealistically would need
generative video, which this pipeline does not use.

## Rebuild

```bash
../sitevo-intro/build.sh   # if the intro is not built yet
./build.sh                 # ~1 hour on CPU (software WebGL); much faster with a GPU
```

Preview one moment: serve the repository root over HTTP and open `sitevo-keys/index.html?t=7.9`.
