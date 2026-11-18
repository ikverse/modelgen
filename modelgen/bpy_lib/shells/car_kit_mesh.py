"""A generated car's face, tail and trim kit (modelgen.tools.car_kit) built onto its game body
(car_game). Runs inside Blender; nothing here cuts the body.

- Lamps, grille, intake, plate, fog lamps: each flat outline from the kit is filled with a grid of
  points (SMALL apart for small parts, LARGE for large), each point found where it meets the body
  along the view's direction (from in front for the front, from behind for the rear, from the side for
  the lamps' side parts), lifted LIFT off the body along the surface's normal and joined into a
  one-sided sheet facing out.
- Mirrors: a small low-poly housing on a stalk, painted, with a dark glass face toward the back.
- Lower trim: one level loop of edges round the body at the trim's height (faces split along a plane,
  not a boolean cut: the body stays one closed solid); the faces below it ahead of the front wheel
  openings or behind the rear ones painted dark.
- Wheel-arch flares: the body's points round each wheel pushed out (bpy_lib.shell.flare) by an amount
  drawn from the car's seed, before the wheel openings are cut.

Every point of every sheet must sit between GAP_MIN and GAP_MAX outside the body; a piece that does
not (or that misses the body) is left out and named (printed as an MG_KIT line), never dropped
silently.
"""

import json
import math
import random
from pathlib import Path

LIFT = 0.0025
GAP_MIN, GAP_MAX = 0.001, 0.005
SMALL, LARGE = 0.02, 0.04   # grid spacing (m); LARGE for parts wider than BIG
BIG = 0.5
FLARE = (0.02, 0.04)        # wheel-arch flare (m), drawn from the seed: a raised blister round each
FLARE_PLATEAU = 0.03        # opening, full out to this far beyond it
FLARE_SPREAD = 0.09         # and gone this far beyond that
LIP_WIDTH = 0.035           # the arch lip: a flange this wide round each opening,
LIP_OUT = 0.012             # standing this far off the body,
LIP_IN = 0.03               # its inner edge turned this far into the opening
LIP_SEGMENTS = 28
SLOT = {"headlamp": "lamp_front", "fog": "lamp_front", "tail": "lamp_rear", "grille": "gloss_black",
        "intake": "gloss_black", "plate": "plate"}
REPORT = {"skipped": [], "gap_mm": None, "pieces": 0}


def load_kit(ref_path):
    """The kit kept for a generated car (car_kit.kit_path), or None (a real car has none)."""
    p = Path(ref_path)
    path = p.parent / "kits" / p.name
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def flare_amount(kit):
    return random.Random(f"flare:{kit.get('type', 'sedan')}:{kit['seed']}").uniform(*FLARE)


# --- the body: flares and the trim loop -------------------------------------------------------

def shape_body(obj, b, kit):
    """Flare the wheel arches and add the lower trim's loop and paint, before the wheel cuts."""
    import bmesh

    from .. import shell

    centres = [(wy, wz) for wy, wz, _ in b.wheels]
    radius = b.arch_radius()
    z_top = b.wheel_radius + radius + FLARE_PLATEAU + FLARE_SPREAD + 0.12
    shell.flare(obj, centres, radius, flare_amount(kit), spread=FLARE_SPREAD, x_from=0.6 * b.width / 2,
                z_top=z_top, plateau=FLARE_PLATEAU)
    me = obj.data
    if all(kit[end].get("trim_z") is None for end in ("front", "rear")):
        return
    me.materials.append(shell.slot("trim"))
    trim_index = len(me.materials) - 1
    (fa, _, _), (ra, _, _) = b.wheels
    reach = b.arch_radius() + 0.02
    for end, inside in (("front", lambda y: y < fa - reach), ("rear", lambda y: y > ra + reach)):
        t = kit[end].get("trim_z")
        if t is None:
            continue
        z = t * b.height
        bm = bmesh.new()
        bm.from_mesh(me)
        bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], dist=1e-6,
                               plane_co=(0, 0, z), plane_no=(0, 0, 1), clear_outer=False, clear_inner=False)
        for f in bm.faces:
            c = f.calc_center_median()
            if c.z < z and inside(c.y):
                f.material_index = trim_index
        bm.to_mesh(me)
        bm.free()
    me.update()


# --- the sheets -------------------------------------------------------------------------------

