"""Tested building blocks ("kits") for kinds of object the plain helpers get wrong. Runs inside Blender only.

Use them as `from bpy_lib import kits as k`. Each function makes the main form of one kind of object
the right way, so a builder starts from a correct shape and adds its own details with the helpers:

- containers (mug, cup, bowl, bottle, vase, pot): `vessel` is ONE hollow solid with a real inside, a
  rounded rim and a foot; `handle` is a loop handle; `fuse` joins the handle to the body without a seam.
- devices (phone, handheld console, remote, speaker): `device_shell` is one rounded slab; `pocket`
  and `slots` carve recesses into it; `plate`, `round_button` and `dpad` are the pieces set into it.
- buildings: `openings` lays out windows and a door on all four walls; `walls`, `windows` and `door`
  are built from that one layout so they fit; `roof` and `curtain_tower` finish the building.
- wheeled vehicles that are not cars: `wheel`, `wheel_arches`, `axle_positions`.
- scene props: `tree`, `bench`, `fence`, `lamp_post`.
- mechanical parts: `spur_gear`, `helical_gear`, `ring_gear`, `rack`, `bevel_gear` with true involute
  teeth from their real numbers, `shaft`, and `gear_pair_layout` to place a meshing pair exactly.

Conventions are the helpers': metres, Z up, the front faces -Y. Kit meshes carry placeholder
materials, one per named slot (listed in each function); the materials step fills a slot with
`m.assign_slot(obj, "inside", material)`. Never `m.assign` a kit part: that would paint every slot
with one material. Faces of a device or a building are named "front" (-Y), "back" (+Y), "left"
(-X), "right" (+X), "top" (+Z) and "bottom" (-Z); a point on a face is (u, v): (x, z) on the front
and back, (y, z) on the left and right, (x, y) on the top and bottom, always in world axes.
"""

import math
import random

import bmesh
import bpy
from mathutils import Matrix, Vector

from . import helpers as h

SLOT_COLOURS = {
    "outside": (0.55, 0.58, 0.62), "inside": (0.90, 0.90, 0.88), "shell": (0.72, 0.72, 0.70),
    "seam": (0.10, 0.10, 0.11), "pocket": (0.30, 0.30, 0.32), "slot": (0.05, 0.05, 0.055),
    "trim": (0.08, 0.08, 0.09), "button": (0.25, 0.25, 0.27), "screen": (0.30, 0.38, 0.30),
    "wall": (0.70, 0.62, 0.55), "reveal": (0.80, 0.78, 0.74), "plinth": (0.50, 0.50, 0.50),
    "frame": (0.92, 0.92, 0.90), "glass": (0.12, 0.16, 0.20), "sill": (0.75, 0.74, 0.70),
    "door": (0.20, 0.32, 0.22), "metal": (0.20, 0.20, 0.22), "roof": (0.25, 0.25, 0.28),
    "gable": (0.70, 0.62, 0.55), "band": (0.82, 0.81, 0.78), "mullion": (0.22, 0.23, 0.25),
    "tyre": (0.04, 0.04, 0.045), "rim": (0.70, 0.71, 0.73), "trunk": (0.32, 0.22, 0.14),
    "leaves": (0.22, 0.45, 0.20), "wood": (0.50, 0.33, 0.18), "post": (0.85, 0.85, 0.83),
    "rail": (0.85, 0.85, 0.83), "lamp": (0.95, 0.93, 0.80), "body": (0.62, 0.64, 0.68),
    "lamp_red": (0.70, 0.05, 0.05), "lamp_amber": (0.95, 0.60, 0.05), "lamp_clear": (0.92, 0.92, 0.88),
    "tooth_face": (0.66, 0.67, 0.69), "hub": (0.56, 0.57, 0.59), "bore": (0.75, 0.75, 0.76),
    "shaft": (0.78, 0.78, 0.79),
}

# the public functions of each kind of object (so a prompt can show only the kit that fits)
KIT_FUNCTIONS = {
    "container": ["vessel", "handle", "fuse"],
    "device": ["device_shell", "pocket", "slots", "plate", "round_button", "dpad"],
    "building": ["openings", "walls", "windows", "door", "roof", "curtain_tower"],
    "vehicle": ["vehicle_body", "hood", "wheel", "wheel_arches", "axle_positions", "pocket"],
    "scene": ["tree", "bench", "fence", "lamp_post"],
    "mechanical": ["spur_gear", "helical_gear", "ring_gear", "rack", "bevel_gear", "shaft", "gear_pair_layout"],
}

# face -> (axis of u, axis of v, axis the face looks along, which way it looks)
FACES = {"front": (0, 2, 1, -1), "back": (0, 2, 1, 1), "left": (1, 2, 0, -1), "right": (1, 2, 0, 1),
         "top": (0, 1, 2, 1), "bottom": (0, 1, 2, -1)}


# --- small private tools ---------------------------------------------------------------

