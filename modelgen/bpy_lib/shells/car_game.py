"""A game-ready car from a reference: one clean low-poly skin with its glass painted on, four wheel
openings cut, and four low-detail wheels.

The geometry (GameBody, its rings and which faces are glass) is plain Python, so the tests check it
without Blender; build_body, build_wheel and finish run inside Blender and import it only there.

GameBody is car_body.Body with the glasshouse placed by the reference's glass points (the side view's
"glass", in metres: what modelgen.tools.car_space measures from the hand marks and writes into every
reference it makes) instead of Body's own search along the outline, which on most sedans finds the
end of the boot lid rather than the rear glass's foot: the windshield's and rear glass's feet are
where the glasshouse starts and ends, and the beltline points are the side windows' bottom edge.

Each section is one half outline with a fixed number of points in four runs: the lower body from the
bottom centre up to the shoulder; a short ledge from the shoulder in to the glass's foot; the
glasshouse's side up to the roof's corner; and the roof across to the centre. Outside the glasshouse
(over the hood and the boot) the same runs lie along the top. So the shoulder and the glass's bottom
edge are each one straight loop of edges along the car, the glass meets the paint on clean lines,
and the skin needs no subdivision.

Conventions as car_body's: metres, Z up, the front toward -Y, the right side toward +X.
"""

import json
import math
from pathlib import Path

from . import car_body

STATIONS = 56        # sections along the car
LOW, LEDGE, SIDE, ROOF = 12, 1, 5, 7   # segments in each run of a half section, bottom to top
HALF = LOW + LEDGE + SIDE + ROOF       # segments in a half section
DENSE = 120          # rays per half section when tracing the outlines the runs are taken from
FAIR = 2             # fairing passes along the car
B_PILLAR = 0.47      # the B-pillar's centre, as a fraction of the way along the side windows,
B_HALF = 0.05        # and its half-width (m)
WHEEL_SEGMENTS = 32  # sides of each wheel-opening cutter
MAX_BODY_TRIS = 10000
# the lower run's named points (indices into a half section): each is one line of edges along the car
SILL_IN, SILL_OUT, ROCKER, WAIST, CREASE_LOW, CREASE, SHOULDER = 2, 3, 4, 6, 8, 9, LOW
SILL_CHAMFER = 0.03  # the sill's bottom corner is cut this much across and up
ROCKER_UP = 0.10     # the rocker line stands this far above the underside
END_HOLD = 0.30      # the nose and tail: the hood's (boot's) line and the underside's are carried on
END_ROUND = 0.08     # from this far in to the end, which is rounded over its last END_ROUND
END_DROP = (0.05, 0.04)   # by this much at the top and at the bottom,
END_SLOPE = (0.08, 0.03)  # the carried lines falling (rising) by no more than this on the way;
PLAN_HOLD = 0.86     # seen from above, each end keeps this much of its width END_HOLD in,
PLAN_ROUND = 0.14    # its corners rounded over this length
SHARP_ANGLE = 30.0   # edges sharper than this shade crisp (the shoulder, the glass's frame, the sill)
GLASS_FRAME = 0.02   # the black frame round each window (m),
GLASS_DEPTH = 0.008  # and how far the glass sits below it
TYRE_OUT = 0.02      # the tyre's outer face stands this far outside the body above the wheel opening
                     # (inside the flared arch and its lip)
PALETTE = [(0.62, 0.05, 0.06), (0.06, 0.16, 0.42), (0.75, 0.76, 0.78), (0.08, 0.08, 0.09), (0.93, 0.93, 0.91),
           (0.20, 0.32, 0.22), (0.45, 0.47, 0.50), (0.55, 0.36, 0.14), (0.12, 0.42, 0.62), (0.32, 0.06, 0.12)]
