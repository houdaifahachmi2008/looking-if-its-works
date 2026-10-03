// Deterministic rigid-body simulation of the falling keycaps (cannon-es).
// Output: out/sim.bin (Float32 pos+quat per key per record step), out/sim.json (meta), out/contacts.json (for audio)
import * as CANNON from 'cannon-es';
import fs from 'fs';
import { DESK, DESK_Y, deskPoint, topY, halfW, VARIANTS, LEGENDS_1U, LEGENDS_MOD, CAM_H } from './geom.mjs';

let seed = 20261003;
const rnd = () => { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
const R = (a, b) => a + (b - a) * rnd();

const DT = 1 / 480, REC_EVERY = 4, SIM_END = 3.6; // record at 120 Hz
const world = new CANNON.World({ gravity: new CANNON.Vec3(0, -9.81, 0) });
world.broadphase = new CANNON.SAPBroadphase(world);
world.allowSleep = true;
world.solver.iterations = 12;
const mKey = new CANNON.Material('key'), mDesk = new CANNON.Material('desk'), mFloor = new CANNON.Material('floor');
world.addContactMaterial(new CANNON.ContactMaterial(mKey, mDesk, { friction: 0.32, restitution: 0.38 }));
world.addContactMaterial(new CANNON.ContactMaterial(mKey, mKey, { friction: 0.3, restitution: 0.32 }));
world.addContactMaterial(new CANNON.ContactMaterial(mKey, mFloor, { friction: 0.4, restitution: 0.3 }));

// static desk (top at DESK_Y) and floor
const deskLen = 2.6, deskC = deskPoint(-0.6 + deskLen / 2 - 0.4, DESK.depth / 2);
const desk = new CANNON.Body({ mass: 0, material: mDesk, shape: new CANNON.Box(new CANNON.Vec3(deskLen / 2, 0.02, DESK.depth / 2)) });
desk.position.set(deskC[0], DESK_Y - 0.02, deskC[2]);
desk.quaternion.setFromAxisAngle(new CANNON.Vec3(0, 1, 0), DESK.yaw);
world.addBody(desk);
const floor = new CANNON.Body({ mass: 0, material: mFloor, shape: new CANNON.Plane() });
floor.quaternion.setFromEuler(-Math.PI / 2, 0, 0);
world.addBody(floor);

/* ---------- spawn plan */
const keys = [];
function pickVariant() {
  let x = rnd(), acc = 0;
  for (let i = 0; i < VARIANTS.length; i++) { acc += VARIANTS[i].w8; if (x <= acc) return i; }
  return 0;
}
function colour(vi) { // 0 graphite, 1 charcoal (modifiers), 2 sitevo blue accent
  if (vi !== 0) return rnd() < 0.04 ? 2 : 1;
  const x = rnd(); return x < 0.02 ? 2 : x < 0.2 ? 1 : 0;
}
function legend(vi) {
  const v = VARIANTS[vi];
  if (v.name === '1u') return LEGENDS_1U[Math.floor(rnd() * LEGENDS_1U.length)];
  const l = LEGENDS_MOD[v.name]; return l[Math.floor(rnd() * l.length)];
}
function spawn(t, zone, phase) {
  const vi = (zone === 'desk' && rnd() < 0.5) || zone.startsWith('hero') ? 0 : pickVariant();
  let p, v = [R(-0.15, 0.15), 0, R(-0.15, 0.15)];
  if (zone === 'desk') {
    const q = deskPoint(R(0.2, 0.56), R(0.05, 0.3));
    const D = -q[2]; p = [q[0], topY(D) + R(0.05, 0.35), q[2]]; v = [R(-0.05, 0.05), R(-0.3, 0), R(-0.05, 0.05)];
  } else if (zone === 'mid') {
    const D = R(1.2, 1.45); const hw = halfW(D);
    p = [R(-0.02 * D, hw * 1.05), topY(D) + R(0.03, 0.3), -D]; v[1] = R(-1.0, 0);
  } else if (zone.startsWith('hero')) { // the first keys: close enough to read clearly
    const [D, xf] = zone === 'hero1' ? [0.46, 0.42] : [0.55, 0.06]; const hw = halfW(D);
    p = [hw * xf, topY(D) + 0.025, -D]; v = [0.01, 0, 0];
  } else { // near curtain, anywhere across the frame
    const D = phase === 'build' ? R(0.3, 0.88) : Math.pow(rnd(), 1.45) * 0.78 + 0.14; const hw = halfW(D);
    p = [R(-hw * 1.08, hw * 1.08), topY(D) + R(0.01, 0.09), -D]; v = [R(-0.12, 0.12), R(-1.3, -0.1), R(-0.06, 0.06)];
  }
  keys.push({ t, zone: zone === 'desk' ? 'desk' : zone === 'mid' ? 'mid' : 'near', vi, col: colour(vi), legend: legend(vi), p, v, w: [R(-14, 14), R(-14, 14), R(-14, 14)], q: [R(0, 6.28), R(0, 6.28), R(0, 6.28)] });
}
// 1) the first 2-3 keys fall on the desk
spawn(0.12, 'hero1'); spawn(0.5, 'hero2'); spawn(0.74, 'desk');
// 2) a few more
for (let i = 0; i < 14; i++) spawn(0.95 + 0.85 * (i + rnd() * .6) / 14, ['near', 'desk', 'near', 'mid', 'near', 'desk', 'near', 'near', 'mid', 'near', 'desk', 'near', 'mid', 'near'][i], 'build');
// 3) it builds up
for (let i = 0; i < 130; i++) { const u = Math.pow((i + rnd()) / 130, 0.55); spawn(1.8 + 0.42 * u, i % 6 === 0 ? 'desk' : i % 5 === 0 ? 'mid' : 'near', 'build'); }
// 4) the wave: hundreds, peaking around sim 2.45
const WAVE = 2050;
for (let i = 0; i < WAVE; i++) {
  // density rises steeply then falls: sample from a skewed bell on [2.2, 2.62]
  const u = (rnd() + rnd() + rnd() + rnd()) / 4; const t = 2.2 + 0.44 * Math.pow(u, 0.75);
  const r = rnd(); spawn(t, r < 0.035 ? 'desk' : r < 0.11 ? 'mid' : 'near');
}
keys.sort((a, b) => a.t - b.t);
console.log('keys', keys.length);

/* ---------- bodies */
const bodies = keys.map((k, i) => {
  const V = VARIANTS[k.vi];
  const b = new CANNON.Body({ mass: 0.0012 * (V.w / 0.0181) * 1.0, material: mKey, linearDamping: 0.02, angularDamping: 0.03, allowSleep: true, sleepSpeedLimit: 0.03, sleepTimeLimit: 0.4 });
  b.addShape(new CANNON.Box(new CANNON.Vec3(V.w / 2, V.h / 2, V.d / 2)));
  b.position.set(...k.p); b.velocity.set(...k.v); b.angularVelocity.set(...k.w);
  b.quaternion.setFromEuler(...k.q);
  b.keyIndex = i;
  return b;
});

/* ---------- contacts for the sound design */
const contacts = [];
let simT = 0;
bodies.forEach(b => b.addEventListener('collide', e => {
  const other = e.body; const v = Math.abs(e.contact.getImpactVelocityAlongNormal());
  if (v < 0.12) return;
  let type = other === desk ? 'desk' : other === floor ? 'floor' : 'key';
  if (type === 'key' && other.keyIndex < b.keyIndex) return; // dedupe pairs
  contacts.push([+simT.toFixed(4), +v.toFixed(3), +b.position.x.toFixed(3), +b.position.y.toFixed(3), +b.position.z.toFixed(3), type, keys[b.keyIndex].vi]);
}));

/* ---------- run */
const steps = Math.round(SIM_END / DT), recN = Math.floor(steps / REC_EVERY) + 1;
const data = new Float32Array(recN * keys.length * 7);
let next = 0, rec = 0;
const t0 = Date.now();
for (let s = 0; s <= steps; s++) {
  simT = s * DT;
  while (next < keys.length && keys[next].t <= simT) { world.addBody(bodies[next]); next++; }
  if (s % REC_EVERY === 0) {
    const base = rec * keys.length * 7;
    for (let i = 0; i < keys.length; i++) {
      const b = bodies[i], o = base + i * 7;
      const p = i < next ? b.position : { x: keys[i].p[0], y: keys[i].p[1], z: keys[i].p[2] };
      const q = b.quaternion;
      data[o] = p.x; data[o + 1] = p.y; data[o + 2] = p.z; data[o + 3] = q.x; data[o + 4] = q.y; data[o + 5] = q.z; data[o + 6] = q.w;
    }
    rec++;
  }
  world.step(DT);
  // retire keys that have come to rest on the floor (out of view) to keep the solver fast
  if (s % 48 === 0) for (let i = 0; i < next; i++) { const b = bodies[i]; if (b.world && b.position.y < 0.05 && b.sleepState === CANNON.Body.SLEEPING) world.removeBody(b); }
  if (s % 480 === 0) console.log(`t=${simT.toFixed(2)} bodies=${world.bodies.length} ${((Date.now() - t0) / 1000).toFixed(1)}s`);
}
fs.mkdirSync('out', { recursive: true });
fs.writeFileSync('out/sim.bin', Buffer.from(data.buffer));
fs.writeFileSync('out/sim.json', JSON.stringify({ n: keys.length, rate: 1 / (DT * REC_EVERY), records: rec, keys: keys.map(k => ({ t: k.t, zone: k.zone, vi: k.vi, col: k.col, legend: k.legend })) }));
fs.writeFileSync('out/contacts.json', JSON.stringify(contacts));
console.log('records', rec, 'contacts', contacts.length, 'MB', (data.byteLength / 1e6).toFixed(1));