def _slot(name, colour=None):
    """The placeholder material of a slot (made once), tagged so m.assign_slot can fill it."""
    key = f"slot_{name}"
    mat = bpy.data.materials.get(key)
    if mat is None:
        mat = bpy.data.materials.new(key)
        mat.use_nodes = True
        colour = colour or SLOT_COLOURS.get(name, (0.6, 0.6, 0.6))
        bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        bsdf.inputs["Base Color"].default_value = (*colour, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.45
        mat.diffuse_color = (*colour, 1.0)
        mat["mg_slot"] = name
    return mat


def _slot_index(obj, name):
    """The index of the slot on the object, adding it when it is not there yet."""
    me = obj.data
    for i, mat in enumerate(me.materials):
        if mat is not None and mat.get("mg_slot") == name:
            return i
    me.materials.append(_slot(name))
    return len(me.materials) - 1


def _set_slots(obj, names, choose=None):
    """Give the object these slots; choose(polygon) picks the index of each face (default 0)."""
    me = obj.data
    me.materials.clear()
    for name in names:
        me.materials.append(_slot(name))
    for p in me.polygons:
        p.material_index = choose(p) if choose else 0
    return obj


def _same_slots(obj, like, name=None):
    """Give obj the slot list of `like` and put all its faces in slot `name` (default the first)."""
    index = _slot_index(like, name) if name else 0
    obj.data.materials.clear()
    for mat in like.data.materials:
        obj.data.materials.append(mat)
    for p in obj.data.polygons:
        p.material_index = index
    return obj


def _new_object(bm, name, slots, no_bevel=False):
    """A mesh object from a bmesh whose faces already carry slot indices."""
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for s in slots:
        me.materials.append(_slot(s))
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    if no_bevel:
        obj[h.NO_BEVEL_KEY] = 1
    return obj


def _add_box(bm, size, center, index=0, matrix=None):
    """A box into the bmesh: size (x, y, z) about center, optionally turned by a 3x3 or 4x4 matrix
    applied about the box's own centre."""
    made = bmesh.ops.create_cube(bm, size=1.0)["verts"]
    bmesh.ops.scale(bm, vec=Vector(size), verts=made)
    if matrix is not None:
        bmesh.ops.rotate(bm, cent=(0, 0, 0), matrix=matrix, verts=made)
    bmesh.ops.translate(bm, vec=Vector(center), verts=made)
    faces = {f for v in made for f in v.link_faces}
    for f in faces:
        f.material_index = index
    return made


def _add_revolved(bm, profile, segments, index=0, matrix=None, offset=(0, 0, 0)):
    """A solid of revolution about Z into the bmesh. profile: (radius, z) points whose first and last
    lie on the axis (radius 0)."""
    pts = h._dedupe([(float(r), float(z)) for r, z in profile])
    rings = []
    for r, z in pts:
        if r <= 1e-9:
            rings.append([bm.verts.new((0.0, 0.0, z))])
        else:
            rings.append([bm.verts.new((r * math.cos(2 * math.pi * i / segments),
                                        r * math.sin(2 * math.pi * i / segments), z)) for i in range(segments)])
    faces = []
    for a, b in zip(rings, rings[1:]):
        for i in range(segments):
            j = (i + 1) % segments
            if len(a) == 1 and len(b) == 1:
                continue
            if len(a) == 1:
                faces.append(bm.faces.new((a[0], b[j], b[i])))
            elif len(b) == 1:
                faces.append(bm.faces.new((a[i], a[j], b[0])))
            else:
                faces.append(bm.faces.new((a[i], a[j], b[j], b[i])))
    verts = [v for ring in rings for v in ring]
    for f in faces:
        f.material_index = index
    if matrix is not None:
        bmesh.ops.rotate(bm, cent=(0, 0, 0), matrix=matrix, verts=verts)
    bmesh.ops.translate(bm, vec=Vector(offset), verts=verts)
    return verts


def _world(face, u, v, w):
    """The world point of (u, v) on a face's plane system, w along the axis the face looks along."""
    au, av, aw, _ = FACES[face]
    p = [0.0, 0.0, 0.0]
    p[au], p[av], p[aw] = u, v, w
    return Vector(p)


def _face_plane(obj, face):
    """Where the object's outer surface is on that face: the coordinate along the face's axis."""
    lo, hi = h.bounds(obj)
    _, _, aw, sign = FACES[face]
    return hi[aw] if sign > 0 else lo[aw]


def _prism(face, outline, w0, w1, name="prism"):
    """A closed prism: the (u, v) outline on a face's plane system, from w0 to w1 along its axis."""
    pts = h._dedupe([(float(a), float(b)) for a, b in outline])
    bm = bmesh.new()
    lower = [bm.verts.new(_world(face, u, v, w0)) for u, v in pts]
    upper = [bm.verts.new(_world(face, u, v, w1)) for u, v in pts]
    n = len(pts)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((lower[i], lower[j], upper[j], upper[i]))
    bm.faces.new(lower)
    bm.faces.new(list(reversed(upper)))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _rect_outline(rect, segments=None):
    cu, cv, w, hgt = rect[:4]
    r = rect[4] if len(rect) > 4 else 0.0
    if segments is None:  # a round or nearly round outline needs more sides than a rounded corner
        segments = 10 if r >= min(w, hgt) * 0.3 else 5
    return h.rounded_rect_points(w, hgt, r, corner_segments=segments, center=(cu, cv))


def _open_edges(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    count = sum(1 for e in bm.edges if not e.is_manifold)
    bm.free()
    return count


def _cut(target, cutters, slot=None):
    """Cut the cutters out of the target in one boolean; the cut faces go into `slot` (default the
    target's first slot)."""
    cutters = [c for c in cutters if c is not None]
    if not cutters:
        return target
    cutter = h.join(cutters, "cutter") if len(cutters) > 1 else cutters[0]
    h.apply_modifiers(target)
    if not target.data.materials:  # a plain helper-built target: its own faces become the slot "body"
        _set_slots(target, ["body"])
    _same_slots(cutter, target, slot)
    h.boolean(target, cutter)
    return h.merge_by_distance(target, 1e-6)


_ICO = []


def _icosphere():
    """The points and faces of a unit icosphere (80 triangles subdivided once: 162 points)."""
    if not _ICO:
        bm = bmesh.new()
        bmesh.ops.create_icosphere(bm, subdivisions=2, radius=1.0)
        bm.verts.ensure_lookup_table()
        _ICO.append(([tuple(v.co) for v in bm.verts], [tuple(v.index for v in f.verts) for f in bm.faces]))
        bm.free()
    return _ICO[0]


def _smoothstep(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


# --- containers --------------------------------------------------------------------------

def _inner_wall(outer, wall, floor_z):
    """The outer (radius, z) profile moved `wall` toward the inside; points under floor_z are lifted
    to it, and the first point is the centre of the inner floor."""
    n = len(outer)
    normals = []
    for a, b in zip(outer, outer[1:]):
        dr, dz = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dr, dz) or 1.0
        normals.append((-dz / length, dr / length))  # to the left of the way the profile runs: inside
    inner = []
    for i in range(n):
        if i == 0:
            inner.append((0.0, floor_z))
            continue
        n1 = normals[i - 1]
        n2 = normals[i] if i < n - 1 else n1
        mx, mz = n1[0] + n2[0], n1[1] + n2[1]
        length = math.hypot(mx, mz)
        if length < 1e-9:
            mx, mz, length = n1[0], n1[1], 1.0
        mx, mz = mx / length, mz / length
        scale = wall / max(0.35, mx * n1[0] + mz * n1[1])
        r, z = outer[i][0] + mx * scale, max(floor_z, outer[i][1] + mz * scale)
        if r <= 1e-5:
            raise ValueError(f"the vessel is narrower than twice its wall at z = {outer[i][1]:.4f}")
        inner.append((r, z))
    # drop inner points that fold back under the floor line (a sharp base corner)
    cleaned = [inner[0]]
    for p in inner[1:]:
        if abs(p[1] - floor_z) < 1e-9 and abs(cleaned[-1][1] - floor_z) < 1e-9 and len(cleaned) > 1:
            cleaned[-1] = p if p[0] > cleaned[-1][0] else cleaned[-1]
        else:
            cleaned.append(p)
    return cleaned


def _chain_distance(point, chain):
    best = math.inf
    px, pz = point
    for (ax, az), (bx, bz) in zip(chain, chain[1:]):
        dx, dz = bx - ax, bz - az
        d2 = dx * dx + dz * dz
        t = 0.0 if d2 < 1e-18 else max(0.0, min(1.0, ((px - ax) * dx + (pz - az) * dz) / d2))
        best = min(best, math.hypot(px - (ax + t * dx), pz - (az + t * dz)))
    return best


def vessel(profile, wall=0.004, floor=None, rim=True, foot=None, segments=48, inside_over_rim=0.0, name="vessel"):
    """A hollow container as ONE closed solid: mug, cup, bowl, bottle, vase, pot, bucket.
    profile: the OUTER outline as (radius, z) points from the bottom to the top, e.g. a straight
    mug [(0.040, 0), (0.042, 0.095)], a bowl [(0.03, 0), (0.06, 0.02), (0.075, 0.06)], a bottle with
    shoulder and neck. Pass h.smooth_points(...) for a curved wall. The base is closed flat at the
    first point's height. The vessel is EXACTLY as tall as the profile: its top is the last point's z
    (the rounded rim is inside that height, not on top of it), so a profile from z = 0 to z = 0.100
    gives a vessel 100 mm tall standing on z = 0. wall: wall thickness; the inside follows the
    outside at that distance. floor: thickness of the bottom (default: the wall). rim=True rounds the
    top edge (a half-round across the wall); False leaves it flat. foot=(height, width): a foot ring
    of that height and width under the base, the base inside it recessed; it is inside the profile's
    height too. segments: sides around. inside_over_rim: how far down the OUTSIDE, measured from the
    top, the inside's colour reaches, for a glaze that wraps over the lip (0.004 puts the glaze line
    4 mm below the top edge); 0 splits the two colours at the top of the rim.
    The axis is the Z axis through the origin.
    Slots: "outside" (outer wall, base, and the rim's outer half unless inside_over_rim covers it)
    and "inside" (inner wall, inner floor, the rim's inner half, and the outside above the glaze
    line): fill them with m.assign_slot for a glaze of two colours."""
    pts = h._dedupe([(float(r), float(z)) for r, z in profile])
    if len(pts) < 2:
        raise ValueError("a vessel profile needs at least two (radius, z) points")
    if pts[0][0] > 1e-9:
        pts.insert(0, (0.0, pts[0][1]))
    z0 = pts[0][1]
    z_top = pts[-1][1]
    floor = wall if floor is None else floor
    foot_h, foot_w = foot if foot else (0.0, 0.0)
    if rim:
        # the half-round rim is wall / 2 high: end the wall that much short, so the rim's top is the profile's top
        (r1, z1), (r2, z2) = pts[-2], pts[-1]
        length = math.hypot(r2 - r1, z2 - z1)
        if length > wall / 2 + 1e-6:
            t = (wall / 2) / length
            pts[-1] = (r2 - (r2 - r1) * t, z2 - (z2 - z1) * t)
    line = z_top - inside_over_rim if inside_over_rim > 0 else None
    if line is not None:
        # an edge at the glaze line, so the two colours meet in a clean ring
        for i in range(len(pts) - 1, 0, -1):
            (r1, z1), (r2, z2) = pts[i - 1], pts[i]
            if min(z1, z2) + 1e-7 < line < max(z1, z2) - 1e-7:
                t = (line - z1) / (z2 - z1)
                pts.insert(i, (r1 + (r2 - r1) * t, line))
                break
    inner = _inner_wall(pts, wall, z0 + foot_h + floor)
    outer = list(pts)
    if foot:
        base = pts[1]
        if foot_w >= base[0]:
            raise ValueError("the foot ring is wider than the base")
        outer = [(0.0, z0 + foot_h), (base[0] - foot_w, z0 + foot_h), (base[0] - foot_w, z0)] + pts[1:]
    top_out, top_in = outer[-1], inner[-1]
    arc = []
    if rim:
        mx, mz = (top_out[0] + top_in[0]) / 2, (top_out[1] + top_in[1]) / 2
        ax, az = top_out[0] - mx, top_out[1] - mz
        radius = math.hypot(ax, az)
        tx, tz = pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1]
        length = math.hypot(tx, tz) or 1.0
        tx, tz = tx / length * radius, tz / length * radius
        steps = 6
        arc = [(mx + ax * math.cos(math.pi * i / steps) + tx * math.sin(math.pi * i / steps),
                mz + az * math.cos(math.pi * i / steps) + tz * math.sin(math.pi * i / steps))
               for i in range(1, steps)]
    half = len(arc) // 2
    outer_chain = outer + arc[:half + 1]
    inner_chain = arc[half:] + list(reversed(inner))
    obj = h.revolve(outer + arc + list(reversed(inner)), segments=segments, name=name)

    def side(p):
        c = p.center
        if line is not None and c.z > line:
            return 1
        point = (math.hypot(c.x, c.y), c.z)
        return 1 if _chain_distance(point, inner_chain) < _chain_distance(point, outer_chain) else 0

    return _set_slots(obj, ["outside", "inside"], side)


def _power(value, exponent):
    return math.copysign(abs(value) ** exponent, value)


def handle(top_z, bottom_z, body_radius, reach=0.03, section=(0.014, 0.010), embed=0.002, flare=0.9,
           samples=22, squareness=2.6, name="handle"):
    """A loop handle for a mug, jug or pot, on the +X side, as one closed solid: a smooth D-shaped
    loop of oval section that leaves the wall square to it at the top, runs down and comes back
    square to it at the bottom, and curves out into the wall at both ends with a rounded fillet,
    like a handle moulded on.
    top_z, bottom_z: heights of the centres of its two joints. body_radius: the body's outer radius
    there, or (radius at the top joint, radius at the bottom joint) for a tapered body. reach: how
    far the inside of the loop stands off the body (0.022 to 0.030 takes two or three fingers).
    section: (width along Y, thickness) of its oval cross-section (a mug: 0.014 x 0.010).
    embed: how far its ends sink into the wall (keep it under the wall thickness); the ends follow
    the wall's curve, so the whole end is sunk evenly. flare: how much it swells where it meets the
    body, as a share of half the thickness (0.9 on a 10 mm handle is 4.5 mm more all round at the
    wall, easing out over the last 15 mm or so of the loop); under 0.5 the joint reads as a stub
    pressed on, 0 is a plain butt joint. samples: the least number of rings
    along the loop (22; never under 16); rings are added by themselves where the loop bends, so no
    bend is sharp enough to show as a facet. squareness: 2 is a half-ellipse (a round ear), 2.6 a D,
    4 nearly a squared loop.
    Built in place around the Z axis, so no placing is needed; join it to the body with
    k.fuse(body, [handle]). No slots of its own: fused, it takes the body's "outside". About 1,200
    to 1,500 triangles."""
    width, thick = section
    span = top_z - bottom_z
    if span <= thick * 2:
        raise ValueError("the handle's top and bottom are too close together")
    r_top, r_bottom = body_radius if isinstance(body_radius, (list, tuple)) else (body_radius, body_radius)
    far = max(r_top, r_bottom) + reach + thick / 2  # the centre line's furthest point from the axis
    mid = (top_z + bottom_z) / 2

    def wall_at(z):  # where the handle's ends sit: just under the wall's surface
        t = (top_z - z) / span
        return r_top + (r_bottom - r_top) * max(0.0, min(1.0, t)) - embed

    dense = []
    for i in range(481):
        angle = math.pi / 2 - math.pi * i / 480
        z = mid + (span / 2) * _power(math.sin(angle), 2 / squareness)
        start = wall_at(z)
        dense.append((start + (far - start) * _power(math.cos(angle), 2 / squareness), z))
    lengths = [0.0]
    for p0, p1 in zip(dense, dense[1:]):
        lengths.append(lengths[-1] + math.hypot(p1[0] - p0[0], p1[1] - p0[1]))
    total = lengths[-1]

    def heading(i):
        p0, p1 = dense[max(0, i - 1)], dense[min(len(dense) - 1, i + 1)]
        return math.atan2(p1[1] - p0[1], p1[0] - p0[0])

    # the flare: a quarter ellipse from the wall out along the loop, `fillet` high at the wall and `run`
    # long, so the handle swells into the wall gradually (a short one reads as a collar) and meets it
    # at a shallow angle
    fillet = max(0.0, flare) * thick / 2
    run = min(max(thick, width) * 1.2, total * 0.2)
    # the first ring stands clear of the wall (half a millimetre on a mug): one lying on the wall's surface
    # leaves slivers and a nub where the two are joined
    arc = [math.radians(a) for a in (16, 30, 45, 60, 75, 90)] if fillet > 1e-6 else []
    near_wall = [(embed + run * (1 - math.cos(a)), fillet * (1 - math.sin(a))) for a in arc]  # (distance, swell)
    reach_of_fillet = embed + run if arc else embed

    # rings: one wherever the loop has turned 14 degrees or run a full step since the last one
    step = total / max(16, int(samples))
    stops, turned, last, last_heading = [0.0], 0.0, 0.0, heading(0)
    for i in range(1, len(dense) - 1):
        here = heading(i)
        delta = abs((here - last_heading + math.pi) % (2 * math.pi) - math.pi)
        turned += delta
        last_heading = here
        inside = reach_of_fillet < lengths[i] < total - reach_of_fillet
        if inside and (turned >= math.radians(14) or lengths[i] - last >= step):
            stops.append(lengths[i])
            turned, last = 0.0, lengths[i]
    stops.append(total)
    for distance, _ in near_wall:
        stops += [distance, total - distance]
    stops.sort()
    spaced = [stops[0]]
    for stop in stops[1:]:
        if stop - spaced[-1] > 2e-4:
            spaced.append(stop)
    spaced[-1] = total

    def swell_at(along):
        d = min(along, total - along)
        if fillet <= 1e-6 or d >= reach_of_fillet:
            return 0.0
        if d <= embed + run * (1 - math.cos(arc[0])):
            return fillet * (1 - math.sin(arc[0]))  # inside the wall and at its surface: the flare's widest
        x = (d - embed) / run  # along the loop from the wall's surface, 0 to 1
        return fillet * (1 - math.sqrt(max(1 - (1 - x) ** 2, 0.0)))

    oval = [(math.cos(2 * math.pi * i / 18), math.sin(2 * math.pi * i / 18)) for i in range(18)]
    sections, j = [], 0
    for n, want in enumerate(spaced):
        while j < len(lengths) - 2 and lengths[j + 1] < want:
            j += 1
        gap = lengths[j + 1] - lengths[j]
        t = 0.0 if gap < 1e-12 else (want - lengths[j]) / gap
        x = dense[j][0] + (dense[j + 1][0] - dense[j][0]) * t
        z = dense[j][1] + (dense[j + 1][1] - dense[j][1]) * t
        first, last_ring = n == 0, n == len(spaced) - 1
        if want < reach_of_fillet:
            tx, tz = 1.0, 0.0   # square to the wall all through the fillet
        elif want > total - reach_of_fillet:
            tx, tz = -1.0, 0.0
        else:
            tx, tz = math.cos(heading(j)), math.sin(heading(j))
        nx, nz = -tz, tx  # across the loop, in the handle's plane
        grow = swell_at(want)
        half_w, half_t = width / 2 + grow, thick / 2 + grow
        ring = [(x + nx * half_t * c2, half_w * c1, z + nz * half_t * c2) for c1, c2 in oval]
        # near the wall the rings bend with the wall's curve, so the fillet meets it evenly all the way
        # round instead of standing off it at the sides like a collar; the bend fades out along the loop
        d = min(want, total - want)
        follow = max(0.0, 1.0 - max(0.0, d - embed) / run) if fillet > 1e-6 else 0.0
        if follow > 0:
            bent = []
            for px, py, pz in ring:
                surface = wall_at(pz) + embed
                bent.append((px + follow * (math.sqrt(max(surface * surface - py * py, 1e-10)) - surface), py, pz))
            ring = bent
        if first or last_ring:
            # the end follows the wall's curve, sunk `embed` everywhere: no corner pokes out, none goes through
            ring = [(math.sqrt(max(wall_at(pz) ** 2 - py * py, 1e-10)), py, pz) for _, py, pz in ring]
        sections.append(ring)
    return h.loft(sections, closed=True, cap=True, name=name)


def fuse(body, pieces):
    """Join pieces (a handle, a spout, lugs, feet) into the body as ONE closed solid with no seam,
    notch or gap where they meet. The pieces must already be in place and sink a little into the
    body. The result keeps the body's slots; the pieces' faces take the body's first slot.
    Returns the body."""
    if not isinstance(pieces, (list, tuple)):
        pieces = [pieces]
    backup = body.data.copy()
    for p in pieces:
        h.apply_modifiers(p)
        _same_slots(p, body)
    h.boolean(body, list(pieces), "UNION", keep_cutters=True)
    h.merge_by_distance(body, 1e-6)
    if _open_edges(body):  # the union did not close: keep every piece as a closed solid sunk into the body
        body.data = backup
        return h.join([body] + list(pieces), body.name)
    bpy.data.meshes.remove(backup)
    for p in pieces:
        bpy.data.objects.remove(p, do_unlink=True)
    return body


# --- devices -----------------------------------------------------------------------------

def _slab_outline(width, height, radii, grow=0.0, segments=8):
    """Outline (x, z) of an upright rounded rectangle standing on z = 0, each corner with its own
    radius (bl, br, tr, tl), grown outward by `grow` (negative shrinks); same point count always."""
    w, hgt = width + 2 * grow, height + 2 * grow
    limit = min(w, hgt) / 2 - 1e-6
    lo, hi = -grow, height + grow
    out = []
    for key, sx, top, start in (("br", 1, False, 270), ("tr", 1, True, 0), ("tl", -1, True, 90), ("bl", -1, False, 180)):
        r = max(1e-5, min(limit, radii[key] + grow))
        ox = sx * (w / 2 - r)
        oz = hi - r if top else lo + r
        for i in range(segments + 1):
            a = math.radians(start + 90 * i / segments)
            out.append((ox + r * math.cos(a), oz + r * math.sin(a)))
    return out


def device_shell(width, height, depth, corner_radius, corners=None, edge_radius=0.0015, seam=None, name="shell"):
    """The body of a hand-held device as ONE rounded slab: phone, console, remote, tablet, speaker.
    It stands upright: the front face is at y = -depth / 2, the back at y = +depth / 2, the bottom on
    z = 0, centred on x = 0. corner_radius: the radius of the four corners seen from the front;
    corners: a dict to give some their own, e.g. {"br": 0.018} (keys bl, br, tr, tl, as seen from the
    front, so br is at +x). edge_radius: the rounding of the front and back edges. seam=(y_offset,
    width, depth): a groove round the sides where the two halves of the case meet, y_offset from the
    middle. Carve screens, button wells and ports into it with k.pocket and k.slots.
    Slots: "shell", and "seam" inside the groove."""
    radii = {key: corner_radius for key in ("bl", "br", "tr", "tl")}
    radii.update(corners or {})
    obj = h.extrude(_slab_outline(width, height, radii), depth, name=name, plane="XZ", center=True)
    _set_slots(obj, ["shell"])
    if edge_radius > 0:
        h.bevel(obj, min(edge_radius, depth * 0.45), segments=3)
    if seam:
        y, sw, sd = seam
        inner = _slab_outline(width, height, radii, grow=-sd)
        outer = _slab_outline(width, height, radii, grow=max(0.004, sd))
        bm = bmesh.new()
        rings = [[bm.verts.new((x, yy, z)) for x, z in loop]
                 for loop, yy in ((inner, y - sw / 2), (outer, y - sw / 2), (outer, y + sw / 2), (inner, y + sw / 2))]
        n = len(inner)
        for a, b in zip(rings, rings[1:] + rings[:1]):
            for i in range(n):
                j = (i + 1) % n
                bm.faces.new((a[i], a[j], b[j], b[i]))
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        me = bpy.data.meshes.new("seam_cutter")
        bm.to_mesh(me)
        bm.free()
        ring = bpy.data.objects.new("seam_cutter", me)
        bpy.context.scene.collection.objects.link(ring)
        _cut(obj, [ring], "seam")
    obj[h.NO_BEVEL_KEY] = 1
    return obj


def pocket(target, rect, depth, face="front", slot=None):
    """Sink a rounded-rectangle recess into a face of the target (a screen well, a button well, a
    port, a cartridge bay, a battery door outline). rect = (cu, cv, width, height, corner_radius),
    centred on (cu, cv) of that face: (x, z) on "front" and "back", (y, z) on "left" and "right",
    (x, y) on "top" and "bottom", in world axes. depth: how deep it goes from the target's outermost
    surface on that face. slot: the slot its floor and sides get (e.g. "pocket", "slot", "glass");
    default the target's first slot. The target may be any closed solid, also one built with the
    plain helpers (a vehicle body, a cabinet): its own faces then become the slot "body".
    Returns the target."""
    plane = _face_plane(target, face)
    sign = FACES[face][3]
    cutter = _prism(face, _rect_outline(rect), plane - sign * depth, plane + sign * max(depth, 0.01))
    return _cut(target, [cutter], slot)


def slots(target, center, count, slot_size, pitch, angle_deg=0.0, depth=0.001, face="front", slot="slot"):
    """Cut a row of parallel round-ended slots into a face (a speaker grille, vents).
    center: (u, v) of the middle of the row on that face. count: how many. slot_size: (width, length)
    of one slot. pitch: distance between neighbours, measured across the slots. angle_deg: how the
    slots lean: 0 has them lying along u (horizontal on the front), 90 standing along v. depth: how
    deep. slot: the slot the cut faces get (dark by default). Returns the target."""
    plane = _face_plane(target, face)
    sign = FACES[face][3]
    width, length = slot_size
    a = math.radians(angle_deg)
    along, across = (math.cos(a), math.sin(a)), (-math.sin(a), math.cos(a))
    r = width / 2
    straight = max(0.0, length - width) / 2
    stadium = [(straight + r * math.cos(math.radians(t)), r * math.sin(math.radians(t))) for t in range(-90, 91, 30)]
    stadium += [(-straight + r * math.cos(math.radians(t)), r * math.sin(math.radians(t))) for t in range(90, 271, 30)]
    cutters = []
    for i in range(count):
        off = (i - (count - 1) / 2) * pitch
        cu, cv = center[0] + across[0] * off, center[1] + across[1] * off
        outline = [(cu + along[0] * x + across[0] * y, cv + along[1] * x + across[1] * y) for x, y in stadium]
        cutters.append(_prism(face, outline, plane - sign * depth, plane + sign * max(depth, 0.01)))
    return _cut(target, cutters, slot)


def plate(rect, thickness, face="front", shell_depth=None, plane=None, sink=0.0, slot="trim", name="plate"):
    """A thin rounded-rectangle plate lying on a face: a screen bezel, the screen glass, a camera
    island, a port, a label, a battery door. rect = (cu, cv, width, height, corner_radius) on that
    face ((x, z) on front/back, (y, z) on left/right, (x, y) on top/bottom). Where the face is: give
    shell_depth (the device's depth) for the front or back of a k.device_shell, or `plane`, the
    face's coordinate along its own axis (e.g. the right side of a 0.09 wide shell is plane=0.045).
    sink: how far its underside lies below the face, i.e. the depth of the pocket it sits in; with
    sink equal to thickness it is flush, with less it stands proud. Built in place: do not move it.
    Slots: `slot` (one slot, "trim" by default; use e.g. "screen", "glass", "button")."""
    sign = FACES[face][3]
    if plane is None:
        if shell_depth is None or face not in ("front", "back"):
            raise ValueError("give shell_depth for a front or back plate, or plane for any face")
        plane = sign * shell_depth / 2
    w0 = plane - sign * sink
    obj = _prism(face, _rect_outline(rect), w0, w0 + sign * thickness, name=name)
    return _set_slots(obj, [slot])


def _turn_to_face(obj, face):
    """Turn a piece built standing on z = 0 with its top toward +Z so its top looks out of `face`."""
    turns = {"front": Matrix.Rotation(math.radians(90), 4, "X"), "back": Matrix.Rotation(math.radians(-90), 4, "X"),
             "left": Matrix.Rotation(math.radians(-90), 4, "Y"), "right": Matrix.Rotation(math.radians(90), 4, "Y"),
             "top": Matrix.Identity(4), "bottom": Matrix.Rotation(math.radians(180), 4, "X")}
    obj.data.transform(turns[face])
    obj.data.update()
    return obj


def round_button(radius, height, dome=0.3, face="front", segments=24, slot="button", name="button"):
    """A round push button (also a knob, a lens, an LED): a short cylinder whose top is domed.
    radius, height: its size; dome: 0 a flat top with a small rounded edge, 1 a full dome.
    face: the face it sits on; its base is at the origin and its top points out of that face (for
    "front" toward -Y), so place it at a point on that face, sunk a little into its well.
    Slots: `slot` ("button" by default)."""
    dome = max(0.0, min(1.0, dome))
    rise = max(height * 0.12, height * dome)
    side = height - rise
    profile = [(0.0, 0.0), (radius, 0.0), (radius, side)]
    steps = 6
    for i in range(1, steps + 1):
        a = math.pi / 2 * i / steps
        profile.append((radius * math.cos(a) if i < steps else 0.0, side + rise * math.sin(a)))
    obj = h.revolve(profile, segments=segments, name=name)
    _set_slots(obj, [slot])
    obj[h.NO_BEVEL_KEY] = 1
    return _turn_to_face(obj, face)


def dpad(size, arm, height, face="front", slot="button", name="dpad"):
    """A plus-shaped direction pad. size: its full span; arm: the width of each arm; height: how
    thick it is. face: the face it sits on; its base is at the origin, centred, and it points out of
    that face (for "front" toward -Y). Slots: `slot` ("button" by default)."""
    s, a = size / 2, arm / 2
    outline = [(-a, -s), (a, -s), (a, -a), (s, -a), (s, a), (a, a), (a, s), (-a, s), (-a, a), (-s, a), (-s, -a), (-a, -a)]
    obj = h.extrude(outline, height, name=name)
    _set_slots(obj, [slot])
    h.bevel(obj, min(arm * 0.12, height * 0.3), segments=2)
    return _turn_to_face(obj, face)


# --- buildings -----------------------------------------------------------------------------

def _face_span(face, width, depth):
    """(length of the wall, its plane's coordinate) for a wall face of a width x depth building."""
    if face in ("front", "back"):
        return width, (depth / 2) * FACES[face][3]
    return depth, (width / 2) * FACES[face][3]


def openings(width, depth, floors, floor_height, bays_front, bays_side, window=(1.0, 1.5), sill=0.9,
             ground_floor=0.0, door=None, faces=("front", "back", "left", "right")):
    """The layout of every window and the door of a box-shaped building, as a list that k.walls,
    k.windows and k.door all take, so the three fit each other. Each wall is split into equal bays
    with one window centred in each, on every floor.
    width (X), depth (Y): the building's footprint. floors, floor_height: storeys and their height.
    bays_front: bays across the front and back; bays_side: across the left and right. window: (width,
    height) of a window. sill: height of the window's bottom above its floor. ground_floor: height of
    the ground floor's floor above z = 0 (a raised ground floor or a plinth). door: None, or a dict
    {"bay": 1, "width": 1.0, "height": 2.2, "face": "front"}: the door takes the place of the ground
    floor window of that bay (counted from the left as seen from outside, starting at 0). faces: the
    walls that get openings (all four by default; never leave a visible wall blank).
    Returns dicts {"face", "u", "z", "w", "h", "kind"}: u is the opening's centre along the wall (x on
    front and back, y on left and right, world axes), z its bottom, kind "window" or "door". Edit the
    list freely (drop or resize entries) before building from it."""
    out = []
    door = dict(door) if door else None
    for face in faces:
        length, _ = _face_span(face, width, depth)
        bays = bays_front if face in ("front", "back") else bays_side
        if bays < 1:
            continue
        for floor_i in range(floors):
            for bay in range(bays):
                u = -length / 2 + length * (bay + 0.5) / bays
                # bays are counted left to right as seen from outside the wall
                if face in ("back", "left"):
                    u = -u
                z = ground_floor + floor_i * floor_height
                if door and floor_i == 0 and face == door.get("face", "front") and bay == door.get("bay", bays // 2):
                    out.append({"face": face, "u": u, "z": z, "w": door.get("width", 1.0),
                                "h": door.get("height", 2.2), "kind": "door"})
                    continue
                out.append({"face": face, "u": u, "z": z + sill, "w": window[0], "h": window[1], "kind": "window"})
    return out


def walls(width, depth, height, openings, reveal=0.12, plinth=None, name="walls"):
    """The walls of a box-shaped building as one closed solid, with every opening of the layout sunk
    `reveal` deep into the wall (a recess, not a hole: nothing is seen through it, and the window or
    door sits at its back). width (X), depth (Y), height: the box; it stands on z = 0, centred on the
    origin. openings: the list from k.openings. reveal: how far the windows sit back from the wall
    face (0.05 to 0.15 for real buildings). plinth=(height, out): a base course that tall, standing
    `out` proud of the wall. Slots: "wall" (the faces), "reveal" (the sides and back of each recess)
    and "plinth"."""
    obj = h.box((width, depth, height), anchor="bottom", name=name)
    _set_slots(obj, ["wall", "reveal", "plinth"])
    if plinth:
        ph, out = plinth
        base = h.box((width + 2 * out, depth + 2 * out, ph), anchor="bottom", name="plinth")
        _same_slots(base, obj, "plinth")
        h.boolean(obj, base, "UNION")
    cutters = []
    for o in openings:
        _, plane = _face_span(o["face"], width, depth)
        sign = FACES[o["face"]][3]
        outline = [(o["u"] - o["w"] / 2, o["z"]), (o["u"] + o["w"] / 2, o["z"]),
                   (o["u"] + o["w"] / 2, o["z"] + o["h"]), (o["u"] - o["w"] / 2, o["z"] + o["h"])]
        cutters.append(_prism(o["face"], outline, plane - sign * reveal, plane + sign * (reveal + 1.0)))
    _cut(obj, cutters, "reveal")
    obj[h.NO_BEVEL_KEY] = 1
    return obj


def _wall_box(bm, face, plane, u, v, w_in, w_out, su, sv, index):
    """A box on a wall face: centred on (u, v), su wide and sv high, running from w_in to w_out
    metres inside the wall plane (negative = outside it)."""
    sign = FACES[face][3]
    a, b = plane - sign * w_in, plane - sign * w_out
    au, av, aw, _ = FACES[face]
    size, centre = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
    size[au], size[av], size[aw] = su, sv, abs(a - b)
    centre[au], centre[av], centre[aw] = u, v, (a + b) / 2
    _add_box(bm, size, centre, index)


def windows(openings, width, depth, reveal=0.12, frame=0.06, bars=(1, 2), sill=0.04, name="windows"):
    """Every window of the layout, built in place in the recesses of k.walls made from the same
    numbers: a dark pane at the back of each recess, a frame round it, glazing bars and a sill.
    openings, width, depth, reveal: exactly what k.walls got. frame: the width of the frame members.
    bars: (panes across, panes up), e.g. (1, 2) a sash window, (2, 3) small panes, (1, 1) one sheet.
    sill: the sill's thickness (0 for none); it stands a little proud of the wall.
    Built in place: leave it at the origin, like the walls. Slots: "frame", "glass", "sill"."""
    bm = bmesh.new()
    pane_t = 0.012
    frame_d = min(0.05, reveal * 0.6)
    for o in openings:
        if o.get("kind", "window") != "window":
            continue
        face = o["face"]
        _, plane = _face_span(face, width, depth)
        u, w, hgt = o["u"], o["w"], o["h"]
        z = o["z"]
        mid = z + hgt / 2
        f = min(frame, w / 4, hgt / 4)
        lap = f * 0.25  # pieces overlap by this much instead of meeting corner to corner
        _wall_box(bm, face, plane, u, mid, reveal, reveal - pane_t, w - 2 * lap, hgt - 2 * lap, 1)
        for du in (-(w - f) / 2, (w - f) / 2):
            _wall_box(bm, face, plane, u + du, mid, reveal, reveal - frame_d, f, hgt, 0)
        for dv in (-(hgt - f) / 2, (hgt - f) / 2):
            _wall_box(bm, face, plane, u, mid + dv, reveal, reveal - frame_d * 0.96, w - 2 * f + 2 * lap, f, 0)
        cols, rows = bars
        bar = f * 0.5
        for c in range(1, cols):
            _wall_box(bm, face, plane, u - w / 2 + w * c / cols, mid, reveal, reveal - frame_d * 0.8, bar,
                      hgt - 2 * f + 2 * lap, 0)
        for r in range(1, rows):
            _wall_box(bm, face, plane, u, z + hgt * r / rows, reveal, reveal - frame_d * 0.75, w - 2 * f + 2 * lap,
                      bar, 0)
        if sill > 0:
            _wall_box(bm, face, plane, u, z + sill / 2, reveal - frame_d * 0.5, -0.03, w - 0.004, sill, 2)
    return _new_object(bm, name, ["frame", "glass", "sill"], no_bevel=True)


def door(openings, width, depth, reveal=0.12, frame=0.07, panels=(2, 2), name="door"):
    """Every door of the layout, built in place in its recess of k.walls: a leaf with raised panels, a
    frame round the top and sides, a handle and a step-high threshold. openings, width, depth,
    reveal: exactly what k.walls got. frame: the frame's width. panels: (across, up) raised panels on
    the leaf. Built in place: leave it at the origin. Slots: "door" (the leaf), "frame", "metal"
    (handle and threshold)."""
    bm = bmesh.new()
    leaf_t = 0.045
    for o in openings:
        if o.get("kind") != "door":
            continue
        face = o["face"]
        _, plane = _face_span(face, width, depth)
        u, w, hgt, z = o["u"], o["w"], o["h"], o["z"]
        mid = z + hgt / 2
        f = min(frame, w / 5)
        lap = f * 0.25  # pieces overlap by this much instead of meeting corner to corner
        _wall_box(bm, face, plane, u, mid - lap / 2, reveal, reveal - leaf_t, w - 2 * lap, hgt - lap, 0)
        frame_d = min(0.07, reveal * 0.75)
        for du in (-(w - f) / 2, (w - f) / 2):
            _wall_box(bm, face, plane, u + du, mid, reveal, reveal - frame_d, f, hgt, 1)
        _wall_box(bm, face, plane, u, z + hgt - f / 2, reveal, reveal - frame_d * 0.96, w - 2 * f + 2 * lap, f, 1)
        _wall_box(bm, face, plane, u, z + 0.015, reveal - frame_d * 0.5, -0.02, w - 0.004, 0.03, 2)
        cols, rows = panels
        leaf_w, leaf_h = w - 2 * f, hgt - f - 0.03
        gap = min(0.09, leaf_w / (cols * 3))
        pw = (leaf_w - gap * (cols + 1)) / cols
        ph = (leaf_h - gap * (rows + 1)) / rows
        for c in range(cols):
            for r in range(rows):
                pu = u - leaf_w / 2 + gap + pw / 2 + c * (pw + gap)
                pv = z + 0.03 + gap + ph / 2 + r * (ph + gap)
                _wall_box(bm, face, plane, pu, pv, reveal - leaf_t + 0.002, reveal - leaf_t - 0.012, pw, ph, 0)
        side = 1 if face in ("front", "right") else -1
        _wall_box(bm, face, plane, u + side * (leaf_w / 2 - 0.07), z + 1.0, reveal - leaf_t + 0.002,
                  reveal - leaf_t - 0.05, 0.03, 0.14, 2)
    return _new_object(bm, name, ["door", "frame", "metal"], no_bevel=True)


def roof(width, depth, kind="gable", pitch_deg=40.0, overhang=0.3, thickness=0.15, ridge="x", parapet=0.5,
         name="roof"):
    """A roof for a width (X) x depth (Y) box building, as one closed solid. Its underside is flat on
    z = 0, centred on the origin: place it at the height of the wall top.
    kind "gable": two slopes meeting at a ridge, with the two triangular gable ends closed (slot
    "gable", so they can take the wall material); "hip": slopes on all four sides; "flat": a slab
    with a parapet wall `parapet` high round its edge. pitch_deg: the slope. overhang: how far the
    eaves stand out past the walls (use 0 for flat). thickness: the eave's edge height (for flat, the
    slab). ridge: "x" or "y", the direction the ridge runs (gable and hip).
    Slots: "roof" (slopes, eaves and top), "gable" (gable ends; parapet faces on a flat roof)."""
    w, d = width + 2 * overhang, depth + 2 * overhang
    tan = math.tan(math.radians(pitch_deg))
    if kind == "flat":
        obj = h.box((w, d, thickness + parapet), anchor="bottom", name=name)
        _set_slots(obj, ["roof", "gable"], lambda p: 1 if abs(p.normal.z) < 0.5 else 0)
        t = min(0.25, w / 6, d / 6)
        if parapet > 0:
            well = h.box((w - 2 * t, d - 2 * t, parapet + 0.5), location=(0, 0, thickness), anchor="bottom")
            _cut(obj, [well], "roof")
        obj[h.NO_BEVEL_KEY] = 1
        return obj
    along, across = (w, d) if ridge == "x" else (d, w)
    half = across / 2
    rise = half * tan
    bm = bmesh.new()
    if kind == "gable":
        section = [(-half, 0.0), (half, 0.0), (half, thickness), (0.0, thickness + rise), (-half, thickness)]
        ends = [[bm.verts.new((x, y, z)) for y, z in section] for x in (-along / 2, along / 2)]
        n = len(section)
        for i in range(n):
            j = (i + 1) % n
            bm.faces.new((ends[0][i], ends[0][j], ends[1][j], ends[1][i])).material_index = 0
        bm.faces.new(ends[0]).material_index = 1
        bm.faces.new(list(reversed(ends[1]))).material_index = 1
    elif kind == "hip":
        run = max(0.0, along / 2 - half)  # half the ridge length
        low = [bm.verts.new((sx * along / 2, sy * half, 0.0)) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        eave = [bm.verts.new((sx * along / 2, sy * half, thickness)) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        bm.faces.new(list(reversed(low)))
        for i in range(4):
            j = (i + 1) % 4
            bm.faces.new((low[i], low[j], eave[j], eave[i]))
        if run > 1e-6:
            r0 = bm.verts.new((-run, 0.0, thickness + rise))
            r1 = bm.verts.new((run, 0.0, thickness + rise))
            bm.faces.new((eave[0], eave[1], r1, r0))
            bm.faces.new((eave[1], eave[2], r1))
            bm.faces.new((eave[2], eave[3], r0, r1))
            bm.faces.new((eave[3], eave[0], r0))
        else:
            top = bm.verts.new((0.0, 0.0, thickness + rise))
            for i in range(4):
                bm.faces.new((eave[i], eave[(i + 1) % 4], top))
    else:
        bm.free()
        raise ValueError("roof kind must be gable, hip or flat")
    if ridge != "x":
        bmesh.ops.rotate(bm, cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(90), 3, "Z"), verts=bm.verts[:])
    return _new_object(bm, name, ["roof", "gable"], no_bevel=True)


def curtain_tower(width, depth, height, floor_height=3.6, bay=1.5, band=0.5, mullion=0.08, lobby=0.0,
                  name="tower"):
    """A modern glass tower as one part with no booleans: a glass shaft, a spandrel band at every
    floor and at the top, and vertical mullions on all four faces. It stands on z = 0, centred on
    the origin. width (X), depth (Y), height: the shaft. floor_height: storey height (a band every
    storey). bay: spacing of the mullions. band: height of each floor band (0 for none). mullion:
    the mullions' width (0 for none); they stand a little proud of the bands. lobby: height of a
    taller ground floor with no bands below it (0: the first band is one storey up).
    Slots: "glass" (the shaft), "band", "mullion". Vary floor_height, bay, band and mullion between
    towers of one group so they do not read as copies."""
    bm = bmesh.new()
    _add_box(bm, (width, depth, height), (0, 0, height / 2), 0)
    proud = 0.06
    if band > 0:
        z = lobby if lobby > 0 else floor_height
        levels = []
        while z < height - band * 1.5:
            levels.append(z)
            z += floor_height
        levels.append(height - band / 2 + 0.02)
        for z in levels:
            _add_box(bm, (width + 2 * proud, depth + 2 * proud, band), (0, 0, z), 1)
    if mullion > 0:
        out = proud * 2
        for length, fixed, axis in ((width, depth / 2, 0), (depth, width / 2, 1)):
            count = max(1, round(length / bay))
            for i in range(count + 1):
                t = -length / 2 + length * i / count
                for side in (-1, 1):
                    centre = [0.0, 0.0, height / 2]
                    centre[axis] = t
                    centre[1 - axis] = side * (fixed + out / 2 - 0.01)
                    size = [0.0, 0.0, height]
                    size[axis] = mullion
                    size[1 - axis] = out + 0.02
                    _add_box(bm, size, centre, 2)
    return _new_object(bm, name, ["glass", "band", "mullion"], no_bevel=True)


# --- vehicles ----------------------------------------------------------------------------

def wheel(radius, width, rim_ratio=0.62, style="disc", spokes=5, dual=False, hub_reach=0.0, segments=32,
          name="wheel"):
    """A road wheel for a bus, truck, trailer, cart or train-like vehicle: a tyre with rounded
    shoulders, a dished rim and a hub. The axle runs along X and the outer face looks toward +X; it
    is centred on the origin (the centre of the outer tyre), so place it at the axle end and turn it
    180 degrees about Z for the other side. radius: the tyre's outer radius. width: one tyre's width.
    rim_ratio: rim radius over tyre radius (0.62 a bus or truck, 0.7 a van). style: "disc" (a plain
    dished disc), "steel" (a disc with a ring of round holes and wheel nuts, as on trucks and buses)
    or "spoke" (open spokes; `spokes` of them). dual=True adds a second tyre inboard (toward -X), as
    on a truck's rear axle. hub_reach: the length of an axle stub running inboard from the back of
    the rim, so the wheel touches the body or the back of its wheel arch (a wheel that touches
    nothing counts as floating). segments: sides around. Slots: "tyre", "rim"."""
    R, W = radius, width
    rr = R * rim_ratio
    sh = min(W * 0.28, (R - rr) * 0.6)
    bm = bmesh.new()

    def tyre(z_off):
        prof = [(rr - 0.004, -W * 0.46), (R - sh, -W / 2)]
        prof += [(R - sh + sh * math.sin(math.radians(a)), -W / 2 + sh - sh * math.cos(math.radians(a)))
                 for a in (30, 60, 90)]
        prof += [(R - sh + sh * math.cos(math.radians(a)), W / 2 - sh + sh * math.sin(math.radians(a)))
                 for a in (0, 30, 60, 90)]
        prof += [(rr - 0.004, W * 0.46)]
        n = len(prof)
        rings = [[bm.verts.new((r * math.cos(2 * math.pi * i / segments), r * math.sin(2 * math.pi * i / segments),
                                z + z_off)) for i in range(segments)] for r, z in prof]
        for k in range(n):
            a, b = rings[k], rings[(k + 1) % n]
            for i in range(segments):
                j = (i + 1) % segments
                bm.faces.new((a[i], a[j], b[j], b[i])).material_index = 0

    tyre(0.0)
    inner_end = -W * 0.47
    if dual:
        tyre(-(W * 1.12))
        inner_end = -(W * 1.12) - W * 0.47
    tyres = _new_object(bm, name, ["tyre", "rim"], no_bevel=True)
    dish = min(W * 0.32, rr * 0.35)
    face = W * 0.47
    lip = rr + 0.004
    back = [(0.0, inner_end - hub_reach), (rr * 0.28, inner_end - hub_reach), (rr * 0.28, inner_end)]         if hub_reach > 0 else [(0.0, inner_end)]
    profile = back + [(lip, inner_end), (lip, face), (rr * 0.93, face), (rr * 0.84, face - dish),
               (rr * 0.36, face - dish * 0.75), (rr * 0.30, face - dish * 0.2), (0.0, face - dish * 0.2)]
    bm = bmesh.new()
    _add_revolved(bm, profile, segments, 1)
    rim = _new_object(bm, "rim", ["tyre", "rim"], no_bevel=True)
    pieces = [tyres, rim]
    if style in ("spoke", "steel"):
        cutters = []
        count = max(3, int(spokes)) if style == "spoke" else max(6, int(spokes) + 3)
        z0, z1 = inner_end - 0.05, face + 0.05
        for i in range(count):
            if style == "spoke":
                a0 = 2 * math.pi * (i + 0.22) / count
                a1 = 2 * math.pi * (i + 0.78) / count
                r_in, r_out = rr * 0.42, rr * 0.80
                pts = [(r_out * math.cos(a0 + (a1 - a0) * k / 6), r_out * math.sin(a0 + (a1 - a0) * k / 6))
                       for k in range(7)]
                am = (a0 + a1) / 2
                spread = (a1 - a0) * 0.18
                pts += [(r_in * math.cos(am + spread), r_in * math.sin(am + spread)),
                        (r_in * math.cos(am - spread), r_in * math.sin(am - spread))]
            else:
                a = 2 * math.pi * (i + 0.5) / count
                cx, cy, hole = rr * 0.70 * math.cos(a), rr * 0.70 * math.sin(a), rr * 0.075
                pts = [(cx + hole * math.cos(2 * math.pi * k / 10), cy + hole * math.sin(2 * math.pi * k / 10))
                       for k in range(10)]
            cutters.append(_prism("top", pts, z0, z1))
        before = rim.data.copy()
        _cut(rim, cutters, "rim")
        if _open_edges(rim):  # the cut did not come out clean: keep the plain disc
            rim.data = before
        else:
            bpy.data.meshes.remove(before)
    if style == "steel":
        bm = bmesh.new()
        nuts = max(5, int(spokes) + 1)
        for i in range(nuts):
            a = 2 * math.pi * i / nuts
            c = (rr * 0.50 * math.cos(a), rr * 0.50 * math.sin(a), face - dish * 0.72)
            _add_revolved(bm, [(0.0, -0.01), (rr * 0.05, -0.01), (rr * 0.05, 0.02), (0.0, 0.02)], 8, 1, offset=c)
        pieces.append(_new_object(bm, "nuts", ["tyre", "rim"], no_bevel=True))
    obj = h.join(pieces, name)
    obj[h.NO_BEVEL_KEY] = 1
    obj.data.transform(Matrix.Rotation(math.radians(90), 4, "Y"))  # the build axis Z becomes the axle X
    obj.data.update()
    return obj


def _window_row(row):
    """(count, first, last, z_bottom, z_top) -> the rects (centre, centre z, width, height, radius) of
    `count` equal windows filling first..last with 8 cm pillars between them."""
    count, first, last, z0, z1 = row
    count = max(1, int(count))
    lo, hi = min(first, last), max(first, last)
    pillar = 0.08 if count > 1 else 0.0
    width = ((hi - lo) - pillar * (count - 1)) / count
    if width <= 0.05 or z1 - z0 <= 0.05:
        raise ValueError("the window row does not fit: too many windows for its length, or no height")
    radius = min(0.05, width * 0.2, (z1 - z0) * 0.2)
    return [(lo + width / 2 + i * (width + pillar), (z0 + z1) / 2, width, z1 - z0, radius) for i in range(count)]


def vehicle_body(width, length, height, floor=0.3, roof_radius=0.25, windows=None, windshield=None,
                 rear_window=None, doors=None, lamps=None, glass_depth=0.03, name="body"):
    """The body of a bus, van, truck cab, trailer or railway coach as ONE closed solid with its glass,
    doors and lamps sunk into it (nothing stuck on): a box with rounded roof edges, its underside
    `floor` above the ground and its top at `height`, centred on the origin in X and Y, the front
    toward -Y. Call it again without glass for a cargo box or a trailer; an engine hood is k.hood,
    which slopes and narrows (a box makes a hood look too long).
    width, length, height: overall size in metres (height from the ground, z = 0, to the roof).
    floor: height of the underside. roof_radius: rounding of the two long roof edges.
    windows: the side windows, on BOTH sides alike: one row (count, y_first, y_last, z_bottom, z_top)
    or a list of rows; a row's `count` equal windows fill y_first..y_last with pillars between.
    windshield: (z_bottom, z_top) or (z_bottom, z_top, side_margin): one pane across the front.
    rear_window: the same, across the back.
    doors: a list of (face, centre, width, z_bottom, z_top) or with a sixth value False for a door
    without glass: a door panel outlined by a shallow recess, its upper half glazed. face is "left",
    "right" (centre is then a Y position), "front" or "back" (centre is an X position). A door goes
    where no window of a row is, so end the window row before it.
    lamps: a list of (face, centre, z, size, slot): a recessed lamp; size is a diameter for a round
    lamp or (width, height); slot is "lamp_red", "lamp_amber" or "lamp_clear" (headlights), so each
    colour is its own material. Faces as for doors.
    glass_depth: how far the glass sits behind the body's surface.
    Cut the wheel arches afterwards with k.wheel_arches(body, ...).
    Slots: "body" (index 0), "glass", "door", "lamp_red", "lamp_amber", "lamp_clear" (those used).
    About 200 triangles plus 40 to 60 for each opening."""
    if height - floor <= 0.2:
        raise ValueError("the body needs some height above its floor")
    rr = max(0.0, min(roof_radius, width / 2 - 0.01, (height - floor) / 2))
    half = width / 2
    profile = [(-half, floor), (half, floor), (half, height - rr)]
    if rr > 1e-6:
        profile += [(half - rr + x, height - rr + z) for x, z in h.arc_points(rr, 0, 90, 5)[1:]]
        profile += [(-half + rr + x, height - rr + z) for x, z in h.arc_points(rr, 90, 180, 5)]
    else:
        profile += [(half, height), (-half, height)]
    profile += [(-half, height - rr)] if rr > 1e-6 and abs(profile[-1][1] - (height - rr)) > 1e-9 else []
    body = h.extrude(profile, length, plane="XZ", name=name)  # runs from y = 0 toward -Y
    h.place(body, location=(0, length / 2, 0))
    h.apply_transform(body)
    h.bevel(body, min(0.05, (height - floor) * 0.03, width * 0.03), segments=2)
    _set_slots(body, ["body"])

    planes = {face: _face_plane(body, face) for face in ("front", "back", "left", "right")}

    def cutter(face, rect, depth, round_lamp=False):
        sign = FACES[face][3]
        outline = _rect_outline(rect, segments=4 if round_lamp else 2)  # light corners: these are game meshes
        return _prism(face, outline, planes[face] - sign * depth, planes[face] + sign * max(depth, 0.05))

    # side windows never overlap one another, so each side is one cut; every other opening is cut by
    # itself, so a door's pane inside a rear window, or a lamp beside a door, cannot spoil the cut
    side_glass, glass, panels, lamp_cuts = [], [], [], []
    rows = [] if windows is None else ([windows] if not isinstance(windows[0], (list, tuple)) else list(windows))
    for row in rows:
        for rect in _window_row(row):
            side_glass += [cutter("left", rect, glass_depth), cutter("right", rect, glass_depth)]
    for face, pane in (("front", windshield), ("back", rear_window)):
        if pane:
            margin = pane[2] if len(pane) > 2 else max(0.12, rr * 0.6)
            z0, z1 = pane[0], pane[1]
            rect = (0.0, (z0 + z1) / 2, width - 2 * margin, z1 - z0, min(0.08, (z1 - z0) * 0.2))
            glass.append(cutter(face, rect, glass_depth))
    for door in doors or []:
        face, centre, wide, z0, z1 = door[:5]
        glazed = door[5] if len(door) > 5 else True
        panels.append(cutter(face, (centre, (z0 + z1) / 2, wide, z1 - z0, 0.03), 0.015))
        if glazed:
            top = z1 - 0.10
            bottom = z0 + (z1 - z0) * 0.48
            glass.append(cutter(face, (centre, (top + bottom) / 2, wide - 0.16, top - bottom, 0.03), glass_depth + 0.015))
    for face, centre, z, size, slot in lamps or []:
        if isinstance(size, (list, tuple)):
            rect = (centre, z, size[0], size[1], min(size) * 0.2)
        else:
            rect = (centre, z, size, size, size / 2)
        lamp_cuts.append((slot, cutter(face, rect, 0.02, round_lamp=not isinstance(size, (list, tuple)))))
    for piece in panels:
        _cut(body, [piece], "door")
    if side_glass:
        _cut(body, side_glass, "glass")
    for piece in glass:
        _cut(body, [piece], "glass")
    for slot, piece in lamp_cuts:
        _cut(body, [piece], slot)
    body[h.NO_BEVEL_KEY] = 1
    return body


def hood(width, length, height, floor=0.45, nose_height=None, nose_width=None, corner_radius=0.18,
         grille=None, lamps=None, fenders=None, name="hood"):
    """The engine hood (nose) of a bus, truck, tractor or van as one closed solid: it starts at the
    cab or body as wide and high as given, then narrows and slopes down to the grille, with rounded
    top edges. Not a box: a box of the same length reads as a longer, heavier nose.
    Its REAR face is at y = +length / 2 (set that against the body's front), its nose at
    y = -length / 2; it is centred on the origin in X, and its underside is at z = `floor`.
    width, height: at the rear, where it meets the body; height is measured from the ground. Keep it
    narrower than the body (about 90%) and its top below the windshield's bottom edge.
    length: from the body's front face to the grille. Take it from a side view of the real vehicle
    (the distance from the bumper to where the cab begins, less the bumper), never from a round
    share of the vehicle's length.
    nose_height: the top's height at the grille (default: four fifths of the way up from the floor
    to `height`). nose_width: the width at the grille (default 86% of `width`).
    grille: (width, height, z_centre): a dark recess in the nose (slot "trim").
    lamps: a list of (x, z, size, slot): recessed lamps in the nose; size is a diameter or
    (width, height); slot "lamp_clear" for headlights, "lamp_amber" for indicators.
    fenders: (axle_y, wheel_radius): a fender over each front wheel, standing out to the hood's
    full rear width, with axle_y in the hood's own frame (0 is its middle). Cut the wheel arches
    afterwards with k.wheel_arches(hood, [axle_y], wheel_radius + 0.08).
    Slots: "body" (index 0), "trim", and the lamp slots used. About 150 to 500 triangles."""
    top_rear = height
    if top_rear - floor <= 0.1:
        raise ValueError("the hood needs some height above its floor")
    top_nose = floor + (top_rear - floor) * 0.8 if nose_height is None else nose_height
    w_nose = width * 0.86 if nose_width is None else nose_width
    sections = []
    for t in (0.0, 0.3, 0.6, 0.85, 0.97, 1.0):
        ease = t * t * (3 - 2 * t)
        w = width + (w_nose - width) * ease
        top = top_rear + (top_nose - top_rear) * (t ** 1.4)
        if t == 1.0:  # the nose's leading edge is rounded off a little
            w, top = w - 0.04, top - 0.03
        r = max(0.01, min(corner_radius, w / 4, (top - floor) / 2))
        y = length / 2 - length * t
        half = w / 2
        outline = [(-half, floor), (half, floor), (half, top - r)]
        outline += [(half - r + x, top - r + z) for x, z in h.arc_points(r, 0, 90, 4)[1:]]
        outline += [(-half + r + x, top - r + z) for x, z in h.arc_points(r, 90, 180, 4)]
        sections.append([(x, y, z) for x, z in outline])
    obj = h.loft(sections, closed=True, cap=True, name=name)
    _set_slots(obj, ["body"])
    if fenders:
        axle_y, radius = fenders
        inner, outer = radius + 0.06, radius + 0.18
        band = [(axle_y + outer * math.cos(math.radians(a)), radius + outer * math.sin(math.radians(a)))
                for a in range(0, 181, 15)]
        band += [(axle_y + inner * math.cos(math.radians(a)), radius + inner * math.sin(math.radians(a)))
                 for a in range(180, -1, -15)]
        band = [(y, max(z, floor)) for y, z in band]
        pieces = [_prism("right", band, w_nose / 2 - 0.15, width / 2 + 0.02, name="fender"),
                  _prism("left", band, -(w_nose / 2 - 0.15), -(width / 2 + 0.02), name="fender")]
        obj = fuse(obj, pieces)
    plane = -length / 2
    cuts = []
    if grille:
        gw, gh, gz = grille
        cuts.append(("trim", (0.0, gz, gw, gh, min(gw, gh) * 0.08), 0.04, False))
    for x, z, size, slot in lamps or []:
        if isinstance(size, (list, tuple)):
            cuts.append((slot, (x, z, size[0], size[1], min(size) * 0.2), 0.02, False))
        else:
            cuts.append((slot, (x, z, size, size, size / 2), 0.02, True))
    for slot, rect, depth, round_lamp in cuts:
        outline = _rect_outline(rect, segments=4 if round_lamp else 2)
        _cut(obj, [_prism("front", outline, plane + depth, plane - 0.1)], slot)
    obj[h.NO_BEVEL_KEY] = 1
    return obj


def wheel_arches(target, axles, radius, z=None, depth=0.4, slot="trim"):
    """Cut wheel arches into both sides of a vehicle body, so the wheels sit in the body instead of
    hiding under it. target: the body, a closed solid lying along Y (any helper-built shape).
    axles: the axle positions along Y, e.g. [-2.6, 2.3]. radius: the arch radius (the tyre radius
    plus 6 to 12 cm of clearance). z: the height of the arch centre (default: the tyre radius, i.e.
    `radius` less 8 cm). depth: how far each arch goes in from the body side (at least the tyre
    width, doubled tyres need twice that). The arch is open downward. slot: the slot its inside
    gets (dark "trim" by default). Returns the target."""
    lo, hi = h.bounds(target)
    z = (radius - 0.08) if z is None else z
    cutters = []
    for y in axles:
        outline = [(y + radius * math.cos(math.radians(a)), z + radius * math.sin(math.radians(a)))
                   for a in range(0, 181, 12)]
        outline += [(y - radius, lo.z - 0.5), (y + radius, lo.z - 0.5)]
        cutters.append(_prism("right", outline, hi.x - depth, hi.x + 0.5))
        cutters.append(_prism("left", outline, lo.x + depth, lo.x - 0.5))
    return _cut(target, cutters, slot)


def axle_positions(axles, track, radius):
    """Where the wheels of a vehicle go: for each axle position along Y in `axles` (e.g. [-3.2, 2.4]
    for a bus whose front axle is 3.2 m ahead of the middle), the two wheel centres at x = -track / 2
    and +track / 2, at height `radius` so the tyres stand on the floor. Returns a list of dicts
    {"location": (x, y, z), "rotation_deg": (0, 0, 0 or 180)}: the left wheels (-X) are turned 180
    degrees so their outer face looks outward. Use with h.place / h.instance in the assembly."""
    out = []
    for y in axles:
        out.append({"location": (track / 2, y, radius), "rotation_deg": (0, 0, 0)})
        out.append({"location": (-track / 2, y, radius), "rotation_deg": (0, 0, 180)})
    return out


# --- scene props -------------------------------------------------------------------------

def tree(height=4.0, kind="round", seed=0, name="tree"):
    """A stylised low-poly tree as one part, standing on z = 0 at the origin. kind "round": a trunk
    with a flared foot and a crown of several lumpy overlapping masses (a broadleaf tree); "conifer":
    a trunk and stacked, slightly irregular cones. height: total height. seed: change it for a
    different tree of the same kind; give every tree in a scene its own seed and vary the heights.
    About 1,500 triangles. Slots: "trunk", "leaves"."""
    rng = random.Random(seed)
    bm = bmesh.new()
    if kind == "conifer":
        trunk_h, r = height * 0.22, height * 0.035
        _add_revolved(bm, [(0, 0), (r * 1.5, 0), (r * 1.05, trunk_h * 0.35), (r * 0.8, trunk_h * 1.3), (0, trunk_h * 1.3)],
                      8, 0)
        tiers = 4
        step = (height - trunk_h) / tiers
        base_r = height * 0.24
        for t in range(tiers):
            f = t / tiers
            z = trunk_h + t * step
            tier_h = step if t == tiers - 1 else step * 1.55
            r0 = base_r * (1 - f * 0.72) * rng.uniform(0.92, 1.08)
            r1 = 0.0 if t == tiers - 1 else r0 * 0.28
            lean = 0.0 if t == tiers - 1 else rng.uniform(-4, 4)
            tilt = Matrix.Rotation(math.radians(lean), 4, "X") @ Matrix.Rotation(rng.uniform(0, math.pi), 4, "Z")
            profile = [(0, 0), (r0, 0), (r0 * 0.96, tier_h * 0.06), (r1, tier_h), (0, tier_h)]
            verts = _add_revolved(bm, profile, 9, 1, matrix=tilt, offset=(0, 0, z))
            for v in verts:
                if (v.co.x ** 2 + v.co.y ** 2) > (r0 * 0.5) ** 2:
                    v.co.z += rng.uniform(-0.008, 0.008) * height
        return _new_object(bm, name, ["trunk", "leaves"], no_bevel=True)
    if kind != "round":
        bm.free()
        raise ValueError("tree kind must be round or conifer")
    trunk_h, r = height * 0.45, height * 0.032
    crown_r = height * 0.27
    centre_z = height - crown_r * 1.05
    blobs = [(0.0, 0.0, centre_z, crown_r)]
    count = 5
    for i in range(count):
        a = 2 * math.pi * (i + rng.uniform(-0.25, 0.25)) / count
        dist = crown_r * rng.uniform(0.55, 0.8)
        br = crown_r * rng.uniform(0.55, 0.75)
        blobs.append((dist * math.cos(a), dist * math.sin(a), centre_z + crown_r * rng.uniform(-0.45, 0.25), br))
    blobs.append((crown_r * rng.uniform(-0.2, 0.2), crown_r * rng.uniform(-0.2, 0.2), centre_z + crown_r * 0.55,
                  crown_r * 0.6))
    top = 0.0
    made = []
    for x, y, z, br in blobs:
        coords, faces = _icosphere()
        verts = []
        for c in coords:
            k = br * (1.0 + rng.uniform(-0.12, 0.12))
            verts.append(bm.verts.new((x + c[0] * k, y + c[1] * k, z + c[2] * k * 0.82)))
        for f in faces:
            bm.faces.new([verts[i] for i in f]).material_index = 1
        made += verts
        top = max(top, max(v.co.z for v in verts))
    scale = (height - trunk_h * 0.8) / (top - trunk_h * 0.8)  # the crown's top lands exactly on `height`
    for v in made:
        v.co.z = trunk_h * 0.8 + (v.co.z - trunk_h * 0.8) * scale
    reach = trunk_h * 0.8 + (centre_z - crown_r * 0.25 - trunk_h * 0.8) * scale  # the trunk runs up into the crown
    _add_revolved(bm, [(0, 0), (r * 1.8, 0), (r * 1.2, trunk_h * 0.12), (r, trunk_h * 0.4), (r * 0.7, reach),
                       (0, reach)], 8, 0)
    return _new_object(bm, name, ["trunk", "leaves"], no_bevel=True)


def bench(length=1.5, name="bench"):
    """A park bench as one part: two metal side frames, wooden seat slats and a slatted backrest. It
    stands on z = 0, centred on the origin, facing -Y (you sit looking toward -Y). length: its width
    along X. About 0.6 m deep and 0.85 m high, seat at 0.45 m. Slots: "wood" (slats), "metal"
    (frames)."""
    bm = bmesh.new()
    seat_z, depth, back_len = 0.45, 0.46, 0.86
    leg = 0.05
    angle = math.radians(10)
    lean = Matrix.Rotation(-angle, 4, "X")  # the back legs and the backrest lean back (toward +Y)
    back_c = (back_len / 2) * math.cos(angle) + (leg / 2) * math.sin(angle)  # so the leaned leg's foot is on z = 0
    back_y = depth / 2 - leg / 2

    def leg_y(z):  # the middle of a back leg at height z
        return back_y + (z - back_c) * math.tan(angle)

    for sx in (-1, 1):
        x = sx * (length / 2 - 0.12)
        _add_box(bm, (leg, leg, seat_z - 0.02), (x, -depth / 2 + leg / 2, (seat_z - 0.02) / 2), 1)
        _add_box(bm, (leg, leg, back_len), (x, back_y, back_c), 1, matrix=lean)
        _add_box(bm, (leg * 0.8, depth - 0.012, 0.04), (x, 0.0, seat_z - 0.04), 1)
        _add_box(bm, (leg * 0.8, depth - leg, 0.03), (x, 0.0, 0.16), 1)
    slat_w, slat_t = 0.085, 0.03
    count = 4
    first, last = -depth / 2 + slat_w / 2 - 0.01, leg_y(seat_z) - leg / 2 - slat_w / 2 - 0.01
    for i in range(count):
        y = first + (last - first) * i / (count - 1)
        _add_box(bm, (length, slat_w, slat_t), (0, y, seat_z - 0.022 + slat_t / 2), 0)
    for z in (0.62, 0.76):
        y = leg_y(z) - (leg / 2 + slat_t / 2 - 0.004) / math.cos(angle)
        _add_box(bm, (length, slat_t, slat_w), (0, y, z), 0, matrix=lean)
    return _new_object(bm, name, ["wood", "metal"])


def fence(points, height=1.0, post_spacing=2.0, rails=2, post=0.08, name="fence"):
    """A post-and-rail fence along a line on the ground, as one part. points: (x, y) corner points of
    its run in order; repeat the first point at the end for a closed enclosure, or leave a gap in the
    run for a gate. height: the posts' height. post_spacing: the most between posts (they are spread
    evenly along each stretch, with one at every corner). rails: how many horizontal rails. post: the
    posts' thickness. Built in place on z = 0. Slots: "post", "rail"."""
    pts = [(float(x), float(y)) for x, y in points]
    bm = bmesh.new()
    seen = set()
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        length = math.hypot(bx - ax, by - ay)
        if length < 1e-6:
            continue
        angle = math.atan2(by - ay, bx - ax)
        turn = Matrix.Rotation(angle, 4, "Z")
        n = max(1, math.ceil(length / post_spacing))
        for i in range(n + 1):
            x, y = ax + (bx - ax) * i / n, ay + (by - ay) * i / n
            key = (round(x, 4), round(y, 4))
            if key in seen:
                continue
            seen.add(key)
            _add_box(bm, (post, post, height), (x, y, height / 2), 0, matrix=turn)
        for r in range(rails):
            z = height * (0.88 - 0.5 * r / max(1, rails - 1)) if rails > 1 else height * 0.8
            _add_box(bm, (length, post * 0.45, post * 1.1), ((ax + bx) / 2, (ay + by) / 2, z), 1, matrix=turn)
    return _new_object(bm, name, ["post", "rail"], no_bevel=True)


def lamp_post(height=4.0, name="lamp_post"):
    """A park or street lamp as one part: a flared base, a slim tapered pole, a collar, a lantern and
    a cap. It stands on z = 0 at the origin. height: total height. Slots: "metal" (base, pole,
    collar, cap), "lamp" (the lantern's glass; give it a glowing material)."""
    bm = bmesh.new()
    r = max(0.04, height * 0.014)
    head = height * 0.11
    pole_top = height - head
    _add_revolved(bm, [(0, 0), (r * 2.6, 0), (r * 2.6, height * 0.012), (r * 1.7, height * 0.05), (r * 1.25, height * 0.12),
                       (r, height * 0.14), (r * 0.7, pole_top), (0, pole_top)], 12, 0)
    _add_revolved(bm, [(0, 0), (r * 1.5, 0), (r * 2.6, head * 0.14), (r * 2.6, head * 0.2), (0, head * 0.2)], 12, 0,
                  offset=(0, 0, pole_top - head * 0.06))
    _add_revolved(bm, [(0, 0), (r * 2.2, 0), (r * 3.3, head * 0.62), (0, head * 0.62)], 10, 1,
                  offset=(0, 0, pole_top + head * 0.12))
    _add_revolved(bm, [(0, 0), (r * 3.8, 0), (r * 3.6, head * 0.05), (r * 0.8, head * 0.2), (r * 0.5, head * 0.28),
                       (0, head * 0.28)], 10, 0, offset=(0, 0, pole_top + head * 0.72))
    return _new_object(bm, name, ["metal", "lamp"], no_bevel=True)


# --- mechanical parts: gears and shafts ------------------------------------------------------
#
# Every number is the real one in metres (module 2 mm = 0.002) and degrees. A gear's axis is its
# local Z through the origin, tooth 0 is centred on local +X, and it is one closed solid. The tooth
# maths is modelgen\tools\mechanism.py (loaded here), the same the layout and the checks use.
# Slots: "tooth_face" (the teeth), "hub" (the body's faces and the hub), "bore" (the bore and its
# keyway); a shaft has "shaft". Each part carries its numbers as JSON in obj["mg_gear"], which the
# motion helpers and the mesh check read.

GEAR_KEY = "mg_gear"
_MECH = []


def _mech():
    """modelgen\\tools\\mechanism.py, loaded once by its path (it uses nothing but math)."""
    if not _MECH:
        import importlib.util
        import os
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "mechanism.py")
        spec = importlib.util.spec_from_file_location("mg_mechanism_maths", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _MECH.append(module)
    return _MECH[0]


def _tag(obj, **info):
    import json
    obj[GEAR_KEY] = json.dumps({k: (round(v, 7) if isinstance(v, float) else v) for k, v in info.items()})
    return obj


def _unwrapped_angles(pts):
    out, last = [], None
    for x, y in pts:
        a = math.atan2(y, x)
        if last is not None:
            while a < last - 1e-9:
                a += 2 * math.pi
        out.append(a)
        last = a
    return out


def _keyway_radius(bore_r, width, depth, centre=math.pi / 2):
    """r(angle) of a bore with a rectangular key slot centred on `centre`, and its four corner angles."""
    w = width / 2
    top = bore_r + depth
    c_top = math.atan2(w, top)
    c_low = math.asin(min(0.999, w / bore_r))

    def radius(a):
        d = (a - centre + math.pi) % (2 * math.pi) - math.pi
        if abs(d) <= c_top:
            return top / math.cos(d)
        if abs(d) <= c_low:
            return w / math.sin(abs(d))
        return bore_r

    return radius, [centre - c_low, centre - c_top, centre + c_top, centre + c_low]


def _snap(angles, targets):
    """Move the nearest angle onto each target (the corners of a key slot), keeping the order."""
    out = list(angles)
    n = len(out)
    for t in targets:
        best = min(range(n), key=lambda i: abs((out[i] - t + math.pi) % (2 * math.pi) - math.pi))
        lo = out[best - 1] if best > 0 else -math.inf
        hi = out[best + 1] if best < n - 1 else math.inf
        cand = out[best] + ((t - out[best] + math.pi) % (2 * math.pi) - math.pi)
        if lo < cand < hi:
            out[best] = cand
    return out


def _outline_ring(pts, z, inset=0.0, twist=0.0):
    c, s = math.cos(twist), math.sin(twist)
    ring = []
    for x, y in pts:
        f = 1 - inset / max(math.hypot(x, y), 1e-9)
        px, py = x * f, y * f
        ring.append((px * c - py * s, px * s + py * c, z))
    return ring


def _circle_ring(angles, z, radius):
    out = []
    for a in angles:
        r = radius(a) if callable(radius) else radius
        out.append((r * math.cos(a), r * math.sin(a), z))
    return out


def _same_ring(a, b):
    return len(a) == len(b) and max(abs(p[k] - q[k]) for p, q in zip(a, b) for k in range(3)) < 1e-9


def _zip(bm, a, b, slot, pa, pb):
    """Close the band between two rings of different point counts with triangles, walking round both
    by angle: every triangle joins neighbours in angle, so none folds over another (a straight flank
    has several points at one angle; pairing points by index would cross there). pa and pb are the
    rings' points in full precision (a vertex keeps single precision, which can turn a straight
    flank's equal angles into a step backwards)."""
    aa = _unwrapped_angles([(p[0], p[1]) for p in pa])
    raw = [math.atan2(p[1], p[0]) for p in pb]
    n, m = len(a), len(b)
    start = aa[0]
    tau = 2 * math.pi
    below = [start - ((start - x) % tau) for x in raw]  # each b's angle at or just below a's start
    j0 = max(range(m), key=lambda j: below[j])  # the b nearest a's start without passing it
    walk = [below[j0]]
    for k in range(1, m + 1):
        step = (raw[(j0 + k) % m] - raw[(j0 + k - 1) % m] + math.pi) % tau - math.pi
        walk.append(walk[-1] + max(0.0, step))  # points at one angle (a straight flank) step by 0, not a turn

    def ang_a(k):
        return aa[k % n] + tau * (k // n)

    def ang_b(k):
        return walk[min(k, m)]

    steps = []  # ("a", i): triangle a_i, a_i+1, b_j; ("b", i, j): triangle a_i, b_j+1, b_j
    i = j = 0
    while i < n or j < m:
        if j >= m or (i < n and ang_a(i + 1) <= ang_b(j + 1)):
            steps.append(("a", i, j))
            i += 1
        else:
            steps.append(("b", i, j))
            j += 1
    flat = max(abs(p[2] - pa[0][2]) for p in list(pa) + list(pb)) < 1e-9
    if not flat:
        for kind, i, j in steps:
            tri = ((a[i % n], a[(i + 1) % n], b[(j0 + j) % m]) if kind == "a" else
                   (a[i % n], b[(j0 + j + 1) % m], b[(j0 + j) % m]))
            bm.faces.new(tri).material_index = slot
        return
    # a flat face: each fan of triangles round one b point is one flat polygon, so no sliver triangle
    # (three points on one radius) is left for the outline to draw as an edge
    first = next(k for k, s in enumerate(steps) if s[0] == "b")
    order = steps[first + 1:] + steps[:first + 1]
    poly = None
    for kind, i, j in order:
        if poly is None:
            poly = [b[(j0 + j) % m], a[i % n]]
        if kind == "a":
            poly.append(a[(i + 1) % n])
        else:
            poly.append(b[(j0 + j + 1) % m])
            bm.faces.new(poly).material_index = slot
            poly = None


def _lathe(rings, slots, name, slot_names):
    """One closed solid from rings round the Z axis: each ring is bridged to the next and the last to
    the first, with quads between rings of the same point count and triangles walked by angle
    between rings of different counts (a toothed outline and a plain circle). slots[i] is the slot
    index of the band from ring i to ring i + 1."""
    bm = bmesh.new()
    keep, keep_slots = [], []
    for ring, slot in zip(rings, slots):  # drop a ring that repeats the one before (a zero chamfer)
        if keep and _same_ring(ring, keep[-1]):
            keep_slots[-1] = slot
            continue
        keep.append(ring)
        keep_slots.append(slot)
    verts = [[bm.verts.new(p) for p in ring] for ring in keep]
    for j in range(len(verts)):
        a, b = verts[j], verts[(j + 1) % len(verts)]
        if len(a) != len(b):
            _zip(bm, a, b, keep_slots[j], keep[j], keep[(j + 1) % len(keep)])
            continue
        n = len(a)
        for i in range(n):
            k = (i + 1) % n
            f = bm.faces.new((a[i], a[k], b[k], b[i]))
            f.material_index = keep_slots[j]
    return _new_object(bm, name, slot_names, no_bevel=True)


def _circle_angles(outline_count, start=0.0, least=48, most=160):
    """Evenly spaced angles for the plain circles of a gear (rim, hub, bore)."""
    m = max(least, min(most, outline_count // 3))
    return [start + 2 * math.pi * i / m for i in range(m)]


def _detail(detail, teeth=24):
    """Points per flank, tip and root: fewer on a gear with many (small-looking) teeth."""
    d = max(0.5, float(detail)) * math.sqrt(24.0 / max(int(teeth), 6))
    return {"flank": max(3, min(7, round(5 * d))), "tip": 2, "root": max(2, min(4, round(3 * d)))}


def _extrude_outline(pts, z0, z1):
    """A closed prism of a 2D outline (a hole or a spoke window cutter)."""
    bm = bmesh.new()
    low = [bm.verts.new((x, y, z0)) for x, y in pts]
    high = [bm.verts.new((x, y, z1)) for x, y in pts]
    n = len(pts)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((low[i], low[j], high[j], high[i]))
    bm.faces.new(list(reversed(low)))
    bm.faces.new(high)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    me = bpy.data.meshes.new("cutter")
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new("cutter", me)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _web_cutters(r_in, r_out, depth, holes=0, spokes=0, phase=0.0):
    """Cutters for lightening holes or the windows between spokes, through the web."""
    cutters = []
    if holes:
        mid, size = (r_in + r_out) / 2, (r_out - r_in)
        dia = min(0.62 * size, 0.8 * 2 * math.pi * mid / holes - 0.0005)
        for i in range(holes):
            a = phase + 2 * math.pi * i / holes
            cutters.append(_extrude_outline(h.circle_points(dia / 2, 24, center=(mid * math.cos(a), mid * math.sin(a))),
                                            -depth, depth))
    if spokes:
        margin = 0.08 * (r_out - r_in)
        lo, hi = r_in + margin, r_out - margin
        spoke = max(0.25 * (hi - lo), 0.18 * 2 * math.pi * lo / spokes)  # spoke width at the hub
        for i in range(spokes):
            a0 = phase + 2 * math.pi * i / spokes + math.pi / spokes
            half_lo = math.pi / spokes - (spoke / 2) / lo
            half_hi = math.pi / spokes - (spoke / 2) / hi
            if half_lo <= 0.02:
                continue
            outer = [(hi * math.cos(a0 + t), hi * math.sin(a0 + t)) for t in (half_hi * (k / 4 - 1) for k in range(9))]
            inner = [(lo * math.cos(a0 + t), lo * math.sin(a0 + t)) for t in (half_lo * (1 - k / 3) for k in range(7))]
            cutters.append(_extrude_outline(outer + inner, -depth, depth))
    return cutters


def _gear_body(pts, width, bore, chamfer, hub, web, holes, spokes, keyway, twist_of, layers, name, rim_r):
    """The solid of an external gear from its outline: teeth (chamfered at the tips), the body or a
    recessed web between rim and hub, the hub and the bore with its key slot."""
    w2 = width / 2
    c = min(chamfer or 0.0, 0.3 * width)
    TOOTH, HUB, BORE = 0, 1, 2
    rings = [_outline_ring(pts, -w2, c, twist_of(-w2))]
    slots = [TOOTH]
    for i in range(layers + 1):  # the teeth, bottom to top (more rings along a helix)
        z = -w2 + c + (width - 2 * c) * i / layers
        rings.append(_outline_ring(pts, z, 0.0, twist_of(z)))
        slots.append(TOOTH)
    rings.append(_outline_ring(pts, w2, c, twist_of(w2)))
    slots.append(HUB)
    angles = _circle_angles(len(pts))
    bore_r = bore / 2
    radius, angles_bore = bore_r, angles
    if keyway and bore_r > 0:
        kw = keyway if isinstance(keyway, (list, tuple)) else (bore * 0.25, bore * 0.12)
        radius, corners = _keyway_radius(bore_r, kw[0], kw[1])
        angles_bore = _snap(angles, corners)
    hub_r = hub[0] / 2 if hub else 0.0
    hub_h = max(w2, hub[1] / 2) if hub else w2
    recess = web is not None and web < width - 1e-9
    if recess:
        hub_r = hub_r or max(bore_r + 0.35 * (rim_r - bore_r), bore_r * 1.6)
    if holes or spokes:
        # rim, web and hub as three clean solids merged into one: drilling the full-detail body would
        # slice every one of its faces
        rings += [_circle_ring(angles, w2, rim_r), _circle_ring(angles, -w2, rim_r)]
        slots += [HUB, HUB]
        rim = _lathe(rings, slots, name, ["tooth_face", "hub", "bore"])
        seg = 64
        plain = [2 * math.pi * i / seg for i in range(seg)]
        if keyway and bore_r > 0:
            plain = _snap(plain, corners) if len(plain) > 8 else plain
        hub_rings = [_circle_ring(plain, -hub_h, hub_r), _circle_ring(plain, hub_h, hub_r),
                     _circle_ring(plain, hub_h, radius), _circle_ring(plain, -hub_h, radius)]
        boss = _lathe(hub_rings, [HUB, HUB, BORE, HUB], name + "_hub", ["tooth_face", "hub", "bore"])
        t = (web if recess else width) / 2
        reach = 0.02 * (rim_r - hub_r)
        disc = _lathe([_circle_ring(plain, -t, hub_r - reach), _circle_ring(plain, -t, rim_r + reach),
                       _circle_ring(plain, t, rim_r + reach), _circle_ring(plain, t, hub_r - reach)],
                      [HUB, HUB, HUB, HUB], name + "_web", ["tooth_face", "hub", "bore"])
        inner = hub_r + 0.04 * (rim_r - hub_r)
        cutters = _web_cutters(inner, rim_r, width, holes, spokes, phase=math.pi / max(holes or spokes, 1))
        disc = _cut(disc, cutters, slot="hub")
        h.boolean(rim, [disc, boss], "UNION")
        rim.name = name
        h.merge_by_distance(rim, 1e-7)
        return rim
    top = []  # (radius, z) steps from the rim inwards on the top face, before the bore
    if recess:
        top = [(rim_r, w2), (rim_r, web / 2), (hub_r, web / 2), (hub_r, hub_h)]
    elif hub:
        top = [(hub_r, w2), (hub_r, hub_h)]
    for r, z in top:
        rings.append(_circle_ring(angles, z, r))
        slots.append(HUB)
    rings.append(_circle_ring(angles_bore, hub_h, radius))
    slots.append(BORE)
    rings.append(_circle_ring(angles_bore, -hub_h, radius))
    slots.append(HUB)
    for r, z in reversed(top):
        rings.append(_circle_ring(angles, -z, r))
        slots.append(HUB)
    return _lathe(rings, slots, name, ["tooth_face", "hub", "bore"])


def _gear_period(teeth, keyway, holes, spokes):
    """After how much of a turn the gear looks the same again: [1, n] is 1/n of a turn."""
    if keyway:
        return [1, 1]
    marks = holes or spokes
    return [1, math.gcd(int(teeth), int(marks)) if marks else int(teeth)]


def spur_gear(module, teeth, width, bore=0.0, pressure_angle=20.0, shift=0.0, shortening=0.0, backlash=None,
              chamfer=None, hub=None, web=None, holes=0, spokes=0, keyway=False, detail=1.0, name="spur_gear"):
    """A spur gear with true involute teeth: module (tooth size; pitch diameter = module x teeth),
    teeth, width (face width) and bore, all in metres. shift: the profile shift coefficient (a
    pinion under 17 teeth needs one; gear_pair_layout gives it); shortening: the tip shortening a
    pair with shifts needs (gear_pair_layout). backlash: the gap between meshing teeth (default 5
    percent of the module); chamfer: on the tooth tips (default 10 percent). hub: (diameter, length)
    of a boss round the bore; web: a thinner web between rim and hub (its thickness), with `holes`
    lightening holes or `spokes` spokes in it; keyway: True (or (width, depth)) for a key slot.
    Centred on the origin, axis Z, tooth 0 on +X. Slots: "tooth_face", "hub", "bore"."""
    mc = _mech()
    bl = 0.05 * module if backlash is None else backlash
    pts = mc.outline(module, int(teeth), pressure_angle, 0.0, shift, bl, False, shortening, **_detail(detail, teeth))
    circ = mc.circles(module, int(teeth), pressure_angle, 0.0, shift, False, shortening)
    if (holes or spokes) and web is None:
        web = 0.45 * width
    obj = _gear_body(pts, width, bore, module * 0.1 if chamfer is None else chamfer, hub, web, holes, spokes,
                     keyway, lambda z: 0.0, 1, name, circ["root"] - 1.6 * module)
    return _tag(obj, type="spur_gear", module=module, teeth=int(teeth), pitch_radius=circ["pitch"],
                tip_radius=circ["tip"], root_radius=circ["root"], base_radius=circ["base"], width=width,
                shift=shift, pressure_angle=pressure_angle, period=_gear_period(teeth, keyway, holes, spokes))


def helical_gear(module, teeth, width, bore=0.0, pressure_angle=20.0, helix_angle=20.0, hand="right",
                 herringbone=False, shift=0.0, shortening=0.0, backlash=None, chamfer=None, hub=None, web=None,
                 holes=0, spokes=0, keyway=False, detail=1.0, name="helical_gear"):
    """A helical gear: as spur_gear, with the teeth running along a helix at helix_angle to the axis
    (module is the normal module; the pitch diameter is module x teeth / cos(helix_angle)). hand:
    "right" or "left"; meshing gears on parallel shafts have opposite hands. herringbone: two
    opposite helices meeting in the middle (no end thrust). Slots: "tooth_face", "hub", "bore"."""
    mc = _mech()
    bl = 0.05 * module if backlash is None else backlash
    pts = mc.outline(module, int(teeth), pressure_angle, helix_angle, shift, bl, False, shortening,
                     **_detail(detail, teeth))
    circ = mc.circles(module, int(teeth), pressure_angle, helix_angle, shift, False, shortening)
    rate = math.tan(math.radians(helix_angle)) / circ["pitch"] * (1 if hand == "right" else -1)
    layers = max(3, min(12, math.ceil(abs(rate) * width / (2 * math.pi / int(teeth) / 3))))
    if herringbone and layers % 2:
        layers += 1
    if (holes or spokes) and web is None:
        web = 0.45 * width
    obj = _gear_body(pts, width, bore, module * 0.1 if chamfer is None else chamfer, hub, web, holes, spokes,
                     keyway, lambda z: rate * (abs(z) if herringbone else z), layers, name,
                     circ["root"] - 1.6 * module)
    return _tag(obj, type="helical_gear", module=module, teeth=int(teeth), pitch_radius=circ["pitch"],
                tip_radius=circ["tip"], root_radius=circ["root"], base_radius=circ["base"], width=width,
                helix_angle=helix_angle, hand=hand, herringbone=bool(herringbone), shift=shift,
                pressure_angle=pressure_angle, period=_gear_period(teeth, keyway, holes, spokes))


def ring_gear(module, teeth, width, rim_diameter=None, pressure_angle=20.0, backlash=None, chamfer=None,
              detail=1.0, name="ring_gear"):
    """An internal (ring) gear: involute teeth on the inside of a ring, for a spur gear of the same
    module to mesh inside it (at least 10 teeth fewer). rim_diameter: the outside (default the root
    diameter plus 6 modules). Centred on the origin, axis Z, a tooth on +X.
    Slots: "tooth_face", "hub"."""
    mc = _mech()
    bl = 0.05 * module if backlash is None else backlash
    pts = mc.outline(module, int(teeth), pressure_angle, 0.0, 0.0, bl, True, 0.0, **_detail(detail, teeth))
    circ = mc.circles(module, int(teeth), pressure_angle, 0.0, 0.0, True)
    outer = (rim_diameter or 2 * circ["root"] + 6 * module) / 2
    w2 = width / 2
    c = min(module * 0.1 if chamfer is None else chamfer, 0.3 * width)
    angles = _circle_angles(len(pts))
    rings = [_circle_ring(angles, -w2, outer), _circle_ring(angles, w2, outer),
             _outline_ring(pts, w2, -c), _outline_ring(pts, w2 - c), _outline_ring(pts, -w2 + c),
             _outline_ring(pts, -w2, -c)]
    obj = _lathe(rings, [1, 1, 0, 0, 0, 1], name, ["tooth_face", "hub"])
    return _tag(obj, type="ring_gear", module=module, teeth=int(teeth), pitch_radius=circ["pitch"],
                tip_radius=circ["tip"], root_radius=circ["root"], base_radius=circ["base"], width=width,
                outer_radius=outer, pressure_angle=pressure_angle, internal=True, period=[1, int(teeth)])


def rack(module, teeth, width, height=None, pressure_angle=20.0, backlash=None, name="rack"):
    """A straight rack: teeth along X pointing up (+Z) with straight flanks at the pressure angle,
    the pitch line on z = 0, `width` across Y, a bar `height` below the tooth roots (default 2.5
    modules). Teeth are centred on x = (k - (teeth - 1) / 2) x pitch (pitch = pi x module).
    Slots: "tooth_face", "hub"."""
    mc = _mech()
    bl = 0.05 * module if backlash is None else backlash
    top, bottom = mc.rack_profile(module, int(teeth), pressure_angle, bl, height)
    poly = top + [(top[-1][0], bottom), (top[0][0], bottom)]
    bm = bmesh.new()
    w2 = width / 2
    front = [bm.verts.new((x, -w2, z)) for x, z in poly]
    back = [bm.verts.new((x, w2, z)) for x, z in poly]
    n = len(poly)
    for i in range(n):
        j = (i + 1) % n
        f = bm.faces.new((front[i], front[j], back[j], back[i]))
        f.material_index = 0 if i < len(top) - 1 else 1
    for cap in (front, list(reversed(back))):
        f = bm.faces.new(cap)
        f.material_index = 1
    obj = _new_object(bm, name, ["tooth_face", "hub"], no_bevel=True)
    pitch = math.pi * module
    return _tag(obj, type="rack", module=module, teeth=int(teeth), pitch=pitch, width=width,
                length=teeth * pitch, pressure_angle=pressure_angle, period=[1, 1])


def bevel_gear(module, teeth, mate_teeth, width=None, bore=0.0, pressure_angle=20.0, hub=None, keyway=False,
               backlash=None, detail=1.0, name="bevel_gear"):
    """A straight bevel gear for a pair meshing at 90 degrees: its cone angle comes from the pair's
    tooth counts (mate_teeth: the other gear's). Teeth are cut on the back cone (Tredgold's
    approximation) and narrow towards the shared apex; width is the face width along the cone
    (default a third of the cone distance). The back face is at z = 0 with the pitch circle there,
    the apex on +Z; hub: (diameter, length) of a boss behind it. Slots: "tooth_face", "hub", "bore"."""
    mc = _mech()
    bl = 0.07 * module if backlash is None else backlash
    b = mc.bevel_numbers(module, int(teeth), int(mate_teeth), width)
    delta = math.radians(b["cone_deg"])
    rp, face, zv = b["pitch"], b["face"], b["virtual_teeth"]
    rpv = module * zv / 2
    alpha = math.radians(pressure_angle)
    d = _detail(detail, teeth)
    prof = mc.tooth_profile(module, rpv, rpv * math.cos(alpha), rpv + module, rpv - 1.25 * module,
                            math.pi / (2 * zv) - bl / (4 * rpv), alpha, d["flank"], d["tip"])

    def actual(rv, tv):  # a point of the virtual spur tooth on the back cone, seen down the axis
        # the back cone is square to the pitch cone: outward along it also moves towards the apex
        dr = rv - rpv
        r = rp + dr * math.cos(delta)
        return r, math.asin(max(-1.0, min(1.0, rv * math.sin(tv) / r))), dr * math.sin(delta)

    one = [actual(r, t) for r, t in prof]
    pitch = 2 * math.pi / int(teeth)
    pts = []
    for k in range(int(teeth)):
        base = k * pitch
        pts += [(r, base + t, z) for r, t, z in one]
        gap = pitch + one[0][1] - one[-1][1]
        pts += [(one[0][0], base + one[-1][1] + gap * j / d["root"], one[0][2]) for j in range(1, d["root"])]
    back = [(r * math.cos(a), r * math.sin(a), z) for r, a, z in pts]
    apex = rp / math.tan(delta)
    k = (b["cone_distance"] - face) / b["cone_distance"]
    front = [(x * k, y * k, apex + k * (z - apex)) for x, y, z in back]
    z_front = apex * (1 - k)
    angles = _circle_angles(len(back))
    bore_r = bore / 2
    radius, angles_bore = bore_r, angles
    if keyway and bore_r > 0:
        kw = keyway if isinstance(keyway, (list, tuple)) else (bore * 0.25, bore * 0.12)
        radius, corners = _keyway_radius(bore_r, kw[0], kw[1])
        angles_bore = _snap(angles, corners)
    r_front = max(bore_r + 0.25 * module, k * (rp - 1.25 * module * math.cos(delta)) - 0.5 * module)
    r_back = max(bore_r + 0.5 * module, rp - 1.25 * module * math.cos(delta) - 0.8 * module)

    def on_back_cone(r):  # the heel is the back cone, square to the pitch cone: further in, further back
        return (r - rp) * math.tan(delta)

    inner = hub[0] / 2 if hub else r_back
    back_z = min(on_back_cone(inner), -hub[1]) if hub else on_back_cone(r_back)
    rings = [back, front, _circle_ring(angles, z_front, r_front), _circle_ring(angles_bore, z_front, radius),
             _circle_ring(angles_bore, back_z, radius)]
    slots = [0, 1, 1, 2, 1]  # teeth, front face, front face to the bore, the bore, then the back
    if hub:
        rings += [_circle_ring(angles, back_z, inner), _circle_ring(angles, on_back_cone(inner), inner)]
        slots += [1, 1]
    else:
        rings.append(_circle_ring(angles, back_z, r_back))
        slots.append(1)
    obj = _lathe(rings, slots, name, ["tooth_face", "hub", "bore"])
    return _tag(obj, type="bevel_gear", module=module, teeth=int(teeth), mate_teeth=int(mate_teeth),
                pitch_radius=rp, tip_radius=rp + module * math.cos(delta), cone_deg=b["cone_deg"],
                cone_distance=b["cone_distance"], face=face, apex_z=apex, pressure_angle=pressure_angle,
                period=[1, 1 if keyway else int(teeth)])


def shaft(diameter, length, keyway=False, chamfer=None, steps=None, segments=32, name="shaft"):
    """A round shaft along Z, centred on the origin, chamfered at both ends. steps: [(length,
    diameter), ...] along it from -Z to +Z instead of one diameter (their lengths add up to length).
    keyway: True (or (width, depth, length)) for a key slot along it on +Y. Slot: "shaft"."""
    c = min(diameter * 0.1 if chamfer is None else chamfer, 0.2 * diameter)
    pieces = steps or [(length, diameter)]
    z = -length / 2
    prof = [(0.0, z)]
    for i, (seg, dia) in enumerate(pieces):
        rr = dia / 2
        prof += [(rr - c, z), (rr, z + c)] if i == 0 else [(rr, z)]
        z += seg
        prof.append((rr, z - c) if i == len(pieces) - 1 else (rr, z))
    prof += [(pieces[-1][1] / 2 - c, z), (0.0, z)]
    bm = bmesh.new()
    _add_revolved(bm, prof, segments, 0)
    obj = _new_object(bm, name, ["shaft"], no_bevel=True)
    if keyway:
        kw = keyway if isinstance(keyway, (list, tuple)) else (diameter * 0.25, diameter * 0.12, length * 0.5)
        cutter = h.box((kw[0], kw[1] * 2, kw[2]), location=(0, diameter / 2, 0))
        obj = _cut(obj, [cutter], slot="shaft")
        obj[h.NO_BEVEL_KEY] = 1
    return _tag(obj, type="shaft", radius=diameter / 2, length=length, period=[1, 1 if keyway else 8])


# --- turbocharger parts: radial wheels, volutes, bearings ---------------------------------------
#
# A wheel's axis is its local Z, its middle at the origin: the back plate at -length/2 and the nose
# (the inducer of a compressor, the exducer of a turbine) at +length/2. A volute is built round the
# wheel it houses in that same frame, so it is placed exactly where its wheel is.

def _wheel_curves(diameter, length, tip_ratio, exit_height, nose_ratio, back_thickness):
    """The hub line and the blade-tip (shroud) line of a radial wheel as functions of s (0 at the
    nose, 1 at the rim): each gives (radius, z)."""
    r2, zn = diameter / 2, length / 2
    zb = -length / 2 + back_thickness
    r1, rn = r2 * tip_ratio, r2 * nose_ratio

    def hub(s):
        a = s * math.pi / 2
        return rn + (0.97 * r2 - rn) * (1 - math.cos(a)), zb + (zn - zb) * (1 - math.sin(a))

    def tip(s):
        a = s * math.pi / 2
        return r1 + (r2 - r1) * (1 - math.cos(a)) ** 0.85, zb + exit_height + (zn - zb - exit_height) * (1 - math.sin(a))

    return hub, tip, zb


def _blade(bm, hub, tip, theta, s0, thick, ns=14, nt=5, sink=0.06):
    """One blade as a closed thin solid between the hub and tip lines, from s0 to the rim, turned
    by theta(s, t) round the axis."""
    sides = []
    for side in (-1, 1):
        grid = []
        for i in range(ns + 1):
            s = s0 + (1 - s0) * i / ns
            (rh, zh), (rt, zt) = hub(s), tip(s)
            row = []
            for j in range(nt + 1):
                t = -sink + (1 + sink) * j / nt
                r, z = rh + (rt - rh) * t, zh + (zt - zh) * t
                a = theta(s, max(t, 0.0)) + side * (thick / 2) / max(r, 1e-6)
                row.append(bm.verts.new((r * math.cos(a), r * math.sin(a), z)))
            grid.append(row)
        sides.append(grid)
    a, b = sides
    for i in range(ns):
        for j in range(nt):
            bm.faces.new((a[i][j], a[i + 1][j], a[i + 1][j + 1], a[i][j + 1]))
            bm.faces.new((b[i][j], b[i][j + 1], b[i + 1][j + 1], b[i + 1][j]))
    for i in range(ns):  # hub edge and tip edge
        bm.faces.new((a[i][0], b[i][0], b[i + 1][0], a[i + 1][0]))
        bm.faces.new((a[i][nt], a[i + 1][nt], b[i + 1][nt], b[i][nt]))
    for j in range(nt):  # leading and trailing edges
        bm.faces.new((a[0][j], a[0][j + 1], b[0][j + 1], b[0][j]))
        bm.faces.new((a[ns][j], b[ns][j], b[ns][j + 1], a[ns][j + 1]))


def _radial_wheel(name, diameter, length, blades, splitters, tip_ratio, exit_height, nose_ratio, wrap_deg,
                  sweep_deg, bore, thickness, kind):
    back_t = 0.07 * length
    hub, tip, zb = _wheel_curves(diameter, length, tip_ratio, exit_height, nose_ratio, back_t)
    r2 = diameter / 2
    seg = 72
    angles = [2 * math.pi * i / seg for i in range(seg)]
    prof = [hub(i / 16) for i in range(17)]  # nose to rim along the hub line
    rn, zn = prof[0]
    bore_r = max(bore / 2, 1e-4)
    rings = [_circle_ring(angles, z, r) for r, z in prof]
    rings += [_circle_ring(angles, -length / 2, 0.97 * r2), _circle_ring(angles, -length / 2, bore_r),
              _circle_ring(angles, zn, bore_r)]
    obj = _lathe(rings, [1] * 16 + [1, 1, 2, 1], name, ["tooth_face", "hub", "bore"])
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    wrap, sweep = math.radians(wrap_deg), math.radians(sweep_deg)
    pitch = 2 * math.pi / blades
    first = len(bm.faces)
    for k in range(blades):
        base = k * pitch
        _blade(bm, hub, tip, lambda s, t, b=base: b + wrap * (1 - s) ** 2 * (0.55 + 0.45 * t) - sweep * s ** 2,
               0.0, thickness)
        if splitters:
            _blade(bm, hub, tip, lambda s, t, b=base + pitch / 2: b + wrap * (1 - s) ** 2 * (0.55 + 0.45 * t)
                   - sweep * s ** 2, 0.38, thickness)
    bm.faces.ensure_lookup_table()
    for f in bm.faces[first:]:
        f.material_index = 0
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(obj.data)
    bm.free()
    return _tag(obj, type=kind, diameter=diameter, length=length, blades=int(blades), splitters=bool(splitters),
                tip_radius=r2, inducer_radius=r2 * tip_ratio, exit_height=exit_height, back_z=-length / 2,
                pitch_radius=r2, period=[1, int(blades)])


def compressor_wheel(diameter, length=None, blades=6, splitters=True, inducer_ratio=0.7, bore=None,
                     backsweep=30.0, thickness=None, name="compressor_wheel"):
    """A turbocharger's compressor wheel: a hub that flares from the nose to a back plate, with
    `blades` full blades (and as many splitter blades starting part way along when `splitters`)
    curved from the inducer at the nose to the rim, swept back by `backsweep` degrees at the exit.
    diameter: at the rim (exducer); inducer_ratio: the blade tips' diameter at the nose over it.
    Axis Z, the middle at the origin, the nose on +Z. Slots: "tooth_face" (blades), "hub", "bore"."""
    length = length or 0.42 * diameter
    return _radial_wheel(name, diameter, length, int(blades), splitters, inducer_ratio, 0.09 * diameter, 0.16, 75.0,
                         backsweep * 0.6, bore if bore is not None else 0.1 * diameter,
                         thickness or 0.012 * diameter, "compressor_wheel")


def turbine_wheel(diameter, length=None, blades=11, exducer_ratio=0.86, bore=None, thickness=None,
                  name="turbine_wheel"):
    """A turbocharger's radial turbine wheel: exhaust enters at the rim and leaves along the axis
    at the nose (the exducer, exducer_ratio of the rim diameter across), `blades` blades curved
    only near the nose. Axis Z, the middle at the origin, the nose on +Z (place it facing away from
    the compressor). Slots: "tooth_face" (blades), "hub", "bore"."""
    length = length or 0.38 * diameter
    return _radial_wheel(name, diameter, length, int(blades), False, exducer_ratio, 0.14 * diameter, 0.2, 45.0, 0.0,
                         bore if bore is not None else 0.1 * diameter, thickness or 0.014 * diameter, "turbine_wheel")


def _tube(bm, centres, frames, radii, k=20):
    """A closed thick tube along a path: centres, a (u, v) frame per centre for its cross-section,
    and (outer, inner) radii per centre. Ends closed by flat rings."""
    outer, inner = [], []
    for c, (u, v), (ro, ri) in zip(centres, frames, radii):
        outer.append([bm.verts.new(c + (u * math.cos(2 * math.pi * j / k) + v * math.sin(2 * math.pi * j / k)) * ro)
                      for j in range(k)])
        inner.append([bm.verts.new(c + (u * math.cos(2 * math.pi * j / k) + v * math.sin(2 * math.pi * j / k)) * ri)
                      for j in range(k)])
    for rows, flip in ((outer, False), (inner, True)):
        for i in range(len(rows) - 1):
            for j in range(k):
                q = (rows[i][j], rows[i][(j + 1) % k], rows[i + 1][(j + 1) % k], rows[i + 1][j])
                bm.faces.new(tuple(reversed(q)) if flip else q)
    for end in (0, len(outer) - 1):
        for j in range(k):
            bm.faces.new((outer[end][j], inner[end][j], inner[end][(j + 1) % k], outer[end][(j + 1) % k]))


def _c_tube(bm, centres, frames, radii, openings, k=24):
    """A thick tube whose section is a C open on its inner side (u = -1) by +-openings[i] radians:
    a volute's scroll, open to the diffuser passage the air flows through. Closed at both ends."""
    outer, inner = [], []
    for c, (u, v), (ro, ri), phi in zip(centres, frames, radii, openings):
        top = math.pi - phi
        alphas = [-top + 2 * top * j / k for j in range(k + 1)]
        outer.append([bm.verts.new(c + (u * math.cos(a) + v * math.sin(a)) * ro) for a in alphas])
        inner.append([bm.verts.new(c + (u * math.cos(a) + v * math.sin(a)) * ri) for a in alphas])
    n = len(outer)
    for i in range(n - 1):
        for j in range(k):
            bm.faces.new((outer[i][j], outer[i][j + 1], outer[i + 1][j + 1], outer[i + 1][j]))
            bm.faces.new((inner[i][j], inner[i + 1][j], inner[i + 1][j + 1], inner[i][j + 1]))
        for j in (0, k):  # the C's two lips
            bm.faces.new((outer[i][j], outer[i + 1][j], inner[i + 1][j], inner[i][j]))
    for end in (0, n - 1):
        for j in range(k):
            bm.faces.new((outer[end][j], inner[end][j], inner[end][j + 1], outer[end][j + 1]))


def volute(wheel_diameter, wheel_length, inlet_diameter, kind="compressor", wall=None, clearance=None,
           exit_height=None, outlet_length=None, flanges=True, name="volute"):
    """The snail housing round a radial wheel, in the wheel's own frame (place it where its wheel
    is): a shroud that follows the blade tips with a small clearance and opens into an axial pipe at
    the nose, a diffuser out to a scroll whose section grows all the way round, and a tangential
    pipe off its big end; a back plate behind the wheel; flanges on both pipes. For a compressor air
    comes in along the axis and leaves through the scroll; for a turbine ("turbine") exhaust comes in
    through the scroll and leaves along the axis. Slots: "hub" (casting), "tooth_face" (flanges).
    Its air paths (local points) are kept in obj["mg_gear"]["flows"]."""
    mc = _mech()  # noqa: F841  (kept for symmetry with the gear tools)
    r2, half = wheel_diameter / 2, wheel_length / 2
    turbine = kind == "turbine"
    tip_ratio = inlet_diameter / wheel_diameter
    b2 = exit_height or (0.14 if turbine else 0.09) * wheel_diameter
    t = wall or max(0.001, 0.035 * wheel_diameter)
    c = clearance or max(0.0003, 0.008 * wheel_diameter)
    back_t = 0.07 * wheel_length
    hub, tip, zb = _wheel_curves(wheel_diameter, wheel_length, tip_ratio, b2, 0.16, back_t)
    inlet_len = 0.35 * wheel_diameter
    rd = 1.3 * r2  # where the diffuser meets the scroll
    seg = 72
    angles = [2 * math.pi * i / seg for i in range(seg)]
    shroud = [tip(i / 12) for i in range(13)]
    r_in = shroud[0][0] + c
    z_dif = zb + b2 + c
    inner = [(r_in, half + inlet_len)] + [(r + c, z + c) for r, z in shroud] + [(rd, z_dif)]
    outer = [(rd, z_dif + t)] + [(r + c + t, z + c + t) for r, z in reversed(shroud)] + [(r_in + t, half + inlet_len)]
    fl = 0.18 * wheel_diameter if flanges else 0.0
    ft = 1.2 * t
    if flanges:
        outer = outer[:-1] + [(r_in + t, half + inlet_len - ft), (r_in + t + fl, half + inlet_len - ft),
                              (r_in + t + fl, half + inlet_len)]
    prof = inner + outer
    shroud_obj = _lathe([_circle_ring(angles, z, r) for r, z in prof], [1] * len(prof), name,
                        ["hub", "tooth_face"])
    # the back plate behind the wheel, with a hole for the shaft
    z0, z1 = -half - c - t, -half - c
    hole = 0.22 * r2
    plate = [(hole, z0), (rd + t, z0), (rd + t, z1), (hole, z1)]
    bm = bmesh.new()
    bm.from_mesh(shroud_obj.data)
    plate_rings = [[bm.verts.new(p) for p in _circle_ring(angles, z, r)] for r, z in plate]
    for j in range(4):
        a, b = plate_rings[j], plate_rings[(j + 1) % 4]
        for i in range(seg):
            bm.faces.new((a[i], a[(i + 1) % seg], b[(i + 1) % seg], b[i]))
    # the scroll: a tube round the diffuser whose section grows from the tongue to the big end
    rho0, rho1 = (0.14 if turbine else 0.11) * r2, (0.5 if turbine else 0.42) * r2
    zc = (z_dif + z1) / 2
    steps, start = 54, math.radians(25)
    centres, frames, radii, flow = [], [], [], []
    for i in range(steps + 1):
        th = start + (2 * math.pi - start) * i / steps
        rho = rho0 + (rho1 - rho0) * (th - start) / (2 * math.pi - start)
        rc = rd + rho - t
        radial = Vector((math.cos(th), math.sin(th), 0.0))
        centres.append(radial * rc + Vector((0, 0, zc)))
        frames.append((radial, Vector((0, 0, 1))))
        radii.append((rho, rho - t))
        flow.append(radial * rc + Vector((0, 0, zc)))
    # the scroll is open on its inner side to the diffuser passage between the front wall and the back
    # plate, as a real volute is: air leaves the wheel through that passage into the scroll
    half_gap = (z_dif - z1) / 2
    openings = [math.asin(min(half_gap, 0.92 * r[0]) / r[0]) for r in radii]
    _c_tube(bm, centres, frames, radii, openings)
    end_c = centres[-1]
    tangent = Vector((0.0, 1.0, 0.0))  # at the big end (angle 2 pi) the scroll runs along +Y
    out_len = outlet_length or 0.9 * wheel_diameter
    pipe = [end_c - tangent * (0.5 * t)]
    for i in range(1, 5):
        cpos = end_c + tangent * (out_len * i / 4)
        pipe.append(cpos)
        flow.append(cpos)
    _tube(bm, pipe, [(Vector((1.0, 0, 0)), Vector((0, 0, 1)))] * len(pipe), [(rho1, rho1 - t)] * len(pipe))
    centres, frames, radii = pipe, [(Vector((1.0, 0, 0)), Vector((0, 0, 1)))] * len(pipe), [(rho1, rho1 - t)] * len(pipe)
    if flanges:  # a square-ish flange ring on the outlet's end
        fc = centres[-1] - tangent * ft / 2
        ring_c = [fc - tangent * ft / 2, fc + tangent * ft / 2]
        _tube(bm, ring_c, [frames[-1]] * 2, [(rho1 + fl, rho1 - 0.5 * t)] * 2, k=24)  # inside the pipe's wall
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(shroud_obj.data)
    bm.free()
    beyond = 0.4 * wheel_diameter
    axis_path = [(0.0, 0.0, half + inlet_len + beyond), (0.0, 0.0, half + 0.1 * wheel_length)]
    scroll = [list(p) for p in flow] + [list(centres[-1] + tangent * beyond)]
    into_wheel = [[r2 * 0.9 * math.cos(start), r2 * 0.9 * math.sin(start), zc]]
    flows = ({"exhaust_in": [list(p) for p in reversed(scroll)] + into_wheel,
              "exhaust": [list(p) for p in reversed(axis_path)]} if turbine else
             {"intake": [list(p) for p in axis_path], "compressed": into_wheel + scroll})
    flows = {k: [[round(v, 6) for v in p] for p in pts] for k, pts in flows.items()}
    return _tag(shroud_obj, type="volute", kind=kind, wheel_diameter=wheel_diameter, clearance=c, wall=t,
                outer_radius=rd + rho1 * 2, flows=flows, period=[1, 1])


def bearing(bore, outer_diameter, width, balls=None, name="bearing"):
    """A deep-groove ball bearing: inner and outer race with ball grooves, the balls on the pitch
    circle and a cage between them. Axis Z, centred on the origin. Slots: "tooth_face" (races),
    "hub" (cage), "bore" (balls). The races look the same at any angle, so the whole bearing turns at
    the cage's speed (mg_gear "cage_ratio" of the shaft's) to show the balls running round."""
    ri, ro = bore / 2, outer_diameter / 2
    nums = _mech().bearing_numbers(bore, outer_diameter, balls)
    pitch, ball, count = nums["pitch"], nums["ball"], nums["count"]
    w2 = width / 2
    seg = 64
    angles = [2 * math.pi * i / seg for i in range(seg)]
    groove = ball * 1.04
    ins = [(ri, -w2), (pitch - ball * 0.55, -w2), (pitch - ball * 0.55, -ball * 0.75)]
    ins += [(pitch - groove * math.cos(a), groove * math.sin(a)) for a in
            (math.radians(d) for d in (-45, -25, 0, 25, 45))]
    ins += [(pitch - ball * 0.55, ball * 0.75), (pitch - ball * 0.55, w2), (ri, w2)]
    outs = [(ro, w2), (pitch + ball * 0.55, w2), (pitch + ball * 0.55, ball * 0.75)]
    outs += [(pitch + groove * math.cos(a), groove * math.sin(a)) for a in
             (math.radians(d) for d in (45, 25, 0, -25, -45))]
    outs += [(pitch + ball * 0.55, -ball * 0.75), (pitch + ball * 0.55, -w2), (ro, -w2)]
    races = [_lathe([_circle_ring(angles, z, r) for r, z in prof], [0] * len(prof), name + "_r" + str(i),
                    ["tooth_face", "hub", "bore"]) for i, prof in enumerate((ins, outs))]
    cage_h = ball * 0.5
    cage = _lathe([_circle_ring(angles, -cage_h, pitch - ball * 0.25), _circle_ring(angles, -cage_h, pitch + ball * 0.25),
                   _circle_ring(angles, cage_h, pitch + ball * 0.25), _circle_ring(angles, cage_h, pitch - ball * 0.25)],
                  [1, 1, 1, 1], name + "_cage", ["tooth_face", "hub", "bore"])
    pieces = races + [cage]
    for k in range(count):
        a = 2 * math.pi * k / count
        bm = bmesh.new()
        bmesh.ops.create_uvsphere(bm, u_segments=12, v_segments=8, radius=ball)
        bmesh.ops.translate(bm, vec=Vector((pitch * math.cos(a), pitch * math.sin(a), 0.0)), verts=bm.verts)
        for f in bm.faces:
            f.material_index = 2
        pieces.append(_new_object(bm, f"{name}_ball{k}", ["tooth_face", "hub", "bore"], no_bevel=True))
    obj = h.join(pieces, name)
    obj[h.NO_BEVEL_KEY] = 1
    return _tag(obj, type="bearing", bore=bore, outer_diameter=outer_diameter, width=width, balls=count,
                pitch_radius=pitch, tip_radius=ro, cage_ratio=0.5 * (1 - ball / pitch), period=[1, count])


def centre_housing(length, shaft_diameter, bore_diameter, outer_diameter, front_flange_diameter=None,
                   back_flange_diameter=None, end_wall=None, oil_ports=True, name="centre_housing"):
    """A turbocharger's centre (bearing) housing: a cast body round the shaft, bored to carry the
    bearings (bore_diameter), closed at each end by a wall with a seal bore a little over the shaft,
    and a flange at each end that meets the volute's back plate (front: the compressor side, +Z).
    oil_ports: an oil inlet boss on top (+X) and a wider drain below (-X), bored through to the
    bearings. Axis Z, centred on the origin. Slots: "hub" (casting), "tooth_face" (flanges, ends),
    "bore" (bores). The oil's path (local points) is kept in obj["mg_gear"]["flows"]."""
    l2 = length / 2
    rb, rr = outer_diameter / 2, bore_diameter / 2
    rf, rk = (front_flange_diameter or outer_diameter) / 2, (back_flange_diameter or outer_diameter) / 2
    rs = shaft_diameter / 2 + max(0.0003, 0.03 * shaft_diameter)
    f = end_wall or max(0.001, 0.08 * outer_diameter)
    ft = min(f, 0.25 * length)  # the flanges' thickness
    prof = [(rs, l2), (rf, l2), (rf, l2 - ft), (rb, l2 - ft), (rb, -l2 + ft), (rk, -l2 + ft), (rk, -l2), (rs, -l2),
            (rs, -l2 + f), (rr, -l2 + f), (rr, l2 - f), (rs, l2 - f)]
    slots = [1, 1, 1, 0, 1, 1, 1, 2, 0, 2, 0, 2]
    angles = [2 * math.pi * i / 64 for i in range(64)]
    obj = _lathe([_circle_ring(angles, z, r) for r, z in prof], slots, name, ["hub", "tooth_face", "bore"])
    flows = {}
    if oil_ports:
        space = l2 - ft
        bosses = [(1.0, min(0.2 * rb, 0.6 * space), 1.3 * rb), (-1.0, min(0.32 * rb, 0.75 * space), 1.22 * rb)]
        solids, holes = [], []
        start = (rr + rb) / 2  # inside the wall, clear of the bearings' bore
        for side, radius, reach in bosses:
            depth = reach - start
            boss = h.cylinder(radius, depth, segments=24, location=(side * (start + depth / 2), 0, 0),
                              rotation_deg=(0, 90, 0), name=name + "_boss")
            _same_slots(boss, obj, "hub")
            solids.append(boss)
            hole_len = reach - rr * 0.8 + 0.002
            holes.append(h.cylinder(radius * 0.45, hole_len, segments=16,
                                    location=(side * (rr * 0.8 + hole_len / 2), 0, 0), rotation_deg=(0, 90, 0),
                                    name=name + "_port"))
        h.union(obj, solids)
        obj = _cut(obj, holes, slot="bore")
        obj[h.NO_BEVEL_KEY] = 1
        top, low = 1.3 * rb + 0.5 * rb, -1.22 * rb - 0.5 * rb
        flows["oil"] = [[round(v, 6) for v in p] for p in
                        ((top, 0.0, 0.0), (rr, 0.0, 0.0), (0.0, rr, 0.0), (-rr, 0.0, 0.0), (low, 0.0, 0.0))]
    return _tag(obj, type="centre_housing", length=length, bore_radius=rr, outer_radius=rb, flows=flows,
                period=[1, 1])


# --- engine parts: crankshaft, connecting rod, piston and rings, cylinder ---------------------------
#
# A crankshaft's axis is its local Z through the main journals (the front, the snout, on +Z); a
# throw at angle 0 has its crankpin straight up (+Y), and a throw at angle a is turned a degrees
# counterclockwise about +Z. A connecting rod lies in its local XY plane: the big end's centre at the
# origin, the small end's at +Y (its length), its thickness along Z. A piston's axis is its local Z
# (the crown up), its origin the gudgeon pin's centre, the pin along X; its rings are built in the
# same frame, so they are placed where it is. A cylinder's axis is its local Z, its origin on the
# axis at the bottom.

ENGINE_SLOTS = ["tooth_face", "hub", "bore"]


def _hull(pts):
    """The convex hull of 2D points, counterclockwise (Andrew's monotone chain)."""
    pts = sorted(set((round(x, 9), round(y, 9)) for x, y in pts))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _circle2(cx, cy, r, n=40, a0=0.0):
    return [(cx + r * math.cos(a0 + 2 * math.pi * i / n), cy + r * math.sin(a0 + 2 * math.pi * i / n)) for i in range(n)]


def _slab(pts, z0, z1, slot, name):
    """A closed prism of a counterclockwise 2D outline from z0 to z1, its two caps single faces."""
    bm = bmesh.new()
    low = [bm.verts.new((x, y, z0)) for x, y in pts]
    high = [bm.verts.new((x, y, z1)) for x, y in pts]
    n = len(pts)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((low[i], low[j], high[j], high[i]))
    bm.faces.new(list(reversed(low)))
    bm.faces.new(high)
    for f in bm.faces:
        f.material_index = slot
    return _new_object(bm, name, ENGINE_SLOTS, no_bevel=True)


def _along(obj, axis):
    """Turn a part built along Z so that Z lies along "X" or "Y" (about its own origin)."""
    rot = {"X": Matrix.Rotation(math.pi / 2, 4, "Y"), "Y": Matrix.Rotation(-math.pi / 2, 4, "X")}[axis]
    obj.data.transform(rot)
    return obj


def _fuse(pieces, name, holes=(), hole_slot="bore"):
    """One closed solid: the pieces unioned, then the holes cut."""
    body = pieces[0]
    if len(pieces) > 1:
        h.union(body, pieces[1:])
    body = h.merge_by_distance(body, 1e-7)
    if holes:
        body = _cut(body, list(holes), slot=hole_slot)
    body.name = name
    body.data.name = name
    body[h.NO_BEVEL_KEY] = 1
    return body


def crankshaft(stroke, crankpin_diameter, journal_diameter, pin_width, web_thickness, journal_width,
               throw_angles=(0.0,), spacing=None, counterweights=True, counterweight_radius=None,
               web_radius=None, name="crankshaft"):
    """A crankshaft: main journals on its axis (local Z), and for each throw a crankpin at half the
    stroke from the axis between two webs, each web a pear shape round the pin and the journal
    with, when counterweights, a fan-shaped counterweight opposite the pin. throw_angles in degrees
    (0: the pin straight up, +Y); throws spacing apart along Z (default: pin, webs and a journal).
    A snout on the front (+Z) and a flywheel flange on the back. Slots: "tooth_face" (journals and
    crankpins, ground smooth), "hub" (webs), "bore" (the flange)."""
    r, rp, rj = stroke / 2, crankpin_diameter / 2, journal_diameter / 2
    angles = [float(a) for a in (throw_angles or (0.0,))]
    n = len(angles)
    spacing = spacing or (pin_width + 2 * web_thickness + journal_width)
    zs = [(i - (n - 1) / 2) * spacing for i in range(n)]
    web_r = web_radius or rp + 0.08 * stroke
    cw_r = counterweight_radius or 0.85 * stroke
    dip = 0.2 * web_thickness  # each pin and journal reaches this far into the webs beside it
    pieces = []
    for z, a in zip(zs, angles):
        t = math.radians(a)
        px, py = -r * math.sin(t), r * math.cos(t)
        pieces.append(_slab(_circle2(px, py, rp), z - pin_width / 2 - dip, z + pin_width / 2 + dip, 0, name + "_pin"))
        outline = _circle2(px, py, web_r, 36) + _circle2(0.0, 0.0, rj + 0.08 * stroke, 36)
        if counterweights:
            away = math.atan2(-py, -px)
            outline += [(cw_r * math.cos(away + 1.15 * (i / 12 - 1)), cw_r * math.sin(away + 1.15 * (i / 12 - 1)))
                        for i in range(25)]
        hull = _hull(outline)
        for side in (-1, 1):
            zc = z + side * (pin_width / 2 + web_thickness / 2)
            pieces.append(_slab(hull, zc - web_thickness / 2, zc + web_thickness / 2, 1, name + "_web"))
    first, last = zs[0] - pin_width / 2 - web_thickness, zs[-1] + pin_width / 2 + web_thickness
    spans = [(first - journal_width, first + dip)]
    for i in range(n - 1):
        spans.append((zs[i] + pin_width / 2 + web_thickness - dip, zs[i + 1] - pin_width / 2 - web_thickness + dip))
    spans.append((last - dip, last + journal_width))
    for z0, z1 in spans:
        if z1 > z0:
            pieces.append(_slab(_circle2(0.0, 0.0, rj, 48), z0, z1, 0, name + "_journal"))
    snout = 0.6 * journal_width
    pieces.append(_slab(_circle2(0.0, 0.0, 0.62 * rj, 40), last + journal_width - dip, last + journal_width + snout, 0,
                        name + "_snout"))
    flange_t = 0.3 * journal_width
    pieces.append(_slab(_circle2(0.0, 0.0, 1.35 * rj, 48), first - journal_width - flange_t,
                        first - journal_width + dip, 2, name + "_flange"))
    obj = _fuse(pieces, name)
    return _tag(obj, type="crankshaft", stroke=stroke, crank_radius=r, throws=n, throw_angles=angles, throw_z=zs,
                pin_radius=rp, journal_radius=rj, counterweight_radius=cw_r if counterweights else 0.0,
                web_radius=web_r, pitch_radius=r, period=[1, 1])


def connecting_rod(length, big_end_bore, small_end_bore, big_end_width, small_end_width=None, name="connecting_rod"):
    """A connecting rod: a big end round the crankpin (big_end_bore) with its cap's two bolt bosses,
    a small end round the gudgeon pin (small_end_bore), length apart centre to centre, joined by an
    I-section shank. Lies in its local XY plane: the big end at the origin, the small end at +Y.
    Slots: "hub" (the forging), "bore" (the two bores)."""
    rb, rs = big_end_bore / 2, small_end_bore / 2
    rbo, rso = rb * 1.32 + 0.0008, rs * 1.5 + 0.0008
    wb = big_end_width
    ws = small_end_width or 0.8 * wb
    t = 0.72 * min(wb, ws)
    y0, y1 = 0.55 * rbo, length - 0.55 * rso
    hb, hs = 0.46 * rbo, 0.62 * rso
    shank = [(-hb, y0), (hb, y0), (hs, y1), (-hs, y1)]
    pieces = [_slab(_circle2(0.0, 0.0, rbo, 48), -wb / 2, wb / 2, 1, name + "_big"),
              _slab(_circle2(0.0, length, rso, 40), -ws / 2, ws / 2, 1, name + "_small"),
              _slab(shank, -t / 2, t / 2, 1, name + "_shank")]
    br = min(0.2 * rbo, 0.45 * wb)
    for side in (-1, 1):  # the cap bolts' bosses, either side of the big end
        boss = _slab(_circle2(0.0, 0.0, br, 20), -0.9 * rbo, 0.5 * rbo, 1, name + "_boss")
        _along(boss, "Y")
        boss.data.transform(Matrix.Translation((side * (rb + br + 0.25 * (rbo - rb)), 0.0, 0.0)))
        pieces.append(boss)
    depth = 0.26 * t
    inset = [(-hb * 0.55, y0 + 0.6 * rbo), (hb * 0.55, y0 + 0.6 * rbo), (hs * 0.5, y1 - 0.5 * rso),
             (-hs * 0.5, y1 - 0.5 * rso)]
    holes = [_slab(_circle2(0.0, 0.0, rb, 48), -wb, wb, 2, name + "_bb"),
             _slab(_circle2(0.0, length, rs, 40), -wb, wb, 2, name + "_sb")]
    if inset[2][1] > inset[0][1] + 0.002:
        holes += [_slab(inset, t / 2 - depth, t, 1, name + "_p1"), _slab(inset, -t, -t / 2 + depth, 1, name + "_p2")]
    obj = _fuse(pieces, name, holes)
    return _tag(obj, type="connecting_rod", length=length, big_end_radius=rb, small_end_radius=rs, period=[1, 1])


def _piston_numbers(bore, compression_height, grooves):
    """Where a piston's ring grooves are: (top, bottom) z of each, from the pin's centre."""
    return _mech().piston_grooves(bore, compression_height, grooves)


def piston(bore, compression_height, pin_diameter, skirt_length, grooves=None, groove_depth=None,
           boss_gap=None, crown="flat", name="piston"):
    """A piston for a cylinder of `bore`: a crown (flat, dished or domed) compression_height above
    the gudgeon pin's centre, a ring belt with one groove per width in `grooves` (default two
    compression rings and an oil ring), a skirt reaching skirt_length below the pin, hollow inside
    with two pin bosses, and the gudgeon pin in its bore along X (boss_gap: the room between the
    bosses for the rod's small end). Slots: "tooth_face" (crown and ring belt), "hub" (skirt,
    inside), "bore" (pin and pin bore)."""
    R = bore / 2 - max(0.00002, 0.0005 * bore)
    land = R - max(0.00005, 0.0025 * bore)
    gd = groove_depth or 0.045 * bore
    grooves = list(grooves or (0.015 * bore, 0.018 * bore, 0.035 * bore))
    ch = compression_height
    slots = _piston_numbers(bore, ch, grooves)
    belt_low = slots[-1][1] - 0.02 * bore
    crown_t = 0.085 * bore
    r_in = land - gd - 0.02 * bore  # the inside, one radius from the crown's underside to the skirt's foot
    c = 0.01 * bore
    dish = {"dish": -0.04 * bore, "dome": 0.06 * bore}.get(crown, 0.0)
    prof = [(0.0, ch + dish)]
    if dish:
        prof.append((0.62 * land, ch))
    prof += [(land - c, ch), (land, ch - c)]
    for top, low in slots:
        prof += [(land, top), (land - gd, top), (land - gd, low), (land, low)]
    prof += [(land, belt_low), (R, belt_low - 0.01 * bore), (R, -skirt_length + c), (R - c, -skirt_length),
             (r_in, -skirt_length), (r_in, ch - crown_t), (0.0, ch - crown_t)]
    bm = bmesh.new()
    _add_revolved(bm, prof, 64, 0)
    for f in bm.faces:  # the skirt and the inside are the casting
        mid = f.calc_center_median()
        f.material_index = 1 if mid.z < belt_low - 0.001 or mid.xy.length < r_in + 0.001 * bore else 0
    body = _new_object(bm, name, ENGINE_SLOTS, no_bevel=True)
    rpin = pin_diameter / 2
    gap = boss_gap or 1.25 * pin_diameter
    boss_r = rpin + 0.05 * bore
    reach = r_in + 0.6 * (land - gd - r_in)  # the bosses' corners stay inside the groove roots
    x_out = math.sqrt(max(reach ** 2 - boss_r ** 2, (gap / 2 + 0.001) ** 2))
    pieces = [body]
    for side in (-1, 1):
        lo, hi = sorted((side * gap / 2, side * x_out))
        pieces.append(_slab([(lo, -boss_r), (hi, -boss_r), (hi, boss_r), (lo, boss_r)], -boss_r,
                            ch - crown_t + 0.003 * bore, 1, name + "_boss"))
    pin_hole = _slab(_circle2(0.0, 0.0, rpin + 0.00001, 40), -bore, bore, 2, name + "_pinbore")
    _along(pin_hole, "X")
    obj = _fuse(pieces, name, [pin_hole])
    pin_len = 2 * R - 0.06 * bore
    pin = _slab(_circle2(0.0, 0.0, rpin, 40), -pin_len / 2, pin_len / 2, 2, name + "_pin")
    bore_in = _slab(_circle2(0.0, 0.0, 0.55 * rpin, 32), -pin_len, pin_len, 2, name + "_pin_hole")
    pin = _cut(pin, [bore_in], slot="bore")
    _along(pin, "X")
    obj = h.join([obj, pin], name)
    obj[h.NO_BEVEL_KEY] = 1
    return _tag(obj, type="piston", bore=bore, radius=R, compression_height=ch, pin_radius=rpin,
                skirt_length=skirt_length, grooves=[[round(a, 7), round(b, 7)] for a, b in slots],
                groove_root=land - gd, boss_gap=gap, period=[1, 1])


def piston_rings(bore, compression_height, grooves=None, groove_depth=None, name="piston_rings"):
    """The rings in a piston's grooves (the same numbers as the piston): each a rectangular section
    reaching the cylinder wall, with its gap, the gaps a third of a turn apart. In the piston's
    frame. Slots: "tooth_face" (the rings)."""
    R = bore / 2
    gd = groove_depth or 0.045 * bore
    grooves = list(grooves or (0.015 * bore, 0.018 * bore, 0.035 * bore))
    land = R - max(0.00002, 0.0005 * bore) - max(0.00005, 0.0025 * bore)
    rings = []
    for k, (top, low) in enumerate(_piston_numbers(bore, compression_height, grooves)):
        r_in = land - gd + max(0.00008, 0.002 * bore)
        h_gap = max(0.00002, 0.0004 * bore)
        ring = _slab(_circle2(0.0, 0.0, R - 0.00001, 96), low + h_gap, top - h_gap, 0, f"{name}_{k}")
        hole = _slab(_circle2(0.0, 0.0, r_in, 96), low - 0.001, top + 0.001, 0, f"{name}_{k}_in")
        a = 2 * math.pi * k / 3 + math.pi / 2
        w = max(0.0002, 0.004 * bore)
        cx, cy = (R - gd / 2) * math.cos(a), (R - gd / 2) * math.sin(a)
        ux, uy = math.cos(a), math.sin(a)
        gap_pts = [(cx - ux * gd - uy * w, cy - uy * gd + ux * w), (cx - ux * gd + uy * w, cy - uy * gd - ux * w),
                   (cx + ux * gd + uy * w, cy + uy * gd - ux * w), (cx + ux * gd - uy * w, cy + uy * gd + ux * w)]
        cut = _slab(gap_pts, low - 0.001, top + 0.001, 0, f"{name}_{k}_gap")
        ring = _cut(ring, [hole], slot="tooth_face")  # one cutter at a time: the two overlap
        rings.append(_cut(ring, [cut], slot="tooth_face"))
    obj = h.join(rings, name)
    obj[h.NO_BEVEL_KEY] = 1
    return _tag(obj, type="piston_rings", bore=bore, rings=len(rings), period=[1, 1])


def cylinder(bore, length, wall=None, style="liner", name="cylinder"):
    """The cylinder a piston runs in: style "liner" (a sleeve with a collar at the top) or "block" (a
    square block section with the bore and a water jacket round it, open at the deck). Axis Z, the
    origin on the axis at the bottom. Slots: "hub" (casting), "bore" (the bore's wall),
    "tooth_face" (the deck)."""
    rb = bore / 2 + max(0.00003, 0.0006 * bore)
    t = wall or 0.08 * bore
    bore_cut = _slab(_circle2(0.0, 0.0, rb, 96), -0.01 * length, 1.01 * length, 2, name + "_bore")
    if style == "block":
        half = rb + t + 0.12 * bore + t
        body = _slab([(-half, -half), (half, -half), (half, half), (-half, half)], 0.0, length, 1, name)
        jacket = _slab(_circle2(0.0, 0.0, rb + t + 0.12 * bore, 64), 0.18 * length, 1.01 * length, 1, name + "_jk")
        core = _slab(_circle2(0.0, 0.0, rb + t, 64), 0.1 * length, length, 1, name + "_core")
        body = _cut(body, [jacket], slot="hub")
        body = h.union(body, [core])
        body = _cut(body, [bore_cut], slot="bore")
    else:
        collar = 0.07 * bore
        body = _slab(_circle2(0.0, 0.0, rb + t, 96), 0.0, length - collar + 0.0001, 1, name)
        top = _slab(_circle2(0.0, 0.0, rb + t + 0.07 * bore, 96), length - collar, length, 0, name + "_collar")
        body = _fuse([body, top], name, [bore_cut])
    body.name = name
    body[h.NO_BEVEL_KEY] = 1
    return _tag(body, type="cylinder", bore=bore, bore_radius=rb, length=length, style=style, period=[1, 1])


# --- fasteners and springs --------------------------------------------------------------------------
#
# A bolt's axis is its local Z, the underside of its head at z = 0 and the shank down -Z; a nut and a
# washer are centred on their axis with their bottom face at z = 0. Threads are right-handed ISO
# metric: a true helix (the surface's phase is z / pitch - angle / turn), so a nut turned one way
# about the bolt advances one pitch per turn and its thread stays in the bolt's.

THREAD_ANGLES = 36  # points round a threaded ring (a multiple of 6: the hex's corners fall on them)


def _hex_r(across_flats):
    s = across_flats / 2

    def radius(a):
        k = ((a + math.pi / 6) % (math.pi / 3)) - math.pi / 6
        return s / math.cos(k)
    return radius


def _thread_rings(diameter, pitch, z0, z1, internal, angles, taper=None):
    """Rings of a threaded surface from z0 to z1 (z0 < z1), a few to each pitch. taper(z) in [0, 1]
    scales the thread's depth (a chamfered end runs the thread out)."""
    mc = _mech()
    steps = max(2, int(math.ceil((z1 - z0) / (pitch / 8))))
    rings = []
    for i in range(steps + 1):
        z = z0 + (z1 - z0) * i / steps
        k = 1.0 if taper is None else taper(z)

        def radius(a, z=z, k=k):
            r = mc.thread_radius(z / pitch - a / (2 * math.pi), diameter, pitch, internal)
            base = diameter / 2 - (0.6134 * pitch if not internal else 0.5413 * pitch)
            return base + (r - base) * k if not internal else r
        rings.append(_circle_ring(angles, z, radius))
    return rings


def bolt(diameter, length, pitch=None, across_flats=None, thread_length=None, name="bolt"):
    """A hexagon-head bolt, ISO metric coarse unless pitch is given: the head (across_flats, height
    0.65 d, chamfered 30 degrees), a plain shank, then the thread (default 2d + 6 mm, the whole length
    when short) to a chamfered tip. Axis Z, the underside of the head at the origin, the shank down
    -Z. Slots: "tooth_face" (thread), "hub" (head and shank)."""
    mc = _mech()
    d = diameter
    p = pitch or mc.METRIC.get(round(d / 0.001), (0.15 * d / 0.001, 0))[0] * 0.001
    s = across_flats or (mc.METRIC.get(round(d / 0.001), (0, 1.6 * d / 0.001))[1] * 0.001)
    k = 0.65 * d
    b = min(length, thread_length or (2 * d + 0.006))
    angles = [2 * math.pi * i / THREAD_ANGLES for i in range(THREAD_ANGLES)]
    hexr = _hex_r(s)
    cham = 0.95 * s / 2  # the head's chamfer circle on top
    tip = 0.6 * p
    r_root = d / 2 - 0.6134 * p
    rings = [_circle_ring(angles, k, 0.0001),
             _circle_ring(angles, k, lambda a: min(hexr(a), cham)),
             _circle_ring(angles, k - 0.15 * k, hexr),
             _circle_ring(angles, 0.0, hexr),
             _circle_ring(angles, 0.0, d / 2),
             _circle_ring(angles, -(length - b), d / 2)]
    taper = (lambda z: min(1.0, max(0.0, (z + length) / (3 * p))))
    rings += list(reversed(_thread_rings(d, p, -length + tip, -(length - b) - 0.2 * p, False, angles, taper)))
    rings += [_circle_ring(angles, -length, r_root - 0.2 * p), _circle_ring(angles, -length, 0.0001)]
    slots = [1] * 5 + [0] * (len(rings) - 6) + [1]
    obj = _lathe(rings, slots[:len(rings)], name, ["tooth_face", "hub", "bore"])
    obj = h.merge_by_distance(obj, 1e-7)
    return _tag(obj, type="bolt", diameter=d, pitch=p, length=length, across_flats=s, head_height=k,
                period=[1, 6])


def nut(diameter, pitch=None, across_flats=None, height=None, name="nut"):
    """A hexagon nut (ISO 4032): across_flats, height 0.84 d, both faces chamfered 30 degrees, the
    internal ISO thread through it. Axis Z, its bottom face at the origin. Slots: "hub" (the hex),
    "tooth_face" (the thread)."""
    mc = _mech()
    d = diameter
    p = pitch or mc.METRIC.get(round(d / 0.001), (0.15 * d / 0.001, 0))[0] * 0.001
    s = across_flats or (mc.METRIC.get(round(d / 0.001), (0, 1.6 * d / 0.001))[1] * 0.001)
    m = height or 0.84 * d
    angles = [2 * math.pi * i / THREAD_ANGLES for i in range(THREAD_ANGLES)]
    hexr = _hex_r(s)
    cham = 0.95 * s / 2
    c = 0.1 * m
    rings = [_circle_ring(angles, 0.0, lambda a: min(hexr(a), cham)), _circle_ring(angles, c, hexr),
             _circle_ring(angles, m - c, hexr), _circle_ring(angles, m, lambda a: min(hexr(a), cham))]
    rings += list(reversed(_thread_rings(d, p, 0.0, m, True, angles)))
    slots = [1, 1, 1, 1] + [0] * (len(rings) - 5) + [1]
    obj = _lathe(rings, slots[:len(rings)], name, ["tooth_face", "hub", "bore"])
    return _tag(obj, type="nut", diameter=d, pitch=p, height=m, across_flats=s, period=[1, 6])


def washer(diameter, inner_diameter=None, outer_diameter=None, thickness=None, name="washer"):
    """A plain washer (ISO 7089) for a bolt of `diameter`: hole d + 0.05 d, outside 2 d, 0.2 d thick.
    Axis Z, its bottom face at the origin. Slots: "hub"."""
    d = diameter
    ri = (inner_diameter or 1.07 * d) / 2
    ro = (outer_diameter or 2.0 * d) / 2
    t = thickness or 0.2 * d
    c = 0.15 * t
    angles = [2 * math.pi * i / 64 for i in range(64)]
    rings = [_circle_ring(angles, 0.0, ri), _circle_ring(angles, 0.0, ro - c), _circle_ring(angles, c, ro),
             _circle_ring(angles, t - c, ro), _circle_ring(angles, t, ro - c), _circle_ring(angles, t, ri)]
    obj = _lathe(rings, [1] * 6, name, ["tooth_face", "hub", "bore"])
    return _tag(obj, type="washer", diameter=d, inner_radius=ri, outer_radius=ro, thickness=t, period=[1, 1])


def spring(wire_diameter, mean_diameter, free_length, coils, kind="compression", name="spring"):
    """A helical spring of round wire. "compression": `coils` turns in all, the last 0.75 turn at each
    end closed down onto the next; "extension": close-wound with a loop (a half-turn hook) at each end.
    Axis Z, its bottom at the origin; free_length is the body's length (the hooks reach past it).
    Slots: "hub" (the wire)."""
    w, rm = wire_diameter, mean_diameter / 2
    n = float(coils)
    steps_per = 24
    total = int(round(n * steps_per))
    zs = []
    if kind == "extension":
        pitch = w * 1.02
        zs = [w / 2 + pitch * i / steps_per for i in range(total + 1)]
    else:
        closed = 0.75
        active = max(0.5, n - 2 * closed)
        dz_closed = w * 1.03
        dz_active = (free_length - w - 2 * closed * dz_closed) / active
        z = w / 2
        for i in range(total + 1):
            zs.append(z)
            t = i / steps_per
            z += (dz_closed if t < closed or t >= n - closed else dz_active) / steps_per
    centres = [Vector((math.cos(2 * math.pi * i / steps_per) * rm, math.sin(2 * math.pi * i / steps_per) * rm, z))
               for i, z in enumerate(zs)]
    if kind == "extension":  # a hook at each end: a half turn of wire standing up across the axis
        first, last = centres[0], centres[-1]
        for base, sign in ((first, -1), (last, 1)):
            radial = Vector((base.x, base.y, 0.0)).normalized()
            hook = [base - radial * rm * (1 - math.cos(math.pi * j / 12))
                    + Vector((0, 0, sign * rm * 1.1 * math.sin(math.pi * j / 12))) for j in range(1, 13)]
            centres = (list(reversed(hook)) + centres) if sign < 0 else (centres + hook)
    frames = []  # each cross-section square to the wire, its axes carried along without twisting
    u = None
    for i, c in enumerate(centres):
        t = (centres[min(i + 1, len(centres) - 1)] - centres[max(i - 1, 0)]).normalized()
        if u is None:
            u = Vector((c.x, c.y, 0.0)).normalized()
        u = (u - t * u.dot(t)).normalized()
        frames.append((u, t.cross(u).normalized()))
    bm = bmesh.new()
    _tube(bm, centres, frames, [(w / 2, w / 2 * 0.001)] * len(centres), k=12)
    for f in bm.faces:
        f.material_index = 0
    obj = _new_object(bm, name, ["hub", "tooth_face", "bore"], no_bevel=True)
    return _tag(obj, type="spring", kind=kind, wire_diameter=w, mean_diameter=2 * rm, free_length=free_length,
                coils=n, solid_length=round((n + 1) * w, 7), period=[1, 1])


def gear_pair_layout(module, teeth_a, teeth_b, pressure_angle=20.0, helix_angle=0.0, internal=False,
                     direction_deg=0.0, phase_a_deg=0.0):
    """Where the second gear of a meshing pair goes (metres in, metres out): the exact centre
    distance, b's centre (x, y) with gear a at the origin and the line of centres at direction_deg
    in their plane, the profile shifts to build each with (spur_gear's shift; small pinions need
    one), the tip shortening, and the angle (degrees about its own axis) gear b must start at so a
    tooth sits in a gap from frame 1 when gear a starts at phase_a_deg. Also b's turns per turn of
    a ("ratio") and "same_direction". internal: b is a ring gear round a."""
    return _mech().gear_pair_layout(module, int(teeth_a), int(teeth_b), pressure_angle, helix_angle, internal,
                                    direction_deg, phase_a_deg)