def _resample_closed(poly, spacing):
    out = []
    n = len(poly)
    for i in range(n):
        (x0, z0), (x1, z1) = poly[i], poly[(i + 1) % n]
        d = math.hypot(x1 - x0, z1 - z0)
        k = max(1, math.ceil(d / spacing))
        out += [(x0 + (x1 - x0) * j / k, z0 + (z1 - z0) * j / k) for j in range(k)]
    clean = [out[0]]
    for p in out[1:]:
        if math.hypot(p[0] - clean[-1][0], p[1] - clean[-1][1]) > 1e-4:
            clean.append(p)
    return clean


def _inside_poly(p, poly):
    x, z = p
    inside = False
    for (x0, z0), (x1, z1) in zip(poly, poly[1:] + poly[:1]):
        if (z0 > z) != (z1 > z) and x < x0 + (z - z0) * (x1 - x0) / (z1 - z0):
            inside = not inside
    return inside


def _seg_d(p, a, b):
    ax, az = b[0] - a[0], b[1] - a[1]
    L = ax * ax + az * az
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * ax + (p[1] - a[1]) * az) / L))
    return math.hypot(p[0] - a[0] - t * ax, p[1] - a[1] - t * az)


def _flat_sheet(poly, spacing):
    """A bmesh of the polygon (u, v) in the plane z = 0, filled with triangles: its outline resampled
    `spacing` apart, a square grid of points `spacing` apart inside it (none closer than half a spacing
    to the outline), triangulated inside the outline (mathutils.geometry.delaunay_2d_cdt)."""
    import bmesh
    from mathutils import Vector
    from mathutils.geometry import delaunay_2d_cdt

    ring = _resample_closed(poly, spacing)
    us, vs = [u for u, _ in ring], [v for _, v in ring]
    inner = []
    u = min(us) + spacing / 2
    while u < max(us):
        v = min(vs) + spacing / 2
        while v < max(vs):
            if _inside_poly((u, v), ring) and \
                    min(_seg_d((u, v), a, b) for a, b in zip(ring, ring[1:] + ring[:1])) > spacing / 2:
                inner.append((u, v))
            v += spacing
        u += spacing
    pts = [Vector(p) for p in ring + inner]
    n = len(ring)
    edges = [(i, (i + 1) % n) for i in range(n)]
    out_verts, _, out_faces, _, _, _ = delaunay_2d_cdt(pts, edges, [list(range(n))], 2, 1e-7)
    bm = bmesh.new()
    verts = [bm.verts.new((p.x, p.y, 0.0)) for p in out_verts]
    for f in out_faces:
        try:
            bm.faces.new([verts[i] for i in f])
        except ValueError:  # a face already there
            pass
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    return bm


def _project(bm, bvh, to_ray):
    """Move every point of the flat sheet onto the body: to_ray(u, v) -> (origin, direction); the point
    goes LIFT off where the ray first meets the body, along the body's normal there. Returns False when
    a ray misses."""
    from mathutils import Vector

    for v in bm.verts:
        origin, direction = to_ray(v.co.x, v.co.y)
        hit, normal, _, _ = bvh.ray_cast(Vector(origin), Vector(direction))
        if hit is None:
            return False
        v.co = hit + normal.normalized() * LIFT
    bm.normal_update()
    view = Vector(to_ray(0.0, 0.0)[1])
    for f in bm.faces:  # face out, toward the viewer
        if f.normal.dot(view) > 0:
            f.normal_flip()
    return True


def _gaps(bm, bvh):
    """(smallest, largest) distance of the sheet's points from the body, negative when inside it."""
    lo, hi = 1.0, -1.0
    for v in bm.verts:
        near, normal, _, d = bvh.find_nearest(v.co)
        if near is None:
            return -1.0, 1.0
        s = d if (v.co - near).dot(normal) >= 0 else -d
        lo, hi = min(lo, s), max(hi, s)
    return lo, hi


def _body_bvh():
    import bpy
    from mathutils.bvhtree import BVHTree

    from .. import helpers as h

    body = next(o for o in bpy.context.scene.objects if o.get(h.PART_KEY) == "body")
    dg = bpy.context.evaluated_depsgraph_get()
    return BVHTree.FromObject(body, dg)


