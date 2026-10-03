// Sound design for the office / falling-keys segment, then mixed into the Sitevo intro soundtrack.
//   node audio.mjs  -> out/keys-full.wav (office segment + intro audio from T.INTRO_IN)
import fs from 'fs';
import { T, videoTime, VARIANTS } from './geom.mjs';

const SR = 48000, TAU = Math.PI * 2;
const OFFICE_LEN = T.END + 1.6;                         // include tails
const INTRO_WAV = '../sitevo-intro/out/sitevo-intro.wav';
const N = Math.round(OFFICE_LEN * SR);
const L = new Float32Array(N), R = new Float32Array(N), VL = new Float32Array(N), VR = new Float32Array(N);

let seed = 99;
const rnd = () => { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
const white = () => rnd() * 2 - 1;
class BQ { constructor(type, f, q = .707) { this.type = type; this.x1 = this.x2 = this.y1 = this.y2 = 0; this.set(f, q); }
  set(f, q = this.q) { this.q = q; const w = TAU * Math.min(f, SR * .45) / SR, c = Math.cos(w), s = Math.sin(w), a = s / (2 * q); let b0, b1, b2;
    if (this.type === 'lp') { b0 = (1 - c) / 2; b1 = 1 - c; b2 = b0; } else if (this.type === 'hp') { b0 = (1 + c) / 2; b1 = -(1 + c); b2 = b0; } else { b0 = a; b1 = 0; b2 = -a; }
    const a0 = 1 + a; this.b0 = b0 / a0; this.b1 = b1 / a0; this.b2 = b2 / a0; this.a1 = -2 * c / a0; this.a2 = (1 - a) / a0; }
  run(x) { const y = this.b0 * x + this.b1 * this.x1 + this.b2 * this.x2 - this.a1 * this.y1 - this.a2 * this.y2; this.x2 = this.x1; this.x1 = x; this.y2 = this.y1; this.y1 = y; return y; } }
const expd = (t, tau) => Math.exp(-t / tau);
function put(t0, len, fn, { gain = 1, pan = 0, verb = 0 } = {}) {
  const i0 = Math.round(t0 * SR), n = Math.round(len * SR);
  const gl = Math.cos((pan + 1) * Math.PI / 4) * Math.SQRT2, gr = Math.sin((pan + 1) * Math.PI / 4) * Math.SQRT2;
  for (let k = 0; k < n; k++) { const i = i0 + k; if (i < 0 || i >= N) continue; const v = fn(k / SR) * gain; L[i] += v * gl; R[i] += v * gr; if (verb) { VL[i] += v * gl * verb; VR[i] += v * gr * verb; } }
}

/* ---------- room: HVAC rumble, distant city, PC fan */
{
  const lp = new BQ('lp', 180), hp = new BQ('hp', 30), fan = new BQ('bp', 420, 1.2), city = new BQ('bp', 900, .5);
  put(0, OFFICE_LEN, s => {
    const fade = Math.min(s / 1.4, 1) * (s > T.SWAP ? Math.max(0, 1 - (s - T.SWAP) / .5) : 1);
    const n = white();
    return fade * (hp.run(lp.run(n)) * 1.1 + fan.run(white()) * .25 * (1 + .1 * Math.sin(TAU * .3 * s)) + city.run(white()) * .12 + .03 * Math.sin(TAU * 120 * s));
  }, { gain: .018 });
}
/* ---------- low cinematic drone (connects to the intro's key) */
{
  const notes = [36.71, 55, 73.42, 110]; // D1 A1 D2 A2
  notes.forEach((f, j) => put(0.4, OFFICE_LEN, s => {
    const tv = s + 0.4;
    const env = Math.min(s / 2.5, 1) * (1 + 1.6 * Math.min(Math.max((tv - T.CLICK) / (T.SWAP - T.CLICK), 0), 1) ** 2) * (tv > T.SWAP ? Math.max(0, 1 - (tv - T.SWAP) / .08) : 1);
    return env * (Math.sin(TAU * f * s + Math.sin(TAU * .11 * s) * .6) + .3 * Math.sin(TAU * f * 2.003 * s));
  }, { gain: .012 / (j + 1) * (j === 0 ? 1.6 : 1), pan: (j % 2 ? .3 : -.3) }));
}
/* ---------- mouse click + UI confirm from the monitor */
function click(t, g = .25) {
  const hp = new BQ('hp', 1800), bp = new BQ('bp', 3400, 3);
  put(t, .06, s => (hp.run(white()) * expd(s, .0009) * 1.2 + bp.run(white()) * expd(s, .004) + .4 * Math.sin(TAU * 1900 * s) * expd(s, .003)), { gain: g, pan: .25, verb: .15 });
}
click(T.CLICK, .22); click(T.CLICK + .085, .14);
[[1318.5, 0], [1975.5, .07]].forEach(([f, d]) => put(T.CLICK + .05 + d, 1.2, s => Math.sin(TAU * f * s) * Math.min(s / .004, 1) * expd(s, .25), { gain: .02, pan: .3, verb: .5 }));
/* ---------- time slows: soft sub drop + reverse air swell */
{ let ph = 0; put(T.CLICK + .02, 2.2, s => { const f = 70 * Math.pow(.5, s / .8) + 30; ph += TAU * f / SR; return Math.sin(ph) * Math.min(s / .02, 1) * expd(s, .7); }, { gain: .16, verb: .2 }); }
{ const bp = new BQ('bp', 300, .8); put(T.CLICK - .2, .55, s => { const k = s / .55; bp.set(300 + 2400 * k * k, .8); return bp.run(white()) * k * k * (k > .9 ? (1 - k) * 10 : 1); }, { gain: .08, verb: .4 }); }
/* ---------- falling-keys whoosh / air rush, peaking at the swap */
{
  const bp = new BQ('bp', 400, .6), lp = new BQ('lp', 900);
  const a = 6.7, b = T.SWAP + .45;
  put(a, b - a, s => {
    const tv = a + s, k = (tv - a) / (T.SWAP - a);
    const env = tv < T.SWAP ? Math.pow(Math.max(k, 0), 2.4) : Math.max(0, 1 - (tv - T.SWAP) / .45) ** 2;
    bp.set(350 + 2600 * Math.min(k, 1), .6); const n = white();
    return env * (bp.run(n) * 1.3 + lp.run(n) * .8);
  }, { gain: .4, verb: .25 });
}
/* ---------- transition hit at the swap */
{ let ph = 0; put(T.SWAP, 2.4, s => { const f = 30 + 50 * expd(s, .08); ph += TAU * f / SR; return Math.sin(ph) * Math.min(s / .004, 1) * expd(s, .6); }, { gain: .28, verb: .35 }); }
{ const lp = new BQ('lp', 260); put(T.SWAP, .8, s => lp.run(white()) * expd(s, .12), { gain: .35, verb: .4 }); }

/* ---------- keycap impacts from the physics contacts */
const contacts = JSON.parse(fs.readFileSync('out/contacts.json', 'utf8'));
let used = 0;
for (const [ts, v, x, y, z, type, vi] of contacts) {
  const tv = videoTime(ts); if (tv < T.SIM0 || tv > T.END + .6) continue;
  const dist = Math.hypot(x, y - 1.35, z) + 0.25;
  let g = Math.min(1, Math.pow(v / 3, 1.25)) / dist;
  if (type === 'key') g *= .35; else if (type === 'floor') g *= .55;
  if (tv > T.SWAP) g *= Math.max(0, 1 - (tv - T.SWAP) / .35);        // the next scene takes over
  if (g < .004) continue;
  used++;
  const size = VARIANTS[vi].w / 0.0181;
  const base = (type === 'desk' ? 2600 : type === 'floor' ? 1700 : 3800) / Math.pow(size, .35) * (0.85 + .3 * rnd()) * 0.82; // a touch lower in slow motion
  const modes = [[1, 1], [1.73, .55], [2.61, .35], [3.9, .2]].map(([r, a]) => [base * r, a, (type === 'floor' ? .010 : .022) / Math.sqrt(r) * 1.4]);
  const hp = new BQ('hp', type === 'floor' ? 900 : 2500);
  const pan = Math.max(-.9, Math.min(.9, x / Math.max(.3, -z) * 1.2));
  const ph0 = rnd() * TAU;
  put(tv, .09, s => {
    let o = hp.run(white()) * expd(s, .0008) * .9;
    for (const [f, a, tau] of modes) o += a * Math.sin(TAU * f * s + ph0) * expd(s, tau);
    return o;
  }, { gain: g * .32, pan, verb: type === 'desk' ? .12 : .2 });
}
console.log('impacts used', used, 'of', contacts.length);

/* ---------- small room reverb */
function verb(inL, inR) {
  const combs = [1116, 1188, 1277, 1356, 1422, 1491], aps = [556, 441, 341];
  const outs = [new Float32Array(N), new Float32Array(N)];
  [inL, inR].forEach((x, ch) => {
    const cs = combs.map(n => ({ b: new Float32Array(Math.round((n + ch * 23) * SR / 44100 * .7)), i: 0, f: 0 })), as = aps.map(n => ({ b: new Float32Array(Math.round((n + ch * 23) * SR / 44100)), i: 0 }));
    for (let i = 0; i < N; i++) { const inp = x[i] * .02; let s = 0;
      for (const c of cs) { const y = c.b[c.i]; c.f = y * .6 + c.f * .4; c.b[c.i] = inp + c.f * .76; c.i = (c.i + 1) % c.b.length; s += y; }
      for (const a of as) { const y = a.b[a.i]; const v = -s + y; a.b[a.i] = s + y * .5; a.i = (a.i + 1) % a.b.length; s = v; }
      outs[ch][i] = s; }
  });
  return outs;
}
const [wl, wr] = verb(VL, VR);

/* ---------- read intro audio and mix */
function readWav(f) {
  const b = fs.readFileSync(f); let o = 12, fmt, data;
  while (o < b.length) { const id = b.toString('ascii', o, o + 4), sz = b.readUInt32LE(o + 4); if (id === 'fmt ') fmt = { ch: b.readUInt16LE(o + 10), sr: b.readUInt32LE(o + 12), bits: b.readUInt16LE(o + 22) }; if (id === 'data') data = b.subarray(o + 8, o + 8 + sz); o += 8 + sz + (sz & 1); }
  const n = data.length / (2 * fmt.ch), l = new Float32Array(n), r = new Float32Array(n);
  for (let i = 0; i < n; i++) { l[i] = data.readInt16LE(i * 2 * fmt.ch) / 32768; r[i] = data.readInt16LE(i * 2 * fmt.ch + 2) / 32768; }
  return { l, r, sr: fmt.sr };
}
const intro = readWav(INTRO_WAV);
const i0 = Math.round(T.INTRO_IN * SR), introN = intro.l.length - i0;
const TOT = Math.round(T.END * SR) + introN;
const oL = new Float32Array(TOT), oR = new Float32Array(TOT);
let peakO = 0; for (let i = 0; i < N; i++) peakO = Math.max(peakO, Math.abs(L[i] + wl[i] * 2), Math.abs(R[i] + wr[i] * 2));
const og = 0.85 / peakO;
for (let i = 0; i < N && i < TOT; i++) { oL[i] += Math.tanh((L[i] + wl[i] * 2) * og * 1.1) / 1.1; oR[i] += Math.tanh((R[i] + wr[i] * 2) * og * 1.1) / 1.1; }
const off = Math.round(T.END * SR);
for (let i = 0; i < introN; i++) { oL[off + i] += intro.l[i0 + i] * .95; oR[off + i] += intro.r[i0 + i] * .95; }
let pk = 0; for (let i = 0; i < TOT; i++) pk = Math.max(pk, Math.abs(oL[i]), Math.abs(oR[i]));
const ng = pk > .97 ? .97 / pk : 1;
const buf = Buffer.alloc(44 + TOT * 4);
buf.write('RIFF', 0); buf.writeUInt32LE(36 + TOT * 4, 4); buf.write('WAVE', 8); buf.write('fmt ', 12); buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(2, 22);
buf.writeUInt32LE(SR, 24); buf.writeUInt32LE(SR * 4, 28); buf.writeUInt16LE(4, 32); buf.writeUInt16LE(16, 34); buf.write('data', 36); buf.writeUInt32LE(TOT * 4, 40);
for (let i = 0; i < TOT; i++) { buf.writeInt16LE(Math.round(Math.max(-1, Math.min(1, oL[i] * ng)) * 32767), 44 + i * 4); buf.writeInt16LE(Math.round(Math.max(-1, Math.min(1, oR[i] * ng)) * 32767), 46 + i * 4); }
fs.writeFileSync('out/keys-full.wav', buf);
console.log('audio -> out/keys-full.wav', (TOT / SR).toFixed(2) + 's');
