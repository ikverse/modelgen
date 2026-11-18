"""The separate parts of a reference-built car's front (bpy_lib.shells.car_v2): what sits inside the
openings bpy_lib.shells.car_details cuts into the body, laid out from the same traced outlines.

- the grille: a diamond mesh of gloss-black bars set back inside its opening, a chrome strip round its
  edge and a chrome oval emblem in its middle;
- each headlight: two projector lenses in chrome rings on the housing's floor, and a glowing strip
  along the lamp's upper edge;
- the lower intake: one gloss-black bar across it.

The layout (where each piece goes, in the front view's (u, v) = (x, z) metres) is plain Python, so it
can be checked without Blender; parts() builds and places the pieces, each at its depth found by
casting a ray onto the finished body from the front.
"""

import math

MESH_SPACING = 0.02   # the grille mesh's bars, this far apart,
MESH_BAR = 0.004      # this wide,
MESH_SETBACK = 0.015  # and this far in front of the grille opening's floor
MESH_PIECE = 0.05     # the bars are laid in pieces this long, each at the opening's depth there
SURROUND = 0.004      # the chrome strip round the grille: its radius
EMBLEM = 0.06         # the emblem: half its width
PROJECTOR = 0.03      # a projector lens's radius, at most
DRL_INSET = 0.008     # the light strip runs this far inside a headlight's upper edge


# --- plain-Python layout --------------------------------------------------------------------------

def inside(p, poly):
    """Whether (u, v) point p is inside the closed outline poly."""
    x, y, c = p[0], p[1], False
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            c = not c
    return c


def _to_edge(p, poly):
    best = 1e9
    for (ax, ay), (cx, cy) in zip(poly, poly[1:] + poly[:1]):
        vx, vy = cx - ax, cy - ay
        t = max(0.0, min(1.0, ((p[0] - ax) * vx + (p[1] - ay) * vy) / ((vx * vx + vy * vy) or 1e-12)))
        best = min(best, math.hypot(p[0] - ax - t * vx, p[1] - ay - t * vy))
    return best


def _crossings(poly, origin, direction):
    """Where the line origin + t * direction crosses the outline: the sorted t values."""
    ox, oy = origin
    dx, dy = direction
    ts = []
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        ex, ey = x1 - x0, y1 - y0
        den = dx * ey - dy * ex
        if abs(den) < 1e-12:
            continue
        t = ((x0 - ox) * ey - (y0 - oy) * ex) / den
        s = ((x0 - ox) * dy - (y0 - oy) * dx) / den
        if 0.0 <= s < 1.0:
            ts.append(t)
    return sorted(ts)


def mesh_pieces(outline, spacing=MESH_SPACING, margin=MESH_BAR, piece=MESH_PIECE):
    """The grille mesh: two families of straight bars at +45 and -45 degrees, `spacing` apart, clipped
    to the outline (kept `margin` inside it), each cut into pieces up to `piece` long. ((u0, v0),
    (u1, v1)) pairs."""
    us, vs = [p[0] for p in outline], [p[1] for p in outline]
    cu, cv = (min(us) + max(us)) / 2, (min(vs) + max(vs)) / 2
    reach = math.hypot(max(us) - min(us), max(vs) - min(vs))
    out = []
    for direction in ((1 / math.sqrt(2), 1 / math.sqrt(2)), (1 / math.sqrt(2), -1 / math.sqrt(2))):
        normal = (-direction[1], direction[0])
        k = -math.ceil(reach / spacing)
        while k * spacing <= reach:
            origin = (cu + normal[0] * k * spacing, cv + normal[1] * k * spacing)
            ts = _crossings(outline, origin, direction)
            for t0, t1 in zip(ts[::2], ts[1::2]):
                t0, t1 = t0 + margin, t1 - margin
                n = max(1, math.ceil((t1 - t0) / piece))
                for i in range(n if t1 > t0 else 0):
                    a, b = t0 + (t1 - t0) * i / n, t0 + (t1 - t0) * (i + 1) / n
                    out.append(((origin[0] + direction[0] * a, origin[1] + direction[1] * a),
                                (origin[0] + direction[0] * b, origin[1] + direction[1] * b)))
            k += 1
    return out


def projector_spots(outline, radius=PROJECTOR):
    """Two projector lenses in a headlight: at a third and two thirds across it, centred up it there,
    each as big as fits (at most `radius`, a little inside the outline). (u, v, r) each."""
    us = [p[0] for p in outline]
    out = []
    for f in (1 / 3, 2 / 3):
        u = min(us) + (max(us) - min(us)) * f
        ts = _crossings(outline, (u, 0.0), (0.0, 1.0))
        if len(ts) < 2:
            continue
        v0, v1 = ts[0], ts[-1]
        r = min(radius, 0.4 * (v1 - v0))
        out.append((u, (v0 + v1) / 2, r))
    return out


def top_edge(outline, inset=DRL_INSET, step=0.01):
    """A lamp's upper edge, `inset` inside it, every `step` across: (u, v) points, only those well
    inside the outline."""
    us = [p[0] for p in outline]
    out = []
    u = min(us) + inset + step / 2
    while u < max(us) - inset:
        ts = _crossings(outline, (u, 0.0), (0.0, 1.0))
        if ts:
            p = (u, ts[-1] - inset)
            if inside(p, outline) and _to_edge(p, outline) >= inset * 0.8:
                out.append(p)
        u += step
    return out