def _sheet_object(bm, name, slot):
    import bpy

    from .. import helpers as h
    from .. import shell

    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    shell._set_material(obj, slot)
    obj[h.NO_BEVEL_KEY] = 1
    return obj


def _rect(u0, v0, u1, v1, r=0.012):
    """A rectangle with rounded corners, as (u, v) points."""
    pts = []
    for cu, cv, a0 in ((u1 - r, v0 + r, -90), (u1 - r, v1 - r, 0), (u0 + r, v1 - r, 90), (u0 + r, v0 + r, 180)):
        for k in range(4):
            a = math.radians(a0 + 90 * k / 3)
            pts.append((cu + r * math.cos(a), cv + r * math.sin(a)))
    return pts


def arch_lips(b, bvh):
    """A painted lip round each wheel opening: a flange LIP_WIDTH wide whose outer edge lies on the
    body and whose face stands LIP_OUT off it, its inner edge turned LIP_IN into the opening. Each
    point is found on the body from the side; where the body ends (under the sill) the lip ends."""
    import bmesh
    import bpy
    from mathutils import Vector

    from .. import helpers as h
    from .. import shell

    R = b.arch_radius()
    x0 = b.width / 2 + 0.5
    out = []
    for wy, wz, _ in b.wheels:
        for side in (1, -1):
            def on_body(r, c, s):
                hit, _, _, _ = bvh.ray_cast(Vector((side * x0, wy + r * c, wz + r * s)), Vector((-side, 0, 0)))
                return None if hit is None or abs(hit.x) < b.width / 2 - 0.3 else abs(hit.x)

            bm = bmesh.new()
            before = None
            for k in range(LIP_SEGMENTS + 1):
                a = math.radians(-25 + 230 * k / LIP_SEGMENTS)
                c, s = math.cos(a), math.sin(a)
                xi, xo = on_body(R + 0.006, c, s), on_body(R + LIP_WIDTH, c, s)
                if xi is None or xo is None:
                    before = None
                    continue
                face = max(xi, xo - 0.004) + LIP_OUT
                row = [bm.verts.new((side * x, wy + r * c, wz + r * s))
                       for x, r in ((xo - 0.003, R + LIP_WIDTH), (face, R + LIP_WIDTH * 0.72), (face, R - 0.006),
                                    (xi - LIP_IN, R - 0.006))]
                if before:
                    for p in range(3):
                        bm.faces.new((before[p], before[p + 1], row[p + 1], row[p]))
                before = row
            if not bm.faces:
                bm.free()
                continue
            bm.normal_update()
            bm.faces.ensure_lookup_table()
            flat = bm.faces[1]  # a face of the lip's flat front: it must look out from the car
            if flat.normal.x * side < 0:
                bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
            me = bpy.data.meshes.new("arch_lip")
            bm.to_mesh(me)
            bm.free()
            obj = bpy.data.objects.new("arch_lip", me)
            bpy.context.scene.collection.objects.link(obj)
            shell._set_material(obj, "paint")
            h.smooth(obj, 40.0)
            obj[h.NO_BEVEL_KEY] = 1
            out.append(obj)
    return out