GLASS = (0.20, 0.21, 0.23)   # dark grey, opaque: lighter than the windows' black frames


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _resample(points, n):
    """n + 1 points evenly spaced by arc length along a polyline (its ends kept)."""
    d = [0.0]
    for (x0, z0), (x1, z1) in zip(points, points[1:]):
        d.append(d[-1] + math.hypot(x1 - x0, z1 - z0))
    total = d[-1]
    if total < 1e-9:
        return [points[0]] * (n + 1)
    out, j = [], 0
    for i in range(n + 1):
        t = total * i / n
        while j < len(d) - 2 and d[j + 1] < t:
            j += 1
        seg = d[j + 1] - d[j]
        u = 0.0 if seg < 1e-12 else (t - d[j]) / seg
        (x0, z0), (x1, z1) = points[j], points[j + 1]
        out.append((x0 + (x1 - x0) * u, z0 + (z1 - z0) * u))
    return out


def _split_at_x(points, x):
    """Split a polyline running inward (x falling) where it first reaches x: (before, after), the
    crossing point ending one and starting the other."""
    for i in range(len(points) - 1):
        (x0, z0), (x1, z1) = points[i], points[i + 1]
        if x0 >= x >= x1 and x0 != x1:
            u = (x0 - x) / (x0 - x1)
            p = (x, z0 + (z1 - z0) * u)
            return points[:i + 1] + [p], [p] + points[i + 1:]
    return points[:2], points[1:]