def intake_bar(outline):
    """The bar across the lower intake: (u0, u1, v), 1 cm inside its ends, a third of the way up it."""
    us, vs = [p[0] for p in outline], [p[1] for p in outline]
    return min(us) + 0.01, max(us) - 0.01, min(vs) + (max(vs) - min(vs)) / 3


# --- built in Blender -------------------------------------------------------------------------------

def _depth(body, u, v):
    """The y of the body's surface at front-view (u, v), seen from the front, or None."""
    from mathutils import Vector
    hit, loc, _, _ = body.ray_cast(Vector((u, -10.0, v)), Vector((0.0, 1.0, 0.0)))
    return loc.y if hit else None


def _bar(p0, p1, y, width, depth, name):
    """A straight box bar from (u, v) p0 to p1 in the front view's plane, at y."""
    from bpy_lib import helpers as h
    (u0, v0), (u1, v1) = p0, p1
    length = math.hypot(u1 - u0, v1 - v0)
    angle = math.degrees(math.atan2(v1 - v0, u1 - u0))
    return h.box((length, depth, width), location=((u0 + u1) / 2, y, (v0 + v1) / 2),
                 rotation_deg=(0, -angle, 0), name=name)


def parts(name, body):
    """Build the front's separate parts for example car `name` on its finished body: a list of objects,
    each one material (its slot set), named for what it is."""
    from bpy_lib import helpers as h
    from bpy_lib import shell
    from bpy_lib.shells import car_details

    front = car_details.load(name).get("front", {})
    out = []

    def finish(pieces, part_name, slot):
        pieces = [p for p in pieces if p is not None]
        if not pieces:
            return
        for p in pieces:
            shell._set_material(p, slot)
        obj = h.join(pieces, part_name) if len(pieces) > 1 else pieces[0]
        obj.name = part_name
        h.smooth(obj)
        out.append(obj)

    for outline in front.get("grille", []):
        bars = []
        for p0, p1 in mesh_pieces(outline):
            mid = ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2)
            floor = _depth(body, *mid)
            if floor is not None:
                bars.append(_bar(p0, p1, floor - MESH_SETBACK, MESH_BAR, MESH_BAR, "grille_bar"))
        finish(bars, "grille_mesh", "gloss_black")
        # the chrome strip round the opening's edge, just inside it
        ring = []
        for k in range(len(outline)):
            (ua, va), (ub, vb) = outline[k], outline[(k + 1) % len(outline)]
            n = max(1, round(math.hypot(ub - ua, vb - va) / 0.01))
            for i in range(n):
                u, v = ua + (ub - ua) * i / n, va + (vb - va) * i / n
                cu = sum(p[0] for p in outline) / len(outline)
                cv = sum(p[1] for p in outline) / len(outline)
                d = math.hypot(cu - u, cv - v) or 1.0
                u, v = u + (cu - u) / d * SURROUND, v + (cv - v) / d * SURROUND
                floor = _depth(body, u, v)
                if floor is not None:
                    ring.append((u, floor - 0.026, v))
        if len(ring) > 3:
            finish([h.sweep(ring, radius=SURROUND, closed=True, name="grille_surround")], "grille_surround", "chrome")
        # the emblem: a chrome oval ring in the middle, standing just behind the opening's face
        us, vs = [p[0] for p in outline], [p[1] for p in outline]
        cu, cv = (min(us) + max(us)) / 2, min(vs) + 0.6 * (max(vs) - min(vs))
        floor = _depth(body, cu, cv)
        if floor is not None:
            ring_obj = h.torus(EMBLEM, 0.006, location=(cu, floor - 0.024, cv), rotation_deg=(90, 0, 0), name="emblem")
            h.place(ring_obj, scale=(1.0, 0.72, 1.0))  # its local y is up once turned to face forward
            finish([ring_obj], "emblem", "chrome")

    lenses, rings, strips = [], [], []
    for outline in front.get("headlights", []):
        for u, v, r in projector_spots(outline):
            floor = _depth(body, u, v)
            if floor is None:
                continue
            ball = h.sphere(r, segments=32, rings=16, location=(u, floor, v), name="projector")
            h.place(ball, scale=(1.0, 0.4, 1.0))  # flattened: it stands under the lamp's clear cover
            lenses.append(ball)
            rings.append(h.torus(r + 0.004, 0.003, location=(u, floor - 0.002, v), rotation_deg=(90, 0, 0),
                                 name="projector_ring"))
        path = []
        for u, v in top_edge(outline):
            floor = _depth(body, u, v)
            if floor is not None:
                path.append((u, floor - 0.004, v))
        if len(path) >= 3:
            strips.append(h.sweep(path, radius=0.003, name="drl"))
    finish(lenses, "projector_lens", "lens")
    finish(rings, "projector_ring", "chrome")
    finish(strips, "drl", "lamp_led")

    bars = []
    for outline in front.get("intakes", []):
        u0, u1, v = intake_bar(outline)
        n = max(1, math.ceil((u1 - u0) / MESH_PIECE))
        for i in range(n):
            a, b = u0 + (u1 - u0) * i / n, u0 + (u1 - u0) * (i + 1) / n
            if not (inside(((a + b) / 2, v), outline)):
                continue  # behind the plate's notch
            floor = _depth(body, (a + b) / 2, v)
            if floor is not None:
                bars.append(_bar((a, v), (b, v), floor - MESH_SETBACK, 0.012, 0.012, "intake_bar"))
    finish(bars, "intake_bar", "gloss_black")
    return out
