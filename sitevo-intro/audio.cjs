#!/usr/bin/env node
/*
 * Deterministic sound design + score for the Sitevo intro (30 s, 48 kHz stereo).
 * Every cue time below matches the visual timeline in index.html.
 *   node audio.cjs [out.wav]
 */
const fs = require('fs');
const path = require('path');

const SR = 48000, DUR = 30, N = SR * DUR;
const TAU = Math.PI * 2;
const L = new Float32Array(N), R = new Float32Array(N);       // dry SFX bus
const RL = new Float32Array(N), RR = new Float32Array(N);     // reverb send
const ML = new Float32Array(N), MR = new Float32Array(N);     // music bus

/* ---------- deterministic noise */
let seed = 1337;
const rnd = () => { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
const white = () => rnd() * 2 - 1;

/* ---------- biquad (RBJ) */
class Biquad {
  constructor(type, f, q = 0.707) { this.type = type; this.x1 = this.x2 = this.y1 = this.y2 = 0; this.set(f, q); }
  set(f, q = this.q) {
    this.q = q; const w = TAU * Math.min(f, SR * 0.45) / SR, c = Math.cos(w), s = Math.sin(w), a = s / (2 * q);
    let b0, b1, b2, a0, a1, a2;
    if (this.type === 'lp') { b0 = (1 - c) / 2; b1 = 1 - c; b2 = b0; }
    else if (this.type === 'hp') { b0 = (1 + c) / 2; b1 = -(1 + c); b2 = b0; }
    else { b0 = a; b1 = 0; b2 = -a; } // band-pass (0 dB peak)
    a0 = 1 + a; a1 = -2 * c; a2 = 1 - a;
    this.b0 = b0 / a0; this.b1 = b1 / a0; this.b2 = b2 / a0; this.a1 = a1 / a0; this.a2 = a2 / a0;
  }
  run(x) { const y = this.b0 * x + this.b1 * this.x1 + this.b2 * this.x2 - this.a1 * this.y1 - this.a2 * this.y2; this.x2 = this.x1; this.x1 = x; this.y2 = this.y1; this.y1 = y; return y; }
}

/* ---------- mixing helpers */
const idx = t => Math.round(t * SR);
function put(t0, len, fn, { gain = 1, pan = 0, verb = 0, bus = 'sfx' } = {}) {
  const i0 = idx(t0), n = Math.round(len * SR);
  const gl = Math.cos((pan + 1) * Math.PI / 4) * Math.SQRT2, gr = Math.sin((pan + 1) * Math.PI / 4) * Math.SQRT2;
  const [A, B] = bus === 'music' ? [ML, MR] : [L, R];
  for (let k = 0; k < n; k++) {
    const i = i0 + k; if (i < 0 || i >= N) continue;
    const r = fn(k / SR, k);
    let v, p = pan;
    if (Array.isArray(r)) { v = r[0]; p = r[1]; } else v = r;
    v *= gain;
    let l = gl, rr = gr;
    if (Array.isArray(r)) { l = Math.cos((p + 1) * Math.PI / 4) * Math.SQRT2; rr = Math.sin((p + 1) * Math.PI / 4) * Math.SQRT2; }
    A[i] += v * l; B[i] += v * rr;
    if (verb) { RL[i] += v * l * verb; RR[i] += v * rr * verb; }
  }
}
const expd = (t, tau) => Math.exp(-t / tau);
const att = (t, a) => t < a ? t / a : 1;

/* ---------- sound recipes */
function chime(t, notes, { gain = .1, tau = .45, verb = .45, pan = 0 } = {}) {
  notes.forEach(([f, dt], j) => put(t + dt, 2.2, s => {
    const e = att(s, .004) * expd(s, tau);
    return e * (Math.sin(TAU * f * s) + .22 * Math.sin(TAU * f * 2.76 * s) * expd(s, tau * .4) + .1 * Math.sin(TAU * f * 2 * s));
  }, { gain, pan: pan + (j ? .12 : -.08), verb }));
}
function tick(t, f = 2400, { gain = .05, pan = 0, verb = .2, body = 0 } = {}) {
  const hp = new Biquad('hp', 2500);
  put(t, .09, s => {
    const n = s < .004 ? hp.run(white()) * expd(s, .0012) : 0;
    return Math.sin(TAU * f * s) * att(s, .0015) * expd(s, .012) + .5 * n + body * Math.sin(TAU * 150 * s) * expd(s, .02);
  }, { gain, pan, verb });
}
function thock(t, { gain = .12, pan = 0, f = 120 } = {}) {
  const lp = new Biquad('lp', 1800);
  put(t, .35, s => {
    const fr = f * (1 + 1.2 * expd(s, .012));
    return Math.sin(TAU * fr * s) * att(s, .002) * expd(s, .06) + .25 * lp.run(white()) * expd(s, .01);
  }, { gain, pan, verb: .25 });
}
function key(t, { gain = .03, pan = 0 } = {}) {
  const bp = new Biquad('bp', 2200 + rnd() * 1400, 1.6);
  const f = 170 + rnd() * 60;
  put(t, .05, s => bp.run(white()) * att(s, .0008) * expd(s, .004) * 1.8 + .35 * Math.sin(TAU * f * s) * expd(s, .008), { gain, pan, verb: .08 });
}
function typing(t0, t1, gain = .028) {
  let t = t0;
  while (t < t1) { key(t, { gain: gain * (.6 + .5 * rnd()), pan: (rnd() - .5) * .2 }); t += .055 + rnd() * .075; }
}
function pop(t, up = true, { gain = .07, pan = 0 } = {}) {
  put(t, .18, s => {
    const k = Math.min(s / .07, 1);
    const f = up ? 520 * Math.pow(2.1, k) : 1150 * Math.pow(.55, k);
    return Math.sin(TAU * f * s) * att(s, .003) * expd(s, .045);
  }, { gain, pan, verb: .25 });
  tick(t, up ? 3000 : 2200, { gain: gain * .35, pan, verb: .1 });
}
function click(t, { gain = .32 } = {}) {
  const hp = new Biquad('hp', 2200);
  put(t, .12, s => hp.run(white()) * expd(s, .0011) * 1.4 + .6 * Math.sin(TAU * 2350 * s) * expd(s, .004) + .5 * Math.sin(TAU * 120 * s) * att(s, .002) * expd(s, .025), { gain, verb: .18 });
}
function whoosh(t, dur, f0, f1, { gain = .2, p0 = -.6, p1 = .6, q = .8, peak = .45, verb = .3, body = .5 } = {}) {
  const bp = new Biquad('bp', f0, q), lp = new Biquad('lp', 400);
  put(t, dur, (s) => {
    const k = s / dur;
    const env = k < peak ? Math.pow(Math.sin(Math.PI / 2 * k / peak), 2) : Math.pow(Math.cos(Math.PI / 2 * (k - peak) / (1 - peak)), 2);
    const f = k < peak ? f0 * Math.pow(f1 / f0, k / peak) : f1 * Math.pow((f0 * 1.4) / f1, (k - peak) / (1 - peak));
    bp.set(f, q);
    const n = white();
    return [env * (bp.run(n) * 1.6 + body * lp.run(n)), p0 + (p1 - p0) * k];
  }, { gain, verb });
}
function sub(t, f0, f1, dur, { gain = .4, tau = .5, verb = .2 } = {}) {
  let ph = 0;
  put(t, dur, s => { const f = f1 + (f0 - f1) * expd(s, dur * .25); ph += TAU * f / SR; return Math.sin(ph) * att(s, .004) * expd(s, tau); }, { gain, verb });
}
function shimmer(t, { gain = .03, dur = 2.4, verb = .7, base = [2093, 2637, 3136, 3951] } = {}) {
  base.forEach((f, j) => put(t + j * .05, dur, s => {
    const e = Math.min(s / .25, 1) * expd(Math.max(0, s - .25), dur * .3);
    return e * Math.sin(TAU * f * s + Math.sin(TAU * 5 * s) * .4);
  }, { gain: gain / base.length * 2, pan: (j / (base.length - 1) - .5) * .8, verb }));
}
function riser(t, dur, { gain = .1 } = {}) {
  const bp = new Biquad('bp', 400, 1.2);
  put(t, dur, s => {
    const k = s / dur; bp.set(400 * Math.pow(10, k), 1.2);
    const env = Math.pow(k, 2) * (k > .9 ? (1 - k) / .1 : 1);
    return env * (bp.run(white()) * 1.4 + .25 * Math.sin(TAU * (180 + 520 * k * k) * s));
  }, { gain, verb: .35 });
}
function sweepTone(t, dur, f0, f1, { gain = .05, verb = .5 } = {}) {
  [-1, 1].forEach((d, j) => {
    let ph = 0;
    put(t, dur, s => {
      const k = s / dur; const f = f0 * Math.pow(f1 / f0, E3(k)) * (1 + d * .006);
      ph += TAU * f / SR;
      return Math.sin(Math.PI * Math.min(k * 1.6, 1)) ** 2 * (k > .6 ? Math.cos((k - .6) / .4 * Math.PI / 2) ** 2 : 1) * (Math.sin(ph) + .2 * Math.sin(2 * ph));
    }, { gain, pan: d * .45, verb });
  });
}
const E3 = k => k < .5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;

/* ================================================================ SFX timeline */
// ambience: quiet room tone / digital air across the whole piece
{
  const lp = new Biquad('lp', 260), hp = new Biquad('hp', 35), air = new Biquad('hp', 7000);
  let b = 0;
  put(0, DUR, (s) => {
    const n = white(); b = .985 * b + .015 * n;
    const fade = Math.min(s / 1.2, 1) * (s > 29 ? (30 - s) : 1);
    return fade * (hp.run(lp.run(n)) * .9 + b * 1.5 + air.run(n) * .08);
  }, { gain: .012, verb: 0 });
}
// Scene 1 - notification
chime(0.95, [[1318.5, 0], [1975.5, .085]], { gain: .085, tau: .4, verb: .5 });
whoosh(1.98, .95, 300, 1400, { gain: .045, p0: 0, p1: 0, q: .7, peak: .5, body: .2, verb: .3 });
// Scene 2 - client message
typing(2.86, 3.28, .022);
pop(3.35, true, { gain: .07, pan: -.15 });
// Scene 3 - Sitevo replies
typing(4.52, 5.16, .02);
pop(5.22, false, { gain: .055, pan: .15 });
typing(5.68, 6.06, .02);
pop(6.12, false, { gain: .055, pan: .15 });
// Scene 4 - build button
tick(6.95, 1760, { gain: .045, verb: .35 });
shimmer(7.0, { gain: .012, dur: 1.6, base: [1760, 2637] });
tick(8.12, 900, { gain: .02, verb: .1 });
click(8.55);
// Scene 5 - the chat breaks apart
sub(8.87, 95, 38, 2.2, { gain: .4, tau: .55, verb: .25 });
whoosh(8.86, 1.9, 140, 2600, { gain: .26, p0: -.7, p1: .7, q: .65, peak: .32, body: .9, verb: .4 });
whoosh(9.0, 1.6, 900, 5200, { gain: .06, p0: .5, p1: -.5, q: 1.4, peak: .3, body: 0, verb: .5 });
tick(10.45, 1320, { gain: .03, verb: .4 }); tick(10.6, 1980, { gain: .025, verb: .4 });
// Scene 6 - components appear
whoosh(11.88, .7, 1200, 4800, { gain: .03, p0: -.4, p1: .4, q: 1.2, peak: .6, body: 0, verb: .3 });
[12.1, 12.24, 12.38, 12.52].forEach((t, i) => tick(t, 2200 + i * 180, { gain: .04, pan: .2 + i * .1, verb: .25 }));
thock(12.97, { gain: .13, f: 110 }); thock(13.14, { gain: .11, f: 98 });
tick(13.96, 1900, { gain: .035, verb: .3 });
whoosh(14.42, .4, 2000, 6000, { gain: .03, p0: 0, p1: 0, q: 1, peak: .4, body: 0 });
pop(14.52, true, { gain: .05 });
[15.0, 15.13, 15.26].forEach((t, i) => tick(t, 1500 + i * 260, { gain: .03, pan: -.4 + i * .4, verb: .35 }));
shimmer(15.25, { gain: .016, dur: 2.2, base: [1046.5, 1568, 2093] });
// Scene 7 - layers
whoosh(17.0, 1.25, 120, 900, { gain: .12, p0: -.3, p1: .3, q: .7, peak: .55, body: .8, verb: .45 });
sub(17.05, 70, 52, 1.6, { gain: .16, tau: .7 });
[17.55, 18.0, 18.45, 18.9, 19.35, 19.8].forEach((t, i) => tick(t, [1046.5, 1174.7, 1318.5, 1568, 1760, 2093][i] * 1.5, { gain: .045, pan: i < 3 ? -.35 : .35, verb: .4 }));
whoosh(18.45, .7, 600, 2800, { gain: .04, p0: -.6, p1: -.6, q: 1, peak: .4, body: .2 });
[18.9, 19.35, 19.8].forEach(t => whoosh(t, .55, 800, 3600, { gain: .03, p0: .6, p1: .6, q: 1, peak: .35, body: .2 }));
chime(20.16, [[1318.5, 0], [1760, .07], [2637, .14]], { gain: .045, tau: .35, verb: .6 });
whoosh(20.3, .72, 3000, 200, { gain: .1, p0: .4, p1: -.1, q: .8, peak: .85, body: .6, verb: .3 });
thock(20.98, { gain: .14, f: 80 });
// Scene 8 - responsive
whoosh(21.0, 1.5, 180, 3200, { gain: .14, p0: -.2, p1: .2, q: .7, peak: .35, body: .7, verb: .5 });
sweepTone(21.02, 1.5, 220, 880, { gain: .04 });
whoosh(22.0, 1.2, 1500, 4500, { gain: .025, p0: .7, p1: .5, q: 1.5, peak: .5, body: 0 });
[22.45, 22.55, 22.65].forEach((t, i) => tick(t, 2600, { gain: .028, pan: [-.6, 0, .6][i], verb: .3 }));
// Scene 9 - final website
riser(24.0, 1.3, { gain: .07 });
whoosh(24.0, 1.4, 160, 1400, { gain: .1, p0: 0, p1: 0, q: .7, peak: .5, body: .8, verb: .4 });
thock(24.74, { gain: .1, f: 104 }); thock(24.89, { gain: .09, f: 92 });
tick(25.36, 1900, { gain: .03, verb: .3 });
whoosh(25.75, .9, 2500, 7000, { gain: .045, p0: -.9, p1: .9, q: 1.6, peak: .5, body: 0, verb: .4 });
pop(26.27, true, { gain: .045 });
// Scene 10 - brand
whoosh(27.0, 1.1, 1800, 220, { gain: .06, p0: 0, p1: 0, q: .7, peak: .25, body: .6, verb: .5 });
sub(28.06, 58, 34, 3.0, { gain: .3, tau: .9, verb: .35 });
{ const lp = new Biquad('lp', 180); put(28.06, 1.2, s => lp.run(white()) * expd(s, .25) * att(s, .003), { gain: .28, verb: .4 }); }
shimmer(28.45, { gain: .022, dur: 2.0, base: [1174.7, 1760, 2349] });

/* ================================================================ score */
// band-limited saw wavetable
const TB = 4096, saw = new Float32Array(TB);
for (let i = 0; i < TB; i++) { let v = 0; for (let h = 1; h <= 10; h++) v += Math.sin(TAU * h * i / TB) / h; saw[i] = v * .55; }
const tab = p => { const x = (p - Math.floor(p)) * TB, i = x | 0, f = x - i; return saw[i] * (1 - f) + saw[(i + 1) % TB] * f; };
const n2f = n => 440 * Math.pow(2, (n - 69) / 12);
const CH = {
  Dm9: [50, 57, 60, 64, 65], Bb9: [46, 53, 57, 60, 62], F7: [41, 53, 57, 60, 64], C69: [48, 55, 57, 62, 64], Dfin: [38, 50, 57, 64, 65, 69],
};
const PROG = [ // [start, end, chord]
  [2.0, 9.4, 'Dm9'], [8.85, 12.9, 'Bb9'], [12.5, 16.4, 'F7'], [16.0, 19.9, 'C69'], [19.5, 23.4, 'Dm9'], [23.0, 26.9, 'Bb9'], [26.5, 28.4, 'C69'], [28.0, 30, 'Dfin'],
];
// pad level automation
function padLevel(t) {
  let g = Math.min(Math.max((t - 2.0) / 2.5, 0), 1) * .55;
  if (t > 8.85) g = .55 + .25 * Math.min((t - 8.85) / 2, 1);
  if (t >= 8.53 && t < 8.85) g *= Math.max(0, 1 - (t - 8.53) / .02);       // freeze: silence
  if (t >= 8.85 && t < 9.2) g *= (t - 8.85) / .35;
  if (t > 24 && t < 27) g *= 1 + .25 * Math.sin(Math.PI * (t - 24) / 3);
  if (t > 29) g *= Math.max(0, (30 - t));
  return g;
}
{
  const lpL = new Biquad('lp', 900, .6), lpR = new Biquad('lp', 900, .6);
  const voices = [];
  PROG.forEach(([a, b, c]) => CH[c].forEach((n, j) => [-1, 0, 1].forEach(d => voices.push({ a, b, f: n2f(n) * Math.pow(2, d * 7 / 1200), pan: (j / 5 - .5) * .9 + d * .15, ph: rnd(), amp: (j === 0 ? 1.1 : .75) / 3 }))));
  for (let i = idx(2.0); i < N; i++) {
    const t = i / SR; let l = 0, r = 0;
    for (const v of voices) {
      if (t < v.a || t > v.b + 1.6) continue;
      const e = Math.min((t - v.a) / 1.4, 1) * (t > v.b ? Math.max(0, 1 - (t - v.b) / 1.6) : 1);
      if (e <= 0) continue;
      v.ph += v.f / SR; const s = tab(v.ph) * v.amp * e;
      l += s * (1 - v.pan) * .5; r += s * (1 + v.pan) * .5;
    }
    const lfo = 1 + .25 * Math.sin(TAU * .07 * t);
    const cut = (t < 8.85 ? 650 : t < 24 ? 1000 : t < 27 ? 1300 : 700) * lfo;
    if ((i & 63) === 0) { lpL.set(cut, .6); lpR.set(cut, .6); }
    const g = padLevel(t) * .07;
    ML[i] += lpL.run(l) * g; MR[i] += lpR.run(r) * g;
    RL[i] += lpL.y1 * g * .5; RR[i] += lpR.y1 * g * .5;
  }
}
// pulse / arpeggio from the transformation onwards (100 BPM grid anchored on the click release)
const BEAT = .6, T0 = 8.85;
const chordAt = t => { let c = 'Dm9'; for (const [a, , n] of PROG) if (t >= a) c = n; return c; };
{
  const dl = new Float32Array(N), dr = new Float32Array(N);
  for (let k = 0; ; k++) {
    const t = T0 + 1.2 + k * BEAT / 4;
    if (t > 26.6) break;
    const sixteenth = t >= 12.45;
    if (!sixteenth && k % 2) continue;
    if (t > 24 && t < 25.4 && k % 2) continue;
    const ch = CH[chordAt(t)];
    const pat = [2, 3, 4, 3, 1, 4, 2, 3];
    const n = ch[pat[k % pat.length] % ch.length] + 12;
    const f = n2f(n), pan = (k % 2 ? .35 : -.35);
    const vel = (k % 4 === 0 ? 1 : .7) * (t < 12.45 ? .75 : 1) * (t > 24 ? .8 : 1);
    const lp = new Biquad('lp', 2600, .7);
    const i0 = idx(t);
    for (let j = 0; j < SR * .6; j++) {
      const s = j / SR, i = i0 + j; if (i >= N) break;
      const v = lp.run((Math.sin(TAU * f * s) + .3 * Math.sin(TAU * 2 * f * s) * expd(s, .05)) * att(s, .003) * expd(s, .16)) * .022 * vel;
      dl[i] += v * (1 - pan); dr[i] += v * (1 + pan);
    }
  }
  // ping-pong delay (dotted eighth)
  const D = Math.round(BEAT * .75 * SR);
  for (let i = 0; i < N; i++) {
    const a = dl[i] + (i >= D ? dr[i - D] * .38 : 0), b = dr[i] + (i >= D ? dl[i - D] * .38 : 0);
    dl[i] = a; dr[i] = b;
    ML[i] += a; MR[i] += b; RL[i] += a * .2; RR[i] += b * .2;
  }
}
// sub bass + soft kick + air hats
PROG.forEach(([a, b, c]) => {
  if (b < 12.4 || a > 27) return;
  const s0 = Math.max(a, 12.45), e0 = Math.min(b, 26.9);
  const f = n2f(CH[c][0] - 12 + (CH[c][0] < 44 ? 12 : 0));
  put(s0, e0 - s0 + .8, s => { const e = Math.min(s / .6, 1) * (s > e0 - s0 ? Math.max(0, 1 - (s - (e0 - s0)) / .8) : 1); return e * (Math.sin(TAU * f * s) + .15 * Math.sin(TAU * 2 * f * s)); }, { gain: .06, bus: 'music' });
});
for (let k = 0; ; k++) {
  const t = T0 + 4.8 + k * BEAT; if (t > 23.95) break;
  put(t, .4, s => { const f = 42 + 55 * expd(s, .03); return Math.sin(TAU * f * s) * att(s, .002) * expd(s, .14); }, { gain: .13, bus: 'music' });
}
{
  for (let k = 0; ; k++) {
    const t = T0 + 8.4 + k * BEAT / 2; if (t > 23.95) break;
    const hp = new Biquad('hp', 8000);
    put(t, .08, s => hp.run(white()) * expd(s, .012), { gain: (k % 2 ? .012 : .007), pan: k % 2 ? .3 : -.3, bus: 'music' });
  }
}

/* ================================================================ reverb (Freeverb-style) */
function freeverb(inL, inR, room = .84, damp = .35) {
  const combs = [1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617], aps = [556, 441, 341, 225];
  const scale = SR / 44100;
  const mk = (spread) => ({ c: combs.map(n => ({ b: new Float32Array(Math.round((n + spread) * scale)), i: 0, f: 0 })), a: aps.map(n => ({ b: new Float32Array(Math.round((n + spread) * scale)), i: 0 })) });
  const chans = [mk(0), mk(23)], ins = [inL, inR], outs = [new Float32Array(N), new Float32Array(N)];
  for (let ch = 0; ch < 2; ch++) {
    const { c, a } = chans[ch], x = ins[ch], o = outs[ch];
    for (let i = 0; i < N; i++) {
      const inp = x[i] * .015; let s = 0;
      for (const cb of c) { const y = cb.b[cb.i]; cb.f = y * (1 - damp) + cb.f * damp; cb.b[cb.i] = inp + cb.f * room; cb.i = (cb.i + 1) % cb.b.length; s += y; }
      for (const ap of a) { const y = ap.b[ap.i]; const v = -s + y; ap.b[ap.i] = s + y * .5; ap.i = (ap.i + 1) % ap.b.length; s = v; }
      o[i] = s;
    }
  }
  return outs;
}
const [wl, wr] = freeverb(RL, RR);

/* ================================================================ master */
const outL = new Float32Array(N), outR = new Float32Array(N);
let peak = 0;
for (let i = 0; i < N; i++) {
  const t = i / SR;
  const musicDuck = (t >= 8.53 && t < 8.85) ? 0 : 1;
  let l = L[i] + ML[i] * musicDuck + wl[i] * 2.2, r = R[i] + MR[i] * musicDuck + wr[i] * 2.2;
  l = Math.tanh(l * 1.2) / 1.2; r = Math.tanh(r * 1.2) / 1.2;
  outL[i] = l; outR[i] = r; peak = Math.max(peak, Math.abs(l), Math.abs(r));
}
const norm = .89 / peak;
const out = process.argv[2] || path.join(__dirname, 'out', 'sitevo-intro.wav');
fs.mkdirSync(path.dirname(out), { recursive: true });
const buf = Buffer.alloc(44 + N * 4);
buf.write('RIFF', 0); buf.writeUInt32LE(36 + N * 4, 4); buf.write('WAVE', 8); buf.write('fmt ', 12);
buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(2, 22); buf.writeUInt32LE(SR, 24); buf.writeUInt32LE(SR * 4, 28); buf.writeUInt16LE(4, 32); buf.writeUInt16LE(16, 34);
buf.write('data', 36); buf.writeUInt32LE(N * 4, 40);
for (let i = 0; i < N; i++) {
  const d = () => (rnd() - rnd()) / 32768;
  buf.writeInt16LE(Math.max(-32767, Math.min(32767, Math.round((outL[i] * norm + d()) * 32767))), 44 + i * 4);
  buf.writeInt16LE(Math.max(-32767, Math.min(32767, Math.round((outR[i] * norm + d()) * 32767))), 46 + i * 4);
}
fs.writeFileSync(out, buf);
console.log('audio ->', out, 'peak before norm', peak.toFixed(3));