class GameBody(car_body.Body):
    """car_body.Body with its glasshouse placed by the reference's glass points (see the module
    docstring)."""

    def __init__(self, ref):
        g = ref["views"]["side"].get("glass")
        if not g:
            raise ValueError("the reference has no glass points: make it with modelgen.tools.car_space")
        self.glass = {k: tuple(v) for k, v in g.items()}
        super().__init__(ref)
        zmax = max(z for _, z in self.top)
        roof = [y for y, z in self.top if z > zmax - 0.03]
        self.roof_span = (roof[0], roof[-1])
        bf, bb = self.glass["belt_front"][0], self.glass["belt_back"][0]
        c = bf + B_PILLAR * (bb - bf)
        self.b_pillar = (c - B_HALF, c + B_HALF)
        self._square_ends()
        # the wheels out to the body's side: the tyre's outer face TYRE_OUT outside the body just above
        # each opening
        top = self.wheel_radius + self.arch_radius()
        side = min(self.half_width(wy, top) for wy, _, _ in self.wheels)
        self.track = round(2 * (side + TYRE_OUT) - self.tyre_width, 3)

    def _square_ends(self):
        """The nose and the tail made into faces: over the last END_HOLD of each end the hood's (boot
        lid's) line and the underside's are carried straight on (by END_SLOPE at most) instead of
        closing to a point, then rounded off over the last END_ROUND; the plan keeps PLAN_HOLD of its
        width there, with rounded corners. The outlines are only ever moved outward, and the car's
        length stays as it is."""
        def rounded(d, reach):
            return 0.0 if d >= reach else 1.0 - math.sqrt(max(0.0, 1.0 - (1.0 - d / reach) ** 2))

        for end, sign in ((self.y_front, 1.0), (self.y_rear, -1.0)):
            ya, yb = end + sign * END_HOLD, end + sign * (END_HOLD + 0.15)

            def carried(points, limit, up):
                za, zb = car_body._interp(points, ya), car_body._interp(points, yb)
                k = (za - zb) / (ya - yb)

                def f(y):
                    z = za + k * (y - ya)
                    return min(z, za + limit) if up else max(z, za - limit)
                return f

            top, low = carried(self.top, END_SLOPE[0], False), carried(self.underside, END_SLOPE[1], True)
            wide = PLAN_HOLD * car_body._interp(self.plan, ya)

            def near(y):
                d = sign * (y - end)
                return d if 0.0 <= d < END_HOLD else None

            new_top, new_low = [], []
            for y, z in self.top:
                d = near(y)
                new_top.append((y, z if d is None else max(z, top(y) - END_DROP[0] * rounded(d, END_ROUND))))
            for y, z in self.underside:
                d = near(y)
                new_low.append((y, z if d is None else min(z, low(y) + END_DROP[1] * rounded(d, END_ROUND))))
            self.top, self.underside = new_top, new_low
            self.plan = [(y, w) if near(y) is None
                         else (y, max(w, wide * (1.0 - rounded(near(y), PLAN_ROUND)))) for y, w in self.plan]

    def _cabin_ends(self):
        (wy, _), (gy, _) = self.glass["windshield_foot"], self.glass["rear_glass_foot"]
        return (wy, car_body._interp(self.top, wy)), (gy, car_body._interp(self.top, gy))

    def _sill_line(self):
        (y0, z0), (y1, z1) = self.glass["belt_front"], self.glass["belt_back"]
        k = (z1 - z0) / (y1 - y0)
        return (z0 - k * y0, k), (y0, y1)

    # --- the sections ------------------------------------------------------------------------------

    def _in_glass(self, y):
        g = self.glass_span
        return bool(g) and g[0] < y < g[1]

    def _lower(self, y):
        """The lower body's half section at y from named corners, LOW + 1 points (x, z) from the bottom
        centre to the shoulder: the underside out to the sill's chamfered bottom corner (SILL_IN,
        SILL_OUT), the rocker line, the waist at the wheels' centres' height, the character line
        (CREASE_LOW just under it, where the side steps in, and CREASE on it) and the shoulder, where
        the side meets the top. Each is the same point on every section, so each is one line of edges
        along the car, and none is rounded off."""
        zb, zt = self.bounds(y)
        edge = self.half_width(y, zt)
        zs = self._top_at(y, edge, zt, edge)
        zc = min(self.belt(y) - car_body.CREASE_DROP, zs - 0.04)
        want = {SILL_OUT: zb + SILL_CHAMFER, ROCKER: zb + ROCKER_UP, WAIST: self.wheel_radius,
                CREASE_LOW: zc - 0.015, CREASE: zc, SHOULDER: zs}
        want[5] = (want[ROCKER] + want[WAIST]) / 2
        want[7] = (want[WAIST] + want[CREASE_LOW]) / 2
        want[10] = zc + 0.45 * (zs - zc)
        want[11] = zc + 0.80 * (zs - zc)
        step = max(1e-4, (zs - zb) / 40)
        zs_list = []
        for i in range(SILL_OUT, LOW + 1):  # heights rising, whatever the section's height
            lo = (zs_list[-1] if zs_list else zb) + step
            zs_list.append(max(lo, min(want[i], zs - (LOW - i) * step)))
        side = [(self.half_width(y, z), z) for z in zs_list]
        xa = max(0.0, side[0][0] - SILL_CHAMFER)
        return [(0.0, zb), (0.55 * xa, zb), (xa, zb)] + side

    def _top(self, y, shoulder):
        """The lower body's top at y from the shoulder in to the centre, as (x, z) points."""
        _, zt = self.bounds(y)
        edge = self.half_width(y, zt)
        xs = shoulder[0]
        return [shoulder] + [(x, self._top_at(y, min(x, edge), zt, edge))
                             for x in (xs * (1 - k / 40) for k in range(1, 41))]

    def _top_runs(self, y, body, s):
        """The top of the section from the shoulder (body[s]) to the top centre, as (ledge, rest):
        over the glasshouse the ledge runs in to the glass's foot and the rest is the glasshouse's
        outline from its foot; elsewhere both lie along the body's top."""
        top = body[s:]
        if self._in_glass(y):
            belt = self.belt(y)
            glass = self.section(y, DENSE, "glass")
            f = next((i for i in range(1, len(glass)) if glass[i][1] >= belt), len(glass) - 1)
            (x0, z0), (x1, z1) = glass[f - 1], glass[f]
            u = 0.0 if z1 == z0 else (belt - z0) / (z1 - z0)
            foot = (x0 + (x1 - x0) * u, belt)
            return [body[s], foot], [foot] + glass[f + 1:]
        x_foot = max(0.0, body[s][0] - car_body.GLASS_INSET)
        return _split_at_x(top, x_foot)

    @staticmethod
    def _roof_corner(rest):
        """Index along the glasshouse's outline (foot to top centre) of its corner into the roof: the
        point furthest out and up at once, each measured across the outline's own extent."""
        xs, zs = [p[0] for p in rest], [p[1] for p in rest]
        wx, wz = (max(xs) or 1e-6), (max(zs) - min(zs)) or 1e-6
        return max(range(1, len(rest) - 1), key=lambda i: rest[i][0] / wx + (rest[i][1] - zs[0]) / wz) \
            if len(rest) > 2 else 1

    def half_section(self, y, corner_ratio=None):
        """The right half section at y as HALF + 1 points (x, z), bottom centre to top centre, in the
        runs LOW, LEDGE, SIDE, ROOF; and the roof corner's x as a fraction of the glass foot's (the
        sections off the glasshouse take it from the nearest one on it, so the runs do not twist)."""
        low = self._lower(y)
        body = low + self._top(y, low[-1])[1:]
        s = LOW
        ledge, rest = self._top_runs(y, body, s)
        if self._in_glass(y) and len(rest) > 2:
            c = self._roof_corner(rest)
            ratio = rest[c][0] / max(1e-6, rest[0][0])
            side, roof = rest[:c + 1], rest[c:]
        else:
            ratio = corner_ratio if corner_ratio is not None else 0.8
            side, roof = _split_at_x(rest, ratio * rest[0][0])
        pts = low + _resample(ledge, LEDGE)[1:] + _resample(side, SIDE)[1:] + _resample(roof, ROOF)[1:]
        pts[0] = (0.0, pts[0][1])
        pts[-1] = (0.0, pts[-1][1])
        return pts, ratio

    def game_stations(self):
        """STATIONS positions along the car (closer at the ends), with the nearest moved onto each
        place the glass changes (the glass points, the roof's ends, the B-pillar's sides), so the glass
        meets the paint on straight lines across the car."""
        st = self.stations(STATIONS)
        keys = [self.cowl[0], self.deck[0], self.glass["belt_front"][0], self.glass["belt_back"][0],
                *self.roof_span, *self.b_pillar]
        if self.glass_span:
            keys += list(self.glass_span)
        taken = {0, len(st) - 1}
        for k in sorted(keys):
            i = min((i for i in range(len(st)) if i not in taken), key=lambda i: abs(st[i] - k))
            st[i] = k
            taken.add(i)
        st = sorted(st)
        for i in range(1, len(st)):  # never two sections on one spot
            st[i] = max(st[i], st[i - 1] + 0.004)
        return st

    def game_rings(self):
        """(rings, stations): closed rings for bpy_lib.shell.skin, every one 2 * HALF points, faired
        along the car (each point keeps its place in its run)."""
        ys = self.game_stations()
        halves = [None] * len(ys)
        ratios = {}
        for i, y in enumerate(ys):
            if self._in_glass(y):
                halves[i], ratios[i] = self.half_section(y)
        inside = sorted(ratios)
        for i, y in enumerate(ys):
            if halves[i] is None:
                near = min(inside, key=lambda j: abs(j - i)) if inside else None
                halves[i], _ = self.half_section(y, ratios.get(near))
        rings = [self._ring(y, half) for y, half in zip(ys, halves)]
        return self._fair(rings, FAIR), ys

    # --- which faces are glass ---------------------------------------------------------------------

    def is_glass(self, y, seg):
        """Whether the band of skin at y, in segment `seg` of the half section (0 at the bottom), is
        glass: the side windows between the beltline's ends (but the B-pillar), the windshield and rear
        glass across the top ahead of and behind the roof; never the first segment of the roof run
        (the A- and C-pillars and the roof's edge)."""
        if not self._in_glass(y):
            return False
        side0, roof0 = LOW + LEDGE, LOW + LEDGE + SIDE
        if side0 <= seg < roof0:
            bf, bb = self.glass["belt_front"][0], self.glass["belt_back"][0]
            return bf <= y <= bb and not self.b_pillar[0] <= y <= self.b_pillar[1]
        if roof0 + 1 <= seg < HALF:
            return y < self.roof_span[0] or y > self.roof_span[1]
        return False

    def glass_faces(self, ys):
        """For the skin of game_rings (its faces in skin's order, ring to ring, round each ring), which
        are glass."""
        n = 2 * HALF
        out = []
        for a, b in zip(ys, ys[1:]):
            y = (a + b) / 2
            for j in range(n):
                seg = j if j < HALF else n - 1 - j
                out.append(self.is_glass(y, seg))
        return out

    def pillar_faces(self, ys):
        """For the same skin, which faces are the B-pillar between the side windows (blacked out)."""
        n = 2 * HALF
        side0, roof0 = LOW + LEDGE, LOW + LEDGE + SIDE
        out = []
        for a, b in zip(ys, ys[1:]):
            y = (a + b) / 2
            on = self._in_glass(y) and self.b_pillar[0] <= y <= self.b_pillar[1]
            for j in range(n):
                seg = j if j < HALF else n - 1 - j
                out.append(on and side0 <= seg < roof0)
        return out


