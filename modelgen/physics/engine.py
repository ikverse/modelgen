"""Position-based dynamics for spring chains and ragdolls.

The method is Verlet integration with constraints solved as in XPBD (extended position-based
dynamics, Macklin, Müller and Chentanez 2016): every step, particles move by their velocity plus
gravity, then a few iterations project them back onto their constraints, each constraint's
compliance (the inverse of its stiffness) making it soft or rigid independently of the step count.
Spring chains follow the VRM SpringBone idea: a chain's root rides on an animated bone, each joint
is pulled back towards where the animation puts it (stiffness) and keeps its length to the joint
before it, and capsules and spheres on the body push joints out so hair and cloth never pass
through it. A ragdoll is the same machinery: a particle per joint, distance constraints along the
bones and across the hips and shoulders, cone limits at the joints, and the floor.

Vectors are 3-tuples; Z is up (Blender's axes).
"""

import math

# --- small vector maths ------------------------------------------------------------------------


def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def mul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def length(a):
    return math.sqrt(dot(a, a))


def norm(a):
    n = length(a)
    return mul(a, 1.0 / n) if n > 1e-12 else (0.0, 0.0, 0.0)


def lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def closest_on_segment(p, a, b):
    ab = sub(b, a)
    denom = dot(ab, ab)
    t = 0.0 if denom < 1e-12 else max(0.0, min(1.0, dot(sub(p, a), ab) / denom))
    return add(a, mul(ab, t))


# --- the pieces ----------------------------------------------------------------------------------


class Particle:
    """A point with a mass. inv_mass 0 pins it: something else (an animated bone) moves it."""

    __slots__ = ("pos", "prev", "inv_mass", "radius", "target")

    def __init__(self, pos, inv_mass=1.0, radius=0.0):
        self.pos = tuple(pos)
        self.prev = tuple(pos)
        self.inv_mass = float(inv_mass)
        self.radius = float(radius)
        self.target = None  # where the animation puts this joint: stiffness pulls it back there

    def teleport(self, pos):
        self.pos = tuple(pos)
        self.prev = tuple(pos)


class Capsule:
    """A collider: a segment with a radius (a sphere when both ends meet), usually riding on a
    bone. Particles are pushed out of it."""

    __slots__ = ("a", "b", "radius")

    def __init__(self, a, b, radius):
        self.a, self.b, self.radius = tuple(a), tuple(b), float(radius)

    def push_out(self, p):
        """p moved out to the capsule's surface if it is inside (its own radius included)."""
        c = closest_on_segment(p.pos, self.a, self.b)
        d = sub(p.pos, c)
        dist = length(d)
        need = self.radius + p.radius
        if dist >= need:
            return False
        n = norm(d) if dist > 1e-9 else (0.0, 0.0, 1.0)
        p.pos = add(c, mul(n, need))
        return True


class Distance:
    """Two particles at a rest length, with a compliance (0: rigid; larger: stretchier)."""

    __slots__ = ("i", "j", "rest", "compliance", "lam")

    def __init__(self, i, j, rest, compliance=0.0):
        self.i, self.j, self.rest, self.compliance, self.lam = i, j, float(rest), float(compliance), 0.0

    def solve(self, ps, dt):
        a, b = ps[self.i], ps[self.j]
        w = a.inv_mass + b.inv_mass
        if w == 0:
            return
        d = sub(b.pos, a.pos)
        dist = length(d)
        if dist < 1e-12:
            return
        n = mul(d, 1.0 / dist)
        c = dist - self.rest
        alpha = self.compliance / (dt * dt)
        dl = (-c - alpha * self.lam) / (w + alpha)
        self.lam += dl
        a.pos = sub(a.pos, mul(n, dl * a.inv_mass))
        b.pos = add(b.pos, mul(n, dl * b.inv_mass))


class Cone:
    """A joint limit: the bone from j to k may turn at most `limit` radians away from the bone
    from i to j continued (a knee, an elbow, a spine; a ragdoll keeps human angles)."""

    __slots__ = ("i", "j", "k", "limit")

    def __init__(self, i, j, k, limit):
        self.i, self.j, self.k, self.limit = i, j, k, float(limit)

    def solve(self, ps, dt):
        a, b, c = ps[self.i], ps[self.j], ps[self.k]
        if c.inv_mass == 0:
            return
        axis = norm(sub(b.pos, a.pos))
        bone = sub(c.pos, b.pos)
        L = length(bone)
        if L < 1e-9 or length(axis) == 0:
            return
        cosang = max(-1.0, min(1.0, dot(axis, bone) / L))
        ang = math.acos(cosang)
        if ang <= self.limit:
            return
        # turn the bone back onto the cone, in the plane of the axis and the bone
        perp = norm(sub(mul(bone, 1.0 / L), mul(axis, cosang)))
        want = add(mul(axis, math.cos(self.limit)), mul(perp, math.sin(self.limit)))
        c.pos = add(b.pos, mul(want, L))


# --- a spring chain ---------------------------------------------------------------------------------


class Chain:
    """Joints of a spring chain, root first. The root follows its animated parent bone; every other
    joint is free, pulled back towards its animated target by `stiffness` (0..1 per step) and kept
    at its length from the joint before it (and two before it, softly, so it does not fold)."""

    def __init__(self, name, points, stiffness=0.25, drag=0.1, gravity=1.0, radius=0.01):
        self.name = name
        self.stiffness, self.drag, self.gravity = float(stiffness), float(drag), float(gravity)
        self.particles = [Particle(p, 0.0 if i == 0 else 1.0, radius) for i, p in enumerate(points)]
        self.links = [Distance(i, i + 1, length(sub(points[i + 1], points[i]))) for i in range(len(points) - 1)]
        self.bends = [Distance(i, i + 2, length(sub(points[i + 2], points[i])), compliance=1e-6)
                      for i in range(len(points) - 2)]

    def set_targets(self, points):
        """Where the animation puts every joint this frame (the root is moved there outright)."""
        for p, t in zip(self.particles, points):
            p.target = tuple(t)
        self.particles[0].pos = tuple(points[0])

    def positions(self):
        return [p.pos for p in self.particles]


