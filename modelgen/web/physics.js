// ModelGen's own physics engine, the JavaScript twin of modelgen/physics/engine.py: Verlet particles
// with XPBD distance constraints, VRM-style spring chains (a root riding on an animated bone, each
// joint pulled back to where the animation puts it), capsule colliders, cone joint limits and a
// floor. The viewer runs a character's spring chains live while its clips play, and drops it as a
// ragdoll. Vectors are THREE.Vector3; up is +Y here (glTF), so gravity is (0, -9.81, 0).
import * as THREE from 'three';

const tmp = new THREE.Vector3(), tmp2 = new THREE.Vector3();

export class Particle {
  constructor(pos, invMass = 1, radius = 0) {
    this.pos = pos.clone(); this.prev = pos.clone(); this.invMass = invMass; this.radius = radius; this.target = null;
  }
}

export class Capsule {
  constructor(a, b, radius) { this.a = a.clone(); this.b = b.clone(); this.radius = radius; }
  closest(p, out) {
    const ab = tmp.subVectors(this.b, this.a), d = ab.lengthSq();
    const t = d < 1e-12 ? 0 : Math.max(0, Math.min(1, tmp2.subVectors(p, this.a).dot(ab) / d));
    return out.copy(this.a).addScaledVector(ab, t);
  }
  pushOut(p) {
    const c = this.closest(p.pos, new THREE.Vector3());
    const d = new THREE.Vector3().subVectors(p.pos, c), dist = d.length(), need = this.radius + p.radius;
    if (dist >= need) return false;
    if (dist < 1e-9) d.set(0, 1, 0); else d.multiplyScalar(1 / dist);
    p.pos.copy(c).addScaledVector(d, need);
    return true;
  }
}

class Distance {
  constructor(i, j, rest, compliance = 0) { this.i = i; this.j = j; this.rest = rest; this.compliance = compliance; this.lam = 0; }
  solve(ps, dt) {
    const a = ps[this.i], b = ps[this.j], w = a.invMass + b.invMass;
    if (!w) return;
    const d = tmp.subVectors(b.pos, a.pos), dist = d.length();
    if (dist < 1e-12) return;
    d.multiplyScalar(1 / dist);
    const alpha = this.compliance / (dt * dt), dl = (-(dist - this.rest) - alpha * this.lam) / (w + alpha);
    this.lam += dl;
    a.pos.addScaledVector(d, -dl * a.invMass);
    b.pos.addScaledVector(d, dl * b.invMass);
  }
}

class Cone {
  constructor(i, j, k, limit) { this.i = i; this.j = j; this.k = k; this.limit = limit; }
  solve(ps) {
    const a = ps[this.i], b = ps[this.j], c = ps[this.k];
    if (!c.invMass) return;
    const axis = new THREE.Vector3().subVectors(b.pos, a.pos).normalize();
    const bone = new THREE.Vector3().subVectors(c.pos, b.pos), L = bone.length();
    if (L < 1e-9 || !axis.lengthSq()) return;
    const cos = Math.max(-1, Math.min(1, axis.dot(bone) / L));
    if (Math.acos(cos) <= this.limit) return;
    const perp = bone.clone().multiplyScalar(1 / L).addScaledVector(axis, -cos).normalize();
    c.pos.copy(b.pos).addScaledVector(axis, L * Math.cos(this.limit)).addScaledVector(perp, L * Math.sin(this.limit));
  }
}

export class Chain {
  constructor(points, { stiffness = 0.25, drag = 0.1, gravity = 1, radius = 0.01 } = {}) {
    this.stiffness = stiffness; this.drag = drag; this.gravity = gravity;
    this.particles = points.map((p, i) => new Particle(p, i ? 1 : 0, radius));
    this.links = points.slice(1).map((p, i) => new Distance(i, i + 1, p.distanceTo(points[i])));
    this.bends = points.slice(2).map((p, i) => new Distance(i, i + 2, p.distanceTo(points[i]), 1e-6));
  }
  setTargets(points) {
    points.forEach((p, i) => { this.particles[i].target = p.clone(); });
    this.particles[0].pos.copy(points[0]);
  }
}