# --- in Blender --------------------------------------------------------------------------------

_BODIES = {}


def game_body(ref):
    key = json.dumps(ref.get("generated", {}).get("seed")) + ref.get("car", "")
    if key not in _BODIES:
        _BODIES[key] = GameBody(ref)
    return _BODIES[key]


def _closed_problems(obj):
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    open_edges = sum(1 for e in bm.edges if not e.is_manifold)
    tris = sum(len(f.verts) - 2 for f in bm.faces)
    bm.free()
    return open_edges, tris


def _triangulate_ngons(obj):
    """Split into triangles here, the best-shaped way, the faces of more than four corners (the wheel
    cuts leave a few) and the four-cornered faces touching a wheel cut (the wheel wells' faces and
    those sharing a corner with them, which the cut can leave bent): left to the exporter, one of them
    can be split along a line that is already an edge, and the file's body is no longer closed. So
    what is checked is what is exported; the rest of the skin stays quads."""
    import bmesh
    trim = [i for i, m in enumerate(obj.data.materials) if m is not None and m.get("mg_slot") == "trim"]
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    # a point the cut left in the middle of an edge (two edges meet there, nothing else): two faces
    # share it, and splitting both would put the same new edge in each, opening the body
    stray = [v for v in bm.verts if len(v.link_edges) == 2]
    if stray:
        bmesh.ops.dissolve_verts(bm, verts=stray)
    cut_verts = {v for f in bm.faces if f.material_index in trim for v in f.verts}
    ngons = [f for f in bm.faces
             if len(f.verts) > 4 or (len(f.verts) == 4 and any(v in cut_verts for v in f.verts))]
    if ngons:
        bmesh.ops.triangulate(bm, faces=ngons, quad_method="BEAUTY", ngon_method="BEAUTY")
        bm.to_mesh(obj.data)
        obj.data.update()
    bm.free()