def build_kit(ref, kit):
    """Every lamp, grille, intake, plate and fog lamp of the kit as sheets on the body (see the module
    docstring), and the wheel arches' lips; returns the objects. Prints one MG_KIT line: the pieces
    left out and the gaps."""
    from . import car_game

    b = car_game.game_body(ref)
    bvh = _body_bvh()
    L, H = b.length, b.height
    pieces, skipped, gaps = [], [], []

    def add(name, poly, slot, to_ray, spacing, shrink=4):
        """shrink: how many times a piece that misses the body or sits too close to it (near the
        rounded nose and tail, or below a high nose) is shrunk to 85% round its centre and tried again
        before it is left out."""
        def again(why):
            if shrink:
                cu, cv = sum(u for u, _ in poly) / len(poly), sum(v for _, v in poly) / len(poly)
                add(name, [(cu + 0.85 * (u - cu), cv + 0.85 * (v - cv)) for u, v in poly], slot, to_ray, spacing,
                    shrink - 1)
            else:
                skipped.append(f"{name} ({why})")

        bm = _flat_sheet(poly, spacing)
        if not _project(bm, bvh, to_ray):
            bm.free()
            again("misses the body")
            return
        lo, hi = _gaps(bm, bvh)
        if not (GAP_MIN <= lo and hi <= GAP_MAX):
            bm.free()
            again(f"gap {lo * 1000:.1f} to {hi * 1000:.1f} mm")
            return
        gaps.append((lo, hi))
        pieces.append(_sheet_object(bm, name.replace(" ", "_"), slot))
        bm.free()

    for end, y0, d in (("front", b.y_front - 0.5, 1.0), ("rear", b.y_rear + 0.5, -1.0)):
        for name, e in kit[end].items():
            if not isinstance(e, dict):
                continue
            for i, poly in enumerate(e["polys"]):
                xs = [x for x, _ in poly]
                spacing = LARGE if max(xs) - min(xs) > BIG else SMALL
                add(f"{end} {name} {i}", poly, SLOT[name],
                    lambda u, v, y0=y0, d=d: ((u, y0, v), (0.0, d, 0.0)), spacing)
    # the lamps' side parts, from the side, on both sides (kept off the very nose and tail)
    for name in ("headlamp", "tail"):
        box = kit["side"][name]["box"]
        y_lo, y_hi = max(0.025, box["y_lo"]), min(0.975, box["y_hi"])
        ya, yb = b.y_front + y_lo * L, b.y_front + y_hi * L
        # kept 3 cm inside the car's own side outline over its length (a low nose or tail is lower than
        # the real cars' lamps sat)
        tops = [b.bounds(ya + (yb - ya) * k / 8) for k in range(9)]
        z_hi = min(box["z_hi"] * H, min(t for _, t in tops) - 0.03)
        z_lo = max(box["z_lo"] * H, max(lo for lo, _ in tops) + 0.03)
        if yb - ya < 0.01 * L or z_hi - z_lo < 0.03:
            skipped.append(f"side {name} (no room inside the side outline)")
            continue
        poly = _rect(ya, z_lo, yb, z_hi)
        for side in (1, -1):
            x0 = side * (b.width / 2 + 0.5)
            add(f"side {name} {'r' if side > 0 else 'l'}", poly, SLOT[name],
                lambda u, v, x0=x0, side=side: ((x0, u, v), (-side, 0.0, 0.0)), SMALL)
    REPORT.update(skipped=skipped, pieces=len(pieces),
                  gap_mm=[round(min(g[0] for g in gaps) * 1000, 2), round(max(g[1] for g in gaps) * 1000, 2)]
                  if gaps else None)
    print("MG_KIT " + json.dumps(REPORT), flush=True)
    if not pieces:
        raise ValueError("no piece of the kit could be placed: " + ", ".join(skipped))
    return pieces + arch_lips(b, bvh)


def build_mirrors(ref, kit):
    """Two door mirrors at the kit's side-view place: a housing on a short stalk out from the body,
    painted, with a dark glass face toward the back (+Y)."""
    from mathutils import Matrix, Vector

    from .. import helpers as h
    from .. import shell
    from . import car_game

    b = car_game.game_body(ref)
    bvh = _body_bvh()
    box = kit["side"]["mirror"]["box"]
    L, H = b.length, b.height
    y = b.y_front + (box["y_lo"] + box["y_hi"]) / 2 * L
    z = (box["z_lo"] + box["z_hi"]) / 2 * H
    ly, lz = max(0.12, (box["y_hi"] - box["y_lo"]) * L), max(0.08, (box["z_hi"] - box["z_lo"]) * H)
    hit, _, _, _ = bvh.ray_cast(Vector((b.width / 2 + 0.5, y, z)), Vector((-1, 0, 0)))
    xs = hit.x if hit is not None else b.width / 2 - 0.05
    out = []
    for side in (1, -1):
        parts = []
        for size, centre, slot in (((0.05, 0.035, 0.03), (xs + 0.02, y, z - lz * 0.2), "trim"),
                                   ((0.09, ly, lz), (xs + 0.085, y, z), "paint"),
                                   ((0.075, 0.006, lz * 0.72), (xs + 0.085, y + ly / 2 + 0.002, z), "glass")):
            p = h.box(size, name="mirror_" + slot)
            h.bevel(p, min(size) * 0.25, segments=1)
            shell._rotate(p, Matrix.Translation((side * centre[0], centre[1], centre[2])))
            shell._set_material(p, slot)
            p[h.NO_BEVEL_KEY] = 1
            parts.append(p)
        out += parts
    return out
