// Shared camera / scene geometry, calibrated to office.webp (1672x941).
// World: metres, y up. Camera at (0, CAM_H, 0) looking down -Z, pitched up slightly.
export const PHOTO = { w: 1672, h: 941, cx: 836, cy: 470.5, f: 1391 };   // ~62 deg horizontal FOV
export const OUT = { w: 1920, h: 1080 };
export const K = OUT.w / PHOTO.w;                                          // photo px -> output px
export const CAM_H = 1.35;
export const PITCH = -7.0 * Math.PI / 180;
export const DESK_Y = 0.75;

// world-space direction of the ray through photo pixel (u, v)
export function pixelRay(u, v) {
  const x = u - PHOTO.cx, y = -(v - PHOTO.cy), z = -PHOTO.f;
  const c = Math.cos(PITCH), s = Math.sin(PITCH);
  const d = [x, y * c - z * s, y * s + z * c];
  const n = Math.hypot(...d); return d.map(a => a / n);
}
export function rayToHeight(u, v, h) {
  const d = pixelRay(u, v); const t = (h - CAM_H) / d[1];
  return [d[0] * t, h, d[2] * t];
}
// project a world point to photo pixels
export function project(p) {
  const x = p[0], y = p[1] - CAM_H, z = p[2];
  const c = Math.cos(-PITCH), s = Math.sin(-PITCH);
  const yc = y * c - z * s, zc = y * s + z * c;
  return [PHOTO.cx + PHOTO.f * x / -zc, PHOTO.cy - PHOTO.f * yc / -zc];
}
// frustum helpers at forward distance D (metres along -z)
export const topY = D => CAM_H + 0.2068 * D;
export const botY = D => CAM_H - 0.4812 * D;
export const halfW = D => D * 0.605;

// desk: front edge through two picked photo points, depth into the desk 0.7 m
const P1 = rayToHeight(1172, 760, DESK_Y), P2 = rayToHeight(1622, 810, DESK_Y);
const ex = P2[0] - P1[0], ez = P2[2] - P1[2], el = Math.hypot(ex, ez);
export const DESK = {
  p1: P1, p2: P2,
  e: [ex / el, ez / el],                 // along the front edge (towards the right / camera)
  n: [ez / el, -ex / el],                // into the desk (away from camera)
  depth: 0.7,
  yaw: Math.atan2(-ez / el, ex / el),
};
DESK.n = (() => { const a = [ez / el, -ex / el]; const toCam = [-P1[0], -P1[2]]; return (a[0] * toCam[0] + a[1] * toCam[1]) > 0 ? [-a[0], -a[1]] : a; })();
// point on desk: s along the edge from P1 (metres), r into the desk (metres)
export const deskPoint = (s, r) => [P1[0] + DESK.e[0] * s + DESK.n[0] * r, DESK_Y, P1[2] + DESK.e[1] * s + DESK.n[1] * r];

/* ---------------- timeline (video seconds) */
export const T = {
  CLICK: 3.0,      // cursor clicks LET'S BUILD
  SIM0: 3.1,       // physics t=0
  SWAP: 8.15,      // keys cover ~97% of the frame -> office plate replaced by the next scene
  END: 9.0,        // office segment ends; the Sitevo intro continues from INTRO_IN
  INTRO_IN: 0.3,
};
// slow-motion speed ramp for the falling keys
export function speed(tv) {
  const sm = (a, b, x) => { const k = Math.min(Math.max((x - a) / (b - a), 0), 1); return k * k * (3 - 2 * k); };
  return 1 - 0.5 * sm(3.02, 3.4, tv);
}
const SIM_TABLE = (() => { const dt = 0.0005, n = Math.ceil(12 / dt); const a = new Float64Array(n + 1); let s = 0; for (let i = 1; i <= n; i++) { const tv = T.SIM0 + (i - .5) * dt; s += speed(tv) * dt; a[i] = s; } return { dt, a }; })();
export function simTime(tv) {
  if (tv <= T.SIM0) return tv - T.SIM0;
  const x = (tv - T.SIM0) / SIM_TABLE.dt, i = Math.min(Math.floor(x), SIM_TABLE.a.length - 2), f = x - i;
  return SIM_TABLE.a[i] * (1 - f) + SIM_TABLE.a[i + 1] * f;
}
export function videoTime(ts) { // inverse (bisection)
  let lo = T.SIM0 - 1, hi = T.SIM0 + 12;
  for (let k = 0; k < 50; k++) { const m = (lo + hi) / 2; if (simTime(m) < ts) lo = m; else hi = m; }
  return (lo + hi) / 2;
}

/* ---------------- keycap variants */
export const U = 0.019;                   // key pitch
export const VARIANTS = [
  // name, width(m), depth, height, legend list, weight
  { name: '1u', w: 0.0181, d: 0.0181, h: 0.0105, w8: 0.86 },
  { name: '125u', w: 0.0181 + U * 0.25, d: 0.0181, h: 0.0102, w8: 0.045 },
  { name: '15u', w: 0.0181 + U * 0.5, d: 0.0181, h: 0.0102, w8: 0.045 },
  { name: '2u', w: 0.0181 + U, d: 0.0181, h: 0.0100, w8: 0.035 },
  { name: 'space', w: 0.0181 + U * 5.25, d: 0.0181, h: 0.0095, w8: 0.015 },
];
export const LEGENDS_1U = 'QWERTYUIOPASDFGHJKLZXCVBNM1234567890'.split('');
export const LEGENDS_MOD = { '125u': ['ctrl', 'alt', 'fn'], '15u': ['tab', 'del'], '2u': ['shift', 'enter'], space: [''] };