def _recess_glass(obj, pillars):
    """Each window set into a black frame: the glass faces (slot 1) inset GLASS_FRAME and sunk
    GLASS_DEPTH below the skin, the frame and the B-pillar's faces (`pillars`, by face index) given the
    gloss black slot. An inset, not a cut: the body stays one closed solid."""
    import bmesh
    from mathutils import Vector

    from .. import shell

    me = obj.data
    me.materials.append(shell.slot("gloss_black"))
    black = len(me.materials) - 1
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    for i, on in enumerate(pillars):
        if on:
            bm.faces[i].material_index = black
    glass = [f for f in bm.faces if f.material_index == 1]
    frame = bmesh.ops.inset_region(bm, faces=glass, thickness=GLASS_FRAME, depth=0.0, use_even_offset=True,
                                   use_boundary=True)["faces"]
    for f in frame:
        f.material_index = black
    bm.normal_update()
    normals = {}
    for f in glass:
        for v in f.verts:
            normals.setdefault(v, Vector())
            normals[v] += f.normal
    for v, n in normals.items():
        if n.length > 1e-9:
            v.co -= n.normalized() * GLASS_DEPTH
    bm.to_mesh(me)
    bm.free()
    me.update()


def build_body(ref, kit=None):
    """The body as one closed mesh: the skin of game_rings with its glass faces given the glass slot
    and set into black frames (_recess_glass), (with a kit, car_kit_mesh.shape_body: the arches flared
    and the lower trim's loop and paint), then the four wheel openings and wells cut as
    bpy_lib.shells.car_v2 cuts them (the only cuts)."""
    from .. import helpers as h
    from .. import shell
    from . import car_v2

    b = game_body(ref)
    rings, ys = b.game_rings()
    obj = shell.skin(rings, levels=0, name="body", end_crease=0.0, end_centre_lines=b.cap_lines(rings))
    me = obj.data
    me.materials.append(shell.slot("glass"))
    for p, glass in zip(me.polygons, b.glass_faces(ys)):  # the sides' faces come first, in order
        p.material_index = 1 if glass else 0
    _recess_glass(obj, b.pillar_faces(ys))
    if kit:
        from . import car_kit_mesh
        car_kit_mesh.shape_body(obj, b, kit)
    outer = b.width / 2 + 0.15
    cutters = []
    for x, y, z in b.wheel_positions():
        well = b.well_x()
        depth = outer - well
        side = 1 if x > 0 else -1
        cutter = h.cylinder(b.arch_radius(), depth, segments=WHEEL_SEGMENTS,
                            location=(side * (well + depth / 2), y, z), rotation_deg=(0, 90, 0), name="wheel_opening")
        cutter.data.materials.append(shell.slot("trim"))
        cutters.append(cutter)
    car_v2._cut_with_materials(obj, cutters)
    car_v2._clean(obj)
    _triangulate_ngons(obj)
    open_edges, tris = _closed_problems(obj)
    if open_edges:
        raise ValueError(f"the body is not one closed solid: {open_edges} open edges")
    if tris > MAX_BODY_TRIS:
        raise ValueError(f"the body has {tris} triangles, over {MAX_BODY_TRIS}")
    h.smooth(obj, SHARP_ANGLE)
    obj[h.NO_BEVEL_KEY] = 1
    obj["mg_triangles"] = tris
    return obj