# --- the world -----------------------------------------------------------------------------------------


class World:
    """Chains, free constraints (a ragdoll's), colliders and the floor, stepped together."""

    def __init__(self, gravity=(0.0, 0.0, -9.81), substeps=4, iterations=4, floor=None):
        self.g = tuple(gravity)
        self.substeps, self.iterations = int(substeps), int(iterations)
        self.floor = floor
        self.chains = []
        self.colliders = []
        self.particles = []  # a ragdoll's free particles
        self.constraints = []  # Distance and Cone over self.particles

    def add_chain(self, chain):
        self.chains.append(chain)
        return chain

    def step(self, dt):
        h = dt / self.substeps
        for _ in range(self.substeps):
            self._substep(h)

    def _integrate(self, p, h, drag, gscale):
        if p.inv_mass == 0:
            p.prev = p.pos
            return
        v = mul(sub(p.pos, p.prev), 1.0 - drag)
        p.prev = p.pos
        p.pos = add(add(p.pos, v), mul(self.g, gscale * h * h))

    def _substep(self, h):
        for ch in self.chains:
            for p in ch.particles:
                self._integrate(p, h, ch.drag, ch.gravity)
            for c in ch.links + ch.bends:
                c.lam = 0.0
        for p in self.particles:
            self._integrate(p, h, 0.02, 1.0)
        for c in self.constraints:
            if isinstance(c, Distance):
                c.lam = 0.0
        for _ in range(self.iterations):
            for ch in self.chains:
                k = 1.0 - (1.0 - ch.stiffness) ** (1.0 / self.iterations)  # the same pull whatever the count
                for p in ch.particles[1:]:
                    if p.target is not None:
                        p.pos = lerp(p.pos, p.target, k)
                for c in ch.links:
                    c.solve(ch.particles, h)
                for c in ch.bends:
                    c.solve(ch.particles, h)
                for p in ch.particles[1:]:
                    for col in self.colliders:
                        col.push_out(p)
            for c in self.constraints:
                c.solve(self.particles, h)
            for p in self.particles:
                for col in self.colliders:
                    col.push_out(p)
                if self.floor is not None and p.pos[2] - p.radius < self.floor:
                    p.pos = (p.pos[0], p.pos[1], self.floor + p.radius)
                    # friction: a joint on the floor keeps little of its sideways speed
                    p.prev = (lerp(p.prev, p.pos, 0.5)[0], lerp(p.prev, p.pos, 0.5)[1], p.prev[2])
        for ch in self.chains:  # a chain keeps its exact lengths after the colliders moved it...
            for c in ch.links:
                c.solve(ch.particles, h)
            for p in ch.particles[1:]:  # ...and never ends a step inside the body
                for col in self.colliders:
                    col.push_out(p)


# --- a ragdoll ---------------------------------------------------------------------------------------------

# the game skeleton's joints a ragdoll keeps, its bones (parent, child) and joint cone limits (degrees)
RAGDOLL_BONES = [("pelvis", "spine_02"), ("spine_02", "neck_01"), ("neck_01", "head_top"),
                 ("neck_01", "upperarm_l"), ("upperarm_l", "lowerarm_l"), ("lowerarm_l", "hand_l"),
                 ("neck_01", "upperarm_r"), ("upperarm_r", "lowerarm_r"), ("lowerarm_r", "hand_r"),
                 ("pelvis", "thigh_l"), ("thigh_l", "calf_l"), ("calf_l", "foot_l"),
                 ("pelvis", "thigh_r"), ("thigh_r", "calf_r"), ("calf_r", "foot_r")]
RAGDOLL_BRACES = [("upperarm_l", "upperarm_r"), ("thigh_l", "thigh_r"), ("thigh_l", "spine_02"),
                  ("thigh_r", "spine_02"), ("upperarm_l", "spine_02"), ("upperarm_r", "spine_02")]
RAGDOLL_LIMITS = [("pelvis", "spine_02", "neck_01", 35), ("spine_02", "neck_01", "head_top", 45),
                  ("upperarm_l", "lowerarm_l", "hand_l", 145), ("upperarm_r", "lowerarm_r", "hand_r", 145),
                  ("thigh_l", "calf_l", "foot_l", 140), ("thigh_r", "calf_r", "foot_r", 140)]


def ragdoll_world(joints, radius=0.05, floor=0.0, velocity=(0.0, 0.0, 0.0)):
    """A World holding a ragdoll made from joint positions {name: (x, y, z)} (the RAGDOLL_BONES
    names, as the skeleton stands now) with an initial velocity per step. Returns (world, names)."""
    names = sorted({n for pair in RAGDOLL_BONES for n in pair} & set(joints))
    index = {n: i for i, n in enumerate(names)}
    w = World(floor=floor, iterations=8)
    for n in names:
        p = Particle(joints[n], 1.0, radius)
        p.prev = sub(p.pos, velocity)
        w.particles.append(p)
    for a, b in RAGDOLL_BONES + RAGDOLL_BRACES:
        if a in index and b in index:
            w.constraints.append(Distance(index[a], index[b], length(sub(joints[b], joints[a]))))
    for a, b, c, deg in RAGDOLL_LIMITS:
        if a in index and b in index and c in index:
            w.constraints.append(Cone(index[a], index[b], index[c], math.radians(deg)))
    return w, names