export class World {
  constructor({ gravity = new THREE.Vector3(0, -9.81, 0), substeps = 4, iterations = 4, floor = null } = {}) {
    this.g = gravity; this.substeps = substeps; this.iterations = iterations; this.floor = floor;
    this.chains = []; this.colliders = []; this.particles = []; this.constraints = [];
  }
  integrate(p, h, drag, gs) {
    if (!p.invMass) { p.prev.copy(p.pos); return; }
    const v = tmp.subVectors(p.pos, p.prev).multiplyScalar(1 - drag);
    p.prev.copy(p.pos);
    p.pos.add(v).addScaledVector(this.g, gs * h * h);
  }
  step(dt) {
    const h = dt / this.substeps;
    for (let s = 0; s < this.substeps; s++) this.substep(h);
  }
  substep(h) {
    for (const ch of this.chains) {
      ch.particles.forEach((p) => this.integrate(p, h, ch.drag, ch.gravity));
      [...ch.links, ...ch.bends].forEach((c) => { c.lam = 0; });
    }
    this.particles.forEach((p) => this.integrate(p, h, 0.02, 1));
    this.constraints.forEach((c) => { if (c instanceof Distance) c.lam = 0; });
    for (let it = 0; it < this.iterations; it++) {
      for (const ch of this.chains) {
        const k = 1 - Math.pow(1 - ch.stiffness, 1 / this.iterations);
        ch.particles.slice(1).forEach((p) => { if (p.target) p.pos.lerp(p.target, k); });
        ch.links.forEach((c) => c.solve(ch.particles, h));
        ch.bends.forEach((c) => c.solve(ch.particles, h));
        ch.particles.slice(1).forEach((p) => this.colliders.forEach((col) => col.pushOut(p)));
      }
      this.constraints.forEach((c) => c.solve(this.particles, h));
      for (const p of this.particles) {
        this.colliders.forEach((col) => col.pushOut(p));
        if (this.floor !== null && p.pos.y - p.radius < this.floor) {
          p.pos.y = this.floor + p.radius;
          p.prev.x += (p.pos.x - p.prev.x) * 0.5; p.prev.z += (p.pos.z - p.prev.z) * 0.5;   // friction
        }
      }
    }
    for (const ch of this.chains) {
      ch.links.forEach((c) => c.solve(ch.particles, h));
      ch.particles.slice(1).forEach((p) => this.colliders.forEach((col) => col.pushOut(p)));
    }
  }
}

// --- a character in the viewer -----------------------------------------------------------------

function boneWorld(b, out = new THREE.Vector3()) { return b.getWorldPosition(out); }

// Turn a bone so that the direction from its own joint to `from` -> `to` holds (world space).
function aim(bone, fromWorld, toWorld, childWorld) {
  const have = new THREE.Vector3().subVectors(childWorld, fromWorld);
  const want = new THREE.Vector3().subVectors(toWorld, fromWorld);
  if (have.lengthSq() < 1e-12 || want.lengthSq() < 1e-12) return;
  const turn = new THREE.Quaternion().setFromUnitVectors(have.normalize(), want.normalize());
  const world = bone.getWorldQuaternion(new THREE.Quaternion());
  const parent = bone.parent ? bone.parent.getWorldQuaternion(new THREE.Quaternion()) : new THREE.Quaternion();
  bone.quaternion.copy(parent.invert().multiply(turn.multiply(world)));
  bone.updateMatrixWorld(true);
}

// The spring chains of a skinned character (bones whose extras carry mg_spring), its colliders
// (mg_collider radii on body bones), run live: call update(dt) after the animation mixer.
export function characterSprings(model) {
  const bones = {};
  model.traverse((o) => { if (o.isBone) bones[o.name] = o; });
  const chains = [], colliders = [];
  for (const b of Object.values(bones)) {
    const u = b.userData || {};
    if (u.mg_spring) {
      const s = typeof u.mg_spring === 'string' ? JSON.parse(u.mg_spring) : u.mg_spring;
      const row = (s.chain || []).map((n) => bones[n]).filter(Boolean);
      if (row.length) chains.push({ row, rest: row.map((r) => r.quaternion.clone()), s });
    }
    if (u.mg_collider) colliders.push({ bone: b, radius: +u.mg_collider });
  }
  if (!chains.length) return null;
  const world = new World();
  const scale = new THREE.Vector3();
  model.getWorldScale(scale);
  function targets(c) {
    c.row.forEach((b, i) => b.quaternion.copy(c.rest[i]));
    c.row[0].parent.updateMatrixWorld(true);
    const pts = c.row.map((b) => boneWorld(b));
    const last = c.row[c.row.length - 1];
    const tail = last.children.find((x) => x.isBone);
    pts.push(tail ? boneWorld(tail) : pts[pts.length - 1].clone().add(new THREE.Vector3().subVectors(pts[pts.length - 1], pts[pts.length - 2] || pts[0])));
    return pts;
  }
  model.updateMatrixWorld(true);
  for (const c of chains) {
    c.sim = new Chain(targets(c), { stiffness: c.s.stiffness, drag: c.s.drag, gravity: c.s.gravity, radius: (c.s.radius || 0.01) * scale.y });
    world.chains.push(c.sim);
  }
  return {
    chainBones: new Set(chains.flatMap((c) => c.row.map((b) => b.name))),
    update(dt) {
      model.updateMatrixWorld(true);
      world.colliders = colliders.map(({ bone, radius }) => {
        const child = bone.children.find((x) => x.isBone);
        const a = boneWorld(bone), b = child ? boneWorld(child) : a.clone();
        return new Capsule(a, b, radius * scale.y);
      });
      for (const c of chains) c.sim.setTargets(targets(c));
      world.step(Math.min(dt, 1 / 20));
      for (const c of chains) {
        const pos = c.sim.particles.map((p) => p.pos);
        c.row.forEach((b, i) => {
          const child = c.row[i + 1] || b.children.find((x) => x.isBone);
          const childPos = child ? boneWorld(child) : boneWorld(b).add(new THREE.Vector3(0, -0.01, 0));
          aim(b, boneWorld(b), pos[i + 1], childPos);
        });
      }
    },
  };
}