def wheel_pieces(diameter, width):
    """A low-detail wheel along X, outer face toward +X, centred on the origin: a tyre of 24 sides with
    a shouldered sidewall; a dished rim (an outer lip, then the barrel falling back to a dark floor);
    five tapered spokes sunk toward the hub, and a hub cap; behind them a brake disc and a caliper."""
    from mathutils import Matrix

    from .. import helpers as h
    from .. import shell

    R, W = diameter / 2, width
    rim_r = R * 0.77
    sh = min(0.035, W * 0.18)
    tyre = h.revolve([(rim_r - 0.004, -W * 0.40), (rim_r + 0.012, -W / 2), (R - sh * 1.4, -W / 2),
                      (R - sh * 0.15, -W / 2 + sh * 0.5), (R, -W * 0.25), (R, W * 0.25),
                      (R - sh * 0.15, W / 2 - sh * 0.5), (R - sh * 1.4, W / 2), (rim_r + 0.012, W / 2),
                      (rim_r - 0.004, W * 0.40)], segments=24, closed=True, name="tyre")
    shell._set_material(tyre, "tyre")
    lip_z = W * 0.44                     # the rim's outer lip, just inside the tyre's outer face
    dish = min(0.07, W * 0.32)           # how far the spokes' plane sits behind it
    face_z = lip_z - dish
    lip = 0.016
    rim = h.revolve([(rim_r + 0.006, lip_z - 0.012), (rim_r + 0.006, lip_z), (rim_r - lip, lip_z),
                     (rim_r - lip - 0.006, face_z - 0.03), (rim_r - lip - 0.006, -W * 0.42),
                     (rim_r + 0.006, -W * 0.42)], segments=24, closed=True, name="rim")
    shell._set_material(rim, "rim")
    inner_r = rim_r - lip - 0.006
    floor = h.cylinder(inner_r, 0.01, segments=24, location=(0, 0, face_z - 0.085), name="floor")
    shell._set_material(floor, "trim")
    disc = h.cylinder(inner_r * 0.86, 0.02, segments=20, location=(0, 0, face_z - 0.05), name="brake_disc")
    shell._set_material(disc, "brake_disc")
    caliper = h.box((inner_r * 0.34, inner_r * 0.62, 0.05), location=(-inner_r * 0.70, 0, face_z - 0.04),
                    name="caliper")
    shell._set_material(caliper, "caliper")
    hub_r = max(0.045, rim_r * 0.24)
    hub = h.cylinder(hub_r, 0.04, segments=12, location=(0, 0, face_z - 0.02), radius_top=hub_r * 0.7,
                     name="hub")
    shell._set_material(hub, "rim")
    pieces = [tyre, rim, floor, disc, caliper, hub]
    # the spokes: wide at the rim, narrower at the hub, their inner ends sunk (a concave face)
    r_in, r_out = hub_r * 0.6, inner_r + 0.004
    w_in, w_out, thick, sink = max(0.028, rim_r * 0.15), max(0.05, rim_r * 0.30), 0.028, min(0.03, dish * 0.5)
    for i in range(5):
        spoke = h.extrude([(r_in, -w_in / 2), (r_out, -w_out / 2), (r_out, w_out / 2), (r_in, w_in / 2)],
                          thick, name="spoke")
        for v in spoke.data.vertices:
            v.co.z += face_z - thick - sink * (1 - (v.co.x - r_in) / (r_out - r_in))
        shell._rotate(spoke, Matrix.Rotation(math.radians(90 + 72 * i), 4, "Z"))
        shell._set_material(spoke, "rim")
        pieces.append(spoke)
    turn = Matrix.Rotation(math.radians(90), 4, "Y")  # build axis Z becomes the axle X, outer face +X
    for p in pieces:
        shell._rotate(p, turn)
    return pieces


def build_wheel(ref, index):
    """Wheel `index` (front right, front left, rear right, rear left) at its place on the car."""
    from mathutils import Matrix

    from .. import helpers as h
    from .. import shell

    b = game_body(ref)
    pieces = wheel_pieces(2 * b.wheel_radius, b.tyre_width)
    x, y, z = b.wheel_positions()[index]
    # moved in the mesh itself: joining the pieces into the part keeps the meshes, not their placement
    move = Matrix.Translation((x, y, z)) @ Matrix.Rotation(0.0 if x > 0 else math.pi, 4, "Z")
    for p in pieces:
        shell._rotate(p, move)
        p[h.NO_BEVEL_KEY] = 1
    return pieces


def finish(groups, ref):
    """The materials: one paint colour picked by the generated car's seed (grey for a real car), dark
    opaque glass, matte black wheel wells."""
    from .. import materials

    seed = ref.get("generated", {}).get("seed")
    paint = PALETTE[(seed - 1) % len(PALETTE)] if isinstance(seed, int) else (0.55, 0.56, 0.58)
    materials.car_finish(groups, paint=paint, style="stylised", glass_tint=GLASS)