// A ragdoll of a skinned character standing as it is now: drops to the floor under gravity.
const RAG = [['pelvis', 'spine_02'], ['spine_02', 'neck_01'], ['neck_01', 'head'], ['neck_01', 'upperarm_l'],
  ['upperarm_l', 'lowerarm_l'], ['lowerarm_l', 'hand_l'], ['neck_01', 'upperarm_r'], ['upperarm_r', 'lowerarm_r'],
  ['lowerarm_r', 'hand_r'], ['pelvis', 'thigh_l'], ['thigh_l', 'calf_l'], ['calf_l', 'foot_l'], ['pelvis', 'thigh_r'],
  ['thigh_r', 'calf_r'], ['calf_r', 'foot_r']];
const BRACE = [['upperarm_l', 'upperarm_r'], ['thigh_l', 'thigh_r'], ['thigh_l', 'spine_02'], ['thigh_r', 'spine_02'],
  ['upperarm_l', 'spine_02'], ['upperarm_r', 'spine_02']];
const LIMITS = [['pelvis', 'spine_02', 'neck_01', 35], ['upperarm_l', 'lowerarm_l', 'hand_l', 145],
  ['upperarm_r', 'lowerarm_r', 'hand_r', 145], ['thigh_l', 'calf_l', 'foot_l', 140], ['thigh_r', 'calf_r', 'foot_r', 140]];
// the bones a ragdoll joint turns: the bone from joint a towards joint b
const DRIVE = [['spine_02', 'neck_01', 'spine_02'], ['neck_01', 'head', 'neck_01'], ['upperarm_l', 'lowerarm_l', 'upperarm_l'],
  ['lowerarm_l', 'hand_l', 'lowerarm_l'], ['upperarm_r', 'lowerarm_r', 'upperarm_r'], ['lowerarm_r', 'hand_r', 'lowerarm_r'],
  ['thigh_l', 'calf_l', 'thigh_l'], ['calf_l', 'foot_l', 'calf_l'], ['thigh_r', 'calf_r', 'thigh_r'], ['calf_r', 'foot_r', 'calf_r']];

export function ragdoll(model, floorY, push = new THREE.Vector3(0, 0, 0.6)) {
  const bones = {};
  model.traverse((o) => { if (o.isBone) bones[o.name] = o; });
  if (!bones.pelvis || !bones.thigh_l) return null;
  model.updateMatrixWorld(true);
  const names = [...new Set(RAG.flat())].filter((n) => bones[n]);
  const idx = Object.fromEntries(names.map((n, i) => [n, i]));
  const scale = new THREE.Vector3();
  model.getWorldScale(scale);
  const world = new World({ floor: floorY, iterations: 8 });
  names.forEach((n) => {
    const p = new Particle(boneWorld(bones[n]), 1, 0.05 * scale.y);
    p.prev.addScaledVector(push, -1 / 60);   // a small shove so it falls one way
    world.particles.push(p);
  });
  for (const [a, b] of [...RAG, ...BRACE]) {
    if (a in idx && b in idx) world.constraints.push(new Distance(idx[a], idx[b], world.particles[idx[a]].pos.distanceTo(world.particles[idx[b]].pos)));
  }
  for (const [a, b, c, deg] of LIMITS) {
    if (a in idx && b in idx && c in idx) world.constraints.push(new Cone(idx[a], idx[b], idx[c], THREE.MathUtils.degToRad(deg)));
  }
  const pelvisOffset = new THREE.Vector3().subVectors(model.getWorldPosition(new THREE.Vector3()), boneWorld(bones.pelvis));
  return {
    update(dt) {
      world.step(Math.min(dt, 1 / 20));
      const P = (n) => world.particles[idx[n]].pos;
      // the whole body follows the pelvis; then each limb bone turns towards its next joint
      const root = bones.pelvis;
      const shift = new THREE.Vector3().subVectors(P('pelvis'), boneWorld(root));
      model.position.add(shift.divide(model.parent ? model.parent.getWorldScale(new THREE.Vector3()) : new THREE.Vector3(1, 1, 1)));
      model.updateMatrixWorld(true);
      aim(root, boneWorld(root), P('spine_02'), boneWorld(bones.spine_02 || root));
      for (const [a, b, drive] of DRIVE) {
        if (!(a in idx) || !(b in idx) || !bones[drive]) continue;
        aim(bones[drive], boneWorld(bones[a]), P(b), boneWorld(bones[b]));
      }
      return pelvisOffset;
    },
  };
}
