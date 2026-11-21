"""Shell modelling: one continuous skin with its details carved into it. Runs inside Blender only.

For objects whose main form is one smooth outer surface (cars, planes, helmets, controllers), the
skin is built as ONE closed mesh from rings of points (`skin`), smoothed, and the details are cut
into that same mesh: recesses with their own material for glass, lamps, grilles and vents
(`recess`), grooves for panel lines (`groove`), wheel openings (`cut_wells`) and fender bulges
(`flare`); shapes that stand out of it (bumpers, lips, skirts, boxed flares, a hood bulge) are
raised out of the same skin (`raise_region`). Nothing is glued on, so there are no joints. Only what is really a separate piece is
built apart: wheels, wings, mirrors, exhausts (`wheel`, `wing`, `mirror_housing`, `exhaust`), and
`surface_point` finds where such a piece meets the skin.

Details are drawn in a flat view of the object and pushed through it along that view:
  "side"  (u, v) = (y, z), looking from +X (both_sides repeats it on -X)
  "front" (u, v) = (x, z), looking from -Y
  "rear"  (u, v) = (x, z), looking from +Y
  "top"   (u, v) = (x, y), looking from +Z
  "aim"   (u, v) across and up in the plane facing `aim`, centred on `at` (class Aim): a detail cut
          along any direction, e.g. a headlight along a sloping nose's own normal
`until` is where the cut stops along the view (an x, y or z value; for an aim, how far past `at`), so
a detail drawn in the front view only cuts the nose. mirror_x repeats a front, rear, top or aimed
detail at -x. A groove whose path closes on itself is cut as two overlapping halves.

Materials: every surface gets a named slot material ("slot_paint", "slot_glass", "slot_trim",
"slot_lamp_front", "slot_lamp_rear", "slot_lamp_amber", "slot_lamp_led", "slot_lamp_led_rear", "slot_tyre", "slot_rim", "slot_chrome",
"slot_accent"); materials.car_finish (or materials.assign_slot) gives each slot its real material.
"""

import math

import bmesh
import bpy
from mathutils import Matrix, Vector

from . import helpers as h

BIG = 50.0  # metres: "far outside" for the cutters

SLOT_PREVIEW = {  # what each slot looks like before the materials step
    "paint": (0.55, 0.58, 0.62), "glass": (0.80, 0.83, 0.86), "trim": (0.05, 0.05, 0.055),
    "lamp_front": (0.92, 0.92, 0.90), "lamp_rear": (0.75, 0.05, 0.05), "lamp_amber": (0.95, 0.65, 0.05),
    "lamp_led": (0.95, 0.97, 1.0), "lamp_led_rear": (1.0, 0.1, 0.08),
    "tyre": (0.08, 0.08, 0.08), "rim": (0.78, 0.78, 0.80), "chrome": (0.85, 0.85, 0.87),
    "accent": (0.35, 0.35, 0.37), "gloss_black": (0.02, 0.02, 0.022),
    "lens": (0.85, 0.88, 0.9), "plate": (0.92, 0.92, 0.9),
}


def slot(name):
    """The placeholder material for a slot (created once), e.g. slot("glass")."""
    key = f"slot_{name}"
    mat = bpy.data.materials.get(key)
    if mat is None:
        mat = bpy.data.materials.new(key)
        mat.use_nodes = True
        color = SLOT_PREVIEW.get(name, (0.6, 0.6, 0.6))
        bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        bsdf.inputs["Base Color"].default_value = (*color, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.45
        mat.diffuse_color = (*color, 1.0)
        mat["mg_slot"] = name
    return mat


def _set_material(obj, name):
    me = obj.data
    me.materials.clear()
    me.materials.append(slot(name))
    for p in me.polygons:
        p.material_index = 0
    return obj


def weld_problems(me, distance=1e-6):
    """Edges that would not be clean (not exactly two faces) once vertices closer than `distance` are
    welded, as ModelGen welds every finished part: 0 for a sound mesh."""
    m = me.copy()
    bm = bmesh.new()
    bm.from_mesh(m)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=distance)
    bad = sum(1 for e in bm.edges if len(e.link_faces) != 2)
    bm.free()
    bpy.data.meshes.remove(m)
    return bad


def _volume(me):
    bm = bmesh.new()
    bm.from_mesh(me)
    v = bm.calc_volume(signed=True)
    bm.free()
    return v


def _sane(operation, before, cutter, after):
    """Whether a boolean's result volume is possible for the operation: the exact solver sometimes
    returns a near-empty mesh with no open edges, which must never win over a real result."""
    tol = 1e-6 + 1e-4 * abs(before)
    if operation == "UNION":
        return after >= max(before, cutter) - tol
    if operation == "DIFFERENCE":
        return before - abs(cutter) - tol <= after <= before + tol
    return -tol <= after <= min(before, cutter) + tol


def _weld(me, distance=1e-6):
    """Merge the vertices a boolean leaves on top of each other: the next manifold boolean refuses a
    mesh that has them."""
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=distance)
    bm.to_mesh(me)
    bm.free()
    me.update()


def _boolean(target, cutter, operation="DIFFERENCE"):
    """Boolean that keeps the cutter's materials on the faces it makes; the cutter is kept. The fast
    manifold solver goes first; if its result has open edges, or vertices so close they would weld
    into bad edges (two surfaces just touching), or it left the mesh unchanged (the fast solver's way
    of refusing a mesh), or its volume is impossible for the operation, the exact solver does it again
    and the cleanest sane result is kept, welded. A skin with cut details is crisp already, so it is
    marked to skip the automatic finishing bevel, which would tear the corners of the cuts."""
    target[h.NO_BEVEL_KEY] = 1
    cutter.hide_render = True
    before = target.data.copy()
    start = weld_problems(before)
    v0, vc = _volume(before), _volume(cutter.data)
    best = None
    for solver in ("MANIFOLD", "EXACT"):
        mod = target.modifiers.new("shell_boolean", "BOOLEAN")
        mod.operation = operation
        mod.object = cutter
        try:
            mod.solver = solver
        except (TypeError, AttributeError):
            target.modifiers.remove(mod)
            continue
        if hasattr(mod, "material_mode"):
            mod.material_mode = "TRANSFER"
        h.apply_modifiers(target)
        bad = weld_problems(target.data)
        # the fast solver can quietly return the mesh unchanged when it will not take it: that is no
        # result, so the exact solver gets its turn
        unchanged = (len(target.data.vertices), len(target.data.polygons)) == \
            (len(before.vertices), len(before.polygons))
        sane = _sane(operation, v0, vc, _volume(target.data))
        key = (not sane, bad, unchanged)
        if best is None or key < best[0]:
            if best is not None:
                bpy.data.meshes.remove(best[1])
            best = (key, target.data.copy())
        if sane and bad <= start and not (unchanged and solver == "MANIFOLD"):
            break
        old = target.data
        target.data = before.copy()
        bpy.data.meshes.remove(old)
    old = target.data
    target.data = best[1]
    if old is not best[1] and old.users == 0:
        bpy.data.meshes.remove(old)
    bpy.data.meshes.remove(before)
    _weld(target.data)
    return target


# --- the skin -----------------------------------------------------------------------------

def _cap(bm, ring, centre_line=None):
    """Close an end ring. A mirror-symmetric ring that starts at its bottom centre (point i mirrors
    point n - i, an even count) gets rows of quads up a centre line, which smooth into a clean flat
    face; any other ring gets one polygon. centre_line: the centre line's inner points (half - 1 of
    them, bottom to top), when the face's middle is shaped apart from its sides; empty: halfway
    between each point and its mirror."""
    n = len(ring)
    half = n // 2
    if n % 2 or n < 6:
        bm.faces.new(ring)
        return
    if centre_line is not None and len(centre_line) != half - 1:
        raise ValueError(f"an end's centre line needs {half - 1} points")
    centre = [ring[0]]
    for k in range(1, half):
        if centre_line is not None:
            centre.append(bm.verts.new(tuple(centre_line[k - 1])))
            continue
        r, l = ring[k].co, ring[n - k].co
        centre.append(bm.verts.new(((r.x + l.x) / 2, (r.y + l.y) / 2, (r.z + l.z) / 2)))
    centre.append(ring[half])
    right = [ring[k] for k in range(half + 1)]
    left = [ring[0]] + [ring[n - k] for k in range(1, half)] + [ring[half]]
    for side in (right, left):
        for k in range(half):
            face = []
            for v in (centre[k], side[k], side[k + 1], centre[k + 1]):
                if not face or face[-1] is not v:
                    face.append(v)
            if face[0] is face[-1]:
                face.pop()
            if len(face) >= 3:
                bm.faces.new(face)


def skin(rings, levels=2, creases=None, end_crease=0.5, name="skin", material="paint", ring_creases=None,
         end_centre_lines=None):
    """One closed, smoothed mesh through rings of (x, y, z) points (every ring the same count, the
    same winding, in order along the object), its two ends capped (with clean quads when the rings
    start at the bottom centre and are mirror-symmetric, as a car's are). creases maps a point index
    in the rings to a crease from 0 to 1, so the line through those points stays crisp (a car's
    shoulder line); end_crease does the same for the edge around each cap, and ring_creases (ring
    index to crease) for whole rings (where the windshield meets the hood, say). end_centre_lines:
    (first cap's, last cap's) centre-line points (see _cap), or empty for flat caps."""
    bm = bmesh.new()
    loops = [[bm.verts.new(tuple(p)) for p in ring] for ring in rings]
    n = len(loops[0])
    if any(len(r) != n for r in loops):
        raise ValueError("every ring needs the same number of points")
    for a, b in zip(loops, loops[1:]):
        for j in range(n):
            bm.faces.new((a[j], a[(j + 1) % n], b[(j + 1) % n], b[j]))
    for ring, line in zip((loops[0], loops[-1]), end_centre_lines or (None, None)):
        _cap(bm, ring, line)
    bm.edges.ensure_lookup_table()
    layer = bm.edges.layers.float.get("crease_edge") or bm.edges.layers.float.new("crease_edge")
    for index, value in (creases or {}).items():
        for i in (index, (n - index) % n):  # the point and its mirror on the other side
            for a, b in zip(loops, loops[1:]):
                e = bm.edges.get((a[i], b[i]))
                if e is not None:
                    e[layer] = value
    edges_round = [(loops[0], end_crease), (loops[-1], end_crease)]
    edges_round += [(loops[i], v) for i, v in (ring_creases or {}).items() if 0 < i < len(loops) - 1]
    for ring, value in edges_round:
        for j in range(n):
            e = bm.edges.get((ring[j], ring[(j + 1) % n]))
            if e is not None:
                e[layer] = value
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    if bm.calc_volume(signed=True) < 0:  # the details offset along the normals: they must point out
        bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    _set_material(obj, material)
    if levels:
        h.subdivide(obj, levels)
    return obj


def fit(obj, half_width=None, y_range=None, z_range=None):
    """Scale the skin so its widest point is at ±half_width and it spans y_range and z_range exactly
    (smoothing pulls a skin inside its rings; this puts the sheet's numbers back)."""
    me = obj.data
    xs = [v.co.x for v in me.vertices]
    ys = [v.co.y for v in me.vertices]
    zs = [v.co.z for v in me.vertices]
    kx = half_width / max(abs(min(xs)), abs(max(xs))) if half_width else 1.0
    y0, y1 = min(ys), max(ys)
    z0, z1 = min(zs), max(zs)
    for v in me.vertices:
        x, y, z = v.co
        if y_range:
            y = y_range[0] + (y - y0) * (y_range[1] - y_range[0]) / (y1 - y0)
        if z_range:
            z = z_range[0] + (z - z0) * (z_range[1] - z_range[0]) / (z1 - z0)
        v.co = (x * kx, y, z)
    me.update()
    return obj


def _smooth(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def flare(obj, centres, radius, amount, spread=0.16, x_from=0.5, z_top=None, stretch=None, plateau=0.0):
    """Bulge the sides outward around the wheels (fenders). centres: (y, z) of each wheel; the bulge
    is `amount` inside `radius` (plus `plateau`) and fades to nothing over `spread` beyond it; only
    where |x| > x_from and below z_top (it fades out over 0.12 under it). stretch: per centre
    (forward, backward), how much longer than tall the bulge reaches toward the front and the back
    (1: round; a rear haunch reaches far forward into the door). A short spread with a plateau makes
    a blister: a raised flare whose edge rolls off quickly but smoothly."""
    me = obj.data
    stretch = stretch or [(1.0, 1.0)] * len(centres)
    for v in me.vertices:
        ax = abs(v.co.x)
        if ax <= x_from:
            continue
        wx = _smooth((ax - x_from) / max(1e-6, 0.35 * x_from))
        wz = 1.0 if z_top is None else _smooth((z_top - v.co.z) / 0.12)
        w = 0.0
        for (cy, cz), (fwd, back) in zip(centres, stretch):
            dy = v.co.y - cy
            dy /= fwd if dy < 0 else back
            d = math.hypot(dy, v.co.z - cz)
            w = max(w, 1.0 if d <= radius + plateau else _smooth(1 - (d - radius - plateau) / spread))
        if w > 0:
            v.co.x += math.copysign(amount * w * wx * wz, v.co.x)
    me.update()
    return obj


def bumper(obj, end_y, direction, out, top_z, edge, wrap, side=0.35):
    """Swell a bumper out of one end of a skin, as one smooth surface with it: below top_z the end
    face moves outward by `out` along its own horizontal direction (so it follows the end's curve and
    rounds its corners), the swell rolling off to nothing over `edge` under top_z and fading out along
    the sides over `wrap` from the end, where it stands out only `side` of the full amount. end_y: the
    skin's end before the swell; direction: +1 for the rear (toward +Y), -1 for the front. Edge loops
    are cut through the rolled top edge and the side fade first, so the curve is smooth however coarse
    the skin is. The mesh stays one closed piece."""
    y0 = end_y - direction * wrap  # where the swell begins along the sides
    fade = wrap * 0.7
    bm = bmesh.new()
    bm.from_mesh(obj.data)

    def near_end(f, margin=0.05):
        return max((v.co.y - y0) * direction for v in f.verts) > -margin

    def cut(co, no, keep):
        faces = [f for f in bm.faces if keep(f)]
        geom = list({e for f in faces for e in f.edges}) + faces + list({v for f in faces for v in f.verts})
        bmesh.ops.bisect_plane(bm, geom=geom, plane_co=co, plane_no=no, dist=1e-5)

    for k in range(7):  # through the rolled top edge
        cut((0, 0, top_z - edge * k / 6), (0, 0, 1), near_end)
    for k in range(6):  # through the fade along the sides
        cut((0, y0 + direction * fade * k / 5, 0), (0, 1, 0),
            lambda f: near_end(f) and min(v.co.z for v in f.verts) < top_z + 0.02)
    bm.normal_update()
    moves = []
    for v in bm.verts:
        d = (v.co.y - y0) * direction
        if d <= 0 or v.co.z >= top_z:
            continue
        nx, ny = v.normal.x, v.normal.y
        flat = math.hypot(nx, ny)
        if flat < 1e-3:
            continue  # the underside: nothing to push outward
        facing = max(0.0, ny * direction / flat)  # 1 on the end face, 0 on the side
        amount = (out * _smooth((top_z - v.co.z) / edge) * _smooth(d / fade) * min(1.0, flat * 1.5)
                  * (side + (1 - side) * facing))
        moves.append((v, nx / flat * amount, ny / flat * amount))
    for v, dx, dy in moves:
        v.co.x += dx
        v.co.y += dy
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return obj


def cut_wells(obj, centres, radius, x_from, material="trim", segments=64):
    """Wheel openings: cylinders along X of `radius` around each (y, z) centre, from |x| = x_from out
    through the side on both sides; the well walls get `material`."""
    for cy, cz in centres:
        for sign in (1, -1):
            length = BIG / 10
            cyl = h.cylinder(radius, length, segments=segments, rotation_deg=(0, 90, 0),
                             location=(sign * (x_from + length / 2), cy, cz), name="well_cutter")
            _set_material(cyl, material)
            _boolean(obj, cyl)
            bpy.data.objects.remove(cyl, do_unlink=True)
    return obj


# --- details cut into the skin ---------------------------------------------------------------

class Aim:
    """A view along any direction: `aim` is the way the cut travels into the skin (x, y, z) and `at`
    the point its outline is centred on. In the outline, u runs across (toward +X when the aim is
    along +Y) and v up; w is the distance along the aim from `at` (until: how far past `at` the cut
    may reach). A headlight on a sloping nose is cut along the nose's own normal this way, so it
    lies on the surface instead of being pushed straight back from the front."""

    def __init__(self, aim, at):
        a = Vector(tuple(float(c) for c in aim)).normalized()
        if a.length < 0.5:
            raise ValueError("aim must be a direction, not (0, 0, 0)")
        side = Vector((1.0, 0.0, 0.0)) - a * a.x
        if side.length < 1e-3:  # aimed along X: u runs along Y instead
            side = Vector((0.0, -1.0, 0.0)) - a * a.y
        self.aim, self.at = a, Vector(tuple(float(c) for c in at))
        self.u = side.normalized()
        self.v = self.u.cross(a).normalized()

    def to_world(self, u, v, w):
        return self.at + self.u * u + self.v * v + self.aim * w

    def mirrored(self):
        return Aim((-self.aim.x, self.aim.y, self.aim.z), (-self.at.x, self.at.y, self.at.z))


def _to_world(view, u, v, w):
    if isinstance(view, Aim):
        return view.to_world(u, v, w)
    if view == "side":
        return Vector((w, u, v))
    if view in ("front", "rear"):
        return Vector((u, w, v))
    if view == "top":
        return Vector((u, v, w))
    raise ValueError("view must be side, front, rear or top")


def _ranges(view, until, both_sides):
    """The spans along the view axis the cutter fills: from `until` out past the surface."""
    if isinstance(view, Aim):
        return [(-BIG, until)]
    if view == "side":
        return [(until, BIG)] + ([(-BIG, -until)] if both_sides else [])
    if view == "front":
        return [(-BIG, until)]
    if view == "rear":
        return [(until, BIG)]
    return [(until, BIG)]


def _prism(view, outline, w0, w1, name="cutter"):
    bm = bmesh.new()
    bottom = [bm.verts.new(_to_world(view, u, v, w0)) for u, v in outline]
    top = [bm.verts.new(_to_world(view, u, v, w1)) for u, v in outline]
    n = len(outline)
    bm.faces.new(bottom)
    bm.faces.new(list(reversed(top)))
    for j in range(n):
        k = (j + 1) % n
        bm.faces.new((bottom[j], bottom[k], top[k], top[j]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _ccw(points):
    area = sum(points[i][0] * points[(i + 1) % len(points)][1] - points[(i + 1) % len(points)][0] * points[i][1]
               for i in range(len(points)))
    return list(points) if area > 0 else list(reversed(points))


def _crosses(points):
    """True when the closed outline crosses itself."""
    n = len(points)

    def hit(a, b, c, d):
        def side(p, q, r):
            return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return side(a, b, c) * side(a, b, d) < 0 and side(c, d, a) * side(c, d, b) < 0

    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            if hit(points[i], points[(i + 1) % n], points[j], points[(j + 1) % n]):
                return True
    return False


def _area(points):
    return sum(points[i][0] * points[(i + 1) % len(points)][1] - points[(i + 1) % len(points)][0] * points[i][1]
               for i in range(len(points))) / 2


def inset_outline(points, distance):
    """The outline moved inward by distance (a frame inside a window outline), or None when the
    outline is too thin for it (the moved outline would turn inside out or cross itself)."""
    pts = _ccw(points)
    n = len(pts)
    out = []
    for i in range(n):
        p0, p1, p2 = Vector(pts[i - 1]), Vector(pts[i]), Vector(pts[(i + 1) % n])
        d0, d1 = (p1 - p0), (p2 - p1)
        if d0.length < 1e-9 or d1.length < 1e-9:
            continue
        n0 = Vector((-d0.y, d0.x)).normalized()
        n1 = Vector((-d1.y, d1.x)).normalized()
        bis = n0 + n1
        if bis.length < 1e-6:
            bis = n1
        bis.normalize()
        scale = distance / max(0.35, bis.dot(n1))
        q = p1 + bis * scale
        out.append((q.x, q.y))
    if len(out) < 3 or _area(out) <= 0 or _crosses(out):
        return None
    return out


class _Inner:
    """Copies of the clean skin moved along its normals, one per distance (inward for a positive
    depth, outward for a negative one): a cut down to such a copy, or a raise up to it, follows the
    curved surface at an even depth or height."""

    def __init__(self, obj):
        self.source = obj.data.copy()
        self.cache = {}

    def get(self, depth, material):
        key = round(depth, 5)
        if key not in self.cache:
            bm = bmesh.new()
            bm.from_mesh(self.source)
            bm.normal_update()
            for v in bm.verts:
                v.co -= v.normal * depth
            me = bpy.data.meshes.new(f"inner_{key}")
            bm.to_mesh(me)
            bm.free()
            obj = bpy.data.objects.new(me.name, me)
            bpy.context.scene.collection.objects.link(obj)
            obj.hide_render = True
            self.cache[key] = obj
        return _set_material(self.cache[key], material)

    def free(self):
        for obj in self.cache.values():
            me = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.meshes.remove(me)
        bpy.data.meshes.remove(self.source)
        self.cache = {}


def _cut_to(obj, inner, view, outline, until, depth, floor, walls, both_sides):
    for w0, w1 in _ranges(view, until, both_sides):
        prism = _prism(view, _ccw(outline), w0, w1)
        _set_material(prism, walls)
        _boolean(prism, inner.get(depth, floor))
        _boolean(obj, prism)
        bpy.data.objects.remove(prism, do_unlink=True)


def _mirrored(view, outline, mirror_x):
    """(view, outline) of a detail and, with mirror_x, of its mirror image at -x."""
    out = [(view, list(outline))]
    if mirror_x and isinstance(view, Aim):
        out.append((view.mirrored(), [(-u, v) for u, v in reversed(outline)]))
    elif mirror_x and view in ("front", "rear", "top"):
        out.append((view, [(-u, v) for u, v in reversed(outline)]))
    return out


def recess(obj, view, outline, depth=0.01, material="trim", until=0.0, frame=0.0, frame_material="trim",
           frame_depth=None, mirror_x=False, both_sides=True, inner=None):
    """Sink the part of the skin inside `outline` by `depth`, following the surface; the floor gets
    `material`, the walls `frame_material`. With frame > 0 a ledge of that width runs round the
    inside of the outline at frame_depth (a dark surround round glass or a lamp) and the middle sinks
    to `depth`. outline: (u, v) points in the view's plane."""
    own = inner is None
    inner = inner or _Inner(obj)
    try:
        for view, shape in _mirrored(view, outline, mirror_x):
            inside = inset_outline(shape, frame) if frame > 0 else None
            if inside:  # no room for the frame inside a thin outline: then the whole outline is floor
                fd = frame_depth if frame_depth is not None else depth * 0.4
                _cut_to(obj, inner, view, shape, until, fd, frame_material, frame_material, both_sides)
                shape = inside
            _cut_to(obj, inner, view, shape, until, depth, material, frame_material, both_sides)
    finally:
        if own:
            inner.free()
    return obj


def raise_region(obj, view, outline, height=0.03, material="paint", until=0.0, mirror_x=False, both_sides=True,
                 inner=None):
    """Lift the part of the skin inside `outline` outward by `height`, following the surface, with
    crisp walls: a bumper standing out of the nose, a lip, a side skirt, a boxed flare, a hood bulge.
    The raised top and walls get `material`. The result is still one closed mesh. outline: (u, v)
    points in the view's plane; until stops it along the view as for recess. A recess cut later
    inside a raised area measures its depth from the original skin, so it needs the height added."""
    own = inner is None
    inner = inner or _Inner(obj)
    try:
        for view, shape in _mirrored(view, outline, mirror_x):
            for w0, w1 in _ranges(view, until, both_sides):
                prism = _prism(view, _ccw(shape), w0, w1)
                _set_material(prism, material)
                _boolean(prism, inner.get(-height, material), "INTERSECT")  # the prism up to the raised surface
                # only a layer at the surface, never the inside; raises of different heights stop at
                # different depths, so two that overlap (a lip on a bumper) never share a surface
                _boolean(prism, inner.get(0.003 + 0.05 * height, material))
                _boolean(obj, prism, "UNION")
                bpy.data.objects.remove(prism, do_unlink=True)
                keep_largest(obj)
    finally:
        if own:
            inner.free()
    return obj


def keep_largest(obj):
    """Delete every separate piece of the mesh but the biggest: overlapping raises can leave tiny
    shells sealed inside the skin, and a skin is one piece."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    seen, pieces = set(), []
    for face in bm.faces:
        if face.index in seen:
            continue
        piece, todo = [], [face]
        seen.add(face.index)
        while todo:
            f = todo.pop()
            piece.append(f)
            for e in f.edges:
                for g in e.link_faces:
                    if g.index not in seen:
                        seen.add(g.index)
                        todo.append(g)
        pieces.append(piece)
    if len(pieces) > 1:
        pieces.sort(key=len, reverse=True)
        doomed = [f for piece in pieces[1:] for f in piece]
        bmesh.ops.delete(bm, geom=doomed, context="FACES")
        bm.to_mesh(obj.data)
        obj.data.update()
    bm.free()
    return obj


def surface_point(obj, origin, direction, distance=BIG * 2):
    """Where a ray from origin along direction first meets the object: (location, normal), or None."""
    mw = obj.matrix_world
    inv = mw.inverted()
    o = inv @ Vector(origin)
    d = (inv.to_3x3() @ Vector(direction)).normalized()
    hit, loc, normal, _ = obj.ray_cast(o, d, distance=distance)
    if not hit:
        return None
    return mw @ loc, (mw.to_3x3() @ normal).normalized()


def _view_ray(view, u, v, until, side=1):
    """A ray from outside toward the skin through (u, v) of the view."""
    if isinstance(view, Aim):
        return view.to_world(u, v, -BIG / 5), view.aim
    if view == "side":
        return _to_world(view, u, v, side * BIG / 5), Vector((-side, 0, 0))
    if view == "front":
        return _to_world(view, u, v, -BIG / 5), Vector((0, 1, 0))
    if view == "rear":
        return _to_world(view, u, v, BIG / 5), Vector((0, -1, 0))
    return _to_world(view, u, v, BIG / 5), Vector((0, 0, -1))


def groove(obj, view, path, width=0.006, material="trim", until=0.0, both_sides=True, mirror_x=False, step=0.01):
    """A panel line: a round groove `width` wide along `path` ((u, v) points in the view's plane),
    following the surface."""
    sides = (1, -1) if view == "side" and both_sides else (1,)
    clean = []  # a repeated point would twist the swept tube where it sits
    for p in path:
        if not clean or math.dist(clean[-1], p) > 1e-4:
            clean.append(tuple(p))
    while len(clean) > 2 and math.dist(clean[0], clean[-1]) <= 1e-4:
        clean.pop()
    for view, pts2d in _mirrored(view, clean, mirror_x):
        dense = []
        for (u0, v0), (u1, v1) in zip(pts2d, pts2d[1:]):
            n = max(1, int(math.hypot(u1 - u0, v1 - v0) / step))
            dense += [(u0 + (u1 - u0) * i / n, v0 + (v1 - v0) * i / n) for i in range(n)]
        dense.append(pts2d[-1])
        for side in sides:
            # the line follows the surface; where a ray misses it, only grazes it, or lands far from
            # the last point (it hit another surface), the line breaks there, so the swept tube never
            # zigzags through itself
            runs, run = [], []
            for u, v in dense:
                origin, direction = _view_ray(view, u, v, until, side)
                hit = surface_point(obj, origin, direction)
                point = None
                if hit and abs(hit[1].dot(Vector(direction).normalized())) > 0.25:
                    point = tuple(hit[0] - hit[1] * width * 0.15)
                if point is not None and run and (Vector(point) - Vector(run[-1])).length > 4 * step:
                    point = None if len(run) < 2 else point
                    runs.append(run)
                    run = []
                if point is None:
                    if len(run) >= 2:
                        runs.append(run)
                    run = []
                else:
                    run.append(point)
            if len(run) >= 2:
                runs.append(run)
            runs = [r for r in runs if len(r) >= 2]
            if not runs:
                continue
            if len(runs) == 1 and len(runs[0]) > 8 and (Vector(runs[0][0]) - Vector(runs[0][-1])).length < width:
                # a closed loop: one tube whose ends meet makes a broken cutter, so cut it as two
                # halves that overlap a little at both joins
                pts3d = runs[0]
                half = len(pts3d) // 2
                runs = [pts3d[:half + 3], pts3d[half - 3:] + pts3d[1:4]]
            for run in runs:
                tube = h.sweep(run, radius=width / 2, smooth=False, profile_segments=8, name="groove_cutter")
                _set_material(tube, material)
                _boolean(obj, tube)
                bpy.data.objects.remove(tube, do_unlink=True)
    return obj


def apply_feature(obj, feature, inner=None):
    """Cut or raise one detail described as a dict (the form a car sheet's `features` use): kind
    "recess" with outline, depth, material, frame...; kind "raise" with outline, height, material; or
    kind "groove" with path and width."""
    from .shells.car_sheet import outline_points  # plain Python, shared with the pipeline
    f = dict(feature)
    view = f.get("view", "side")
    if view == "aim":
        view = Aim(f["aim"], f["at"])
    until = float(f.get("until", 0.0))
    if f.get("kind", "recess") == "groove":
        return groove(obj, view, [tuple(p) for p in f["path"]], float(f.get("width", 0.006)), f.get("material", "trim"),
                      until, f.get("both_sides", True), f.get("mirror_x", False))
    if f.get("kind") == "raise":
        return raise_region(obj, view, outline_points(f["outline"]), float(f.get("height", 0.03)),
                            f.get("material", "paint"), until, f.get("mirror_x", False), f.get("both_sides", True),
                            inner)
    return recess(obj, view, outline_points(f["outline"]), float(f.get("depth", 0.01)), f.get("material", "trim"),
                  until, float(f.get("frame", 0.0)), f.get("frame_material", "trim"), f.get("frame_depth"),
                  f.get("mirror_x", False), f.get("both_sides", True), inner)


# --- separate pieces ---------------------------------------------------------------------------

def _rotate(obj, matrix):
    obj.data.transform(obj.matrix_basis)  # bake the object's own placement first, then move the mesh
    obj.location, obj.rotation_euler, obj.scale = (0, 0, 0), (0, 0, 0), (1, 1, 1)
    obj.data.transform(matrix)
    obj.data.update()
    return obj


def wheel(diameter=0.65, width=0.245, rim_diameter=None, spokes=6, style="spoke", dish=0.02, hub_reach=0.15):
    """A wheel: tyre and rim, axle along X, outer face toward +X, centred on the origin. style
    "spoke" (spokes from 3 to 12), "mesh" (many thin spokes) or "disc". hub_reach: how far a hub stub
    runs inward along -X (to touch the wheel-well wall, so the car stands on its wheels)."""
    R = diameter / 2
    rim_r = (rim_diameter / 2) if rim_diameter else R * 0.70
    W = width
    sh = min(0.035, W * 0.18)
    bulge = min(0.006, W * 0.025)  # the sidewall bulges out a little between the rim and the tread
    mid = (rim_r + R - sh * 1.4) / 2
    tyre_profile = h.smooth_points([(rim_r - 0.004, -W * 0.40), (mid, -W / 2 - bulge * 0.4), (R - sh * 1.4, -W / 2),
                                    (R - sh * 0.15, -W / 2 + sh * 0.5),
                                    (R, -W * 0.25), (R, W * 0.25), (R - sh * 0.15, W / 2 - sh * 0.5),
                                    (R - sh * 1.4, W / 2), (mid, W / 2 + bulge * 0.4), (rim_r - 0.004, W * 0.40)],
                                   samples=4)
    tyre = h.revolve(tyre_profile, segments=64, closed=True, name="tyre")
    _set_material(tyre, "tyre")
    barrel = h.revolve([(rim_r - 0.014, -W * 0.42), (rim_r + 0.004, -W * 0.42), (rim_r + 0.004, W * 0.42),
                        (rim_r - 0.014, W * 0.42)], segments=64, closed=True, name="barrel")
    face_t = 0.03
    face_z = W * 0.42 - dish - face_t / 2
    face = h.cylinder(rim_r - 0.006, face_t, segments=64, location=(0, 0, face_z), name="rim_face")
    hub_r = max(0.045, rim_r * 0.22)
    if style != "disc":
        count = 14 if style == "mesh" else max(3, int(spokes))
        r_in, r_out = hub_r * 1.25, rim_r * 0.88
        spoke_w = (0.018 if style == "mesh" else max(0.03, rim_r * 0.16))
        cutters = []
        for i in range(count):
            a0 = 2 * math.pi * i / count + spoke_w / r_out
            a1 = 2 * math.pi * (i + 1) / count - spoke_w / r_out
            if a1 <= a0:
                continue
            outer = [(r_out * math.cos(a0 + (a1 - a0) * k / 10), r_out * math.sin(a0 + (a1 - a0) * k / 10)) for k in range(11)]
            b0 = 2 * math.pi * i / count + spoke_w / r_in
            b1 = 2 * math.pi * (i + 1) / count - spoke_w / r_in
            inner = [(r_in * math.cos(b0 + (b1 - b0) * k / 4), r_in * math.sin(b0 + (b1 - b0) * k / 4)) for k in range(5)] \
                if b1 > b0 else [(r_in * math.cos((a0 + a1) / 2), r_in * math.sin((a0 + a1) / 2))]
            window = outer + list(reversed(inner))
            cutters.append(h.extrude(window, face_t * 3, location=(0, 0, face_z - face_t * 1.5), name="window"))
        h.boolean(face, cutters)
    _set_material(barrel, "rim")
    _set_material(face, "rim")
    cap = h.cylinder(hub_r, 0.02, segments=32, location=(0, 0, face_z + face_t / 2 + 0.008), name="cap")
    _set_material(cap, "chrome")
    hub = h.cylinder(hub_r * 0.8, hub_reach + face_z, segments=32,
                     location=(0, 0, face_z - (hub_reach + face_z) / 2), name="hub")
    _set_material(hub, "trim")
    # the rim's stepped outer lip over the tyre bead
    lip_z = W * 0.42
    lip = h.revolve([(rim_r - 0.012, lip_z - 0.004), (rim_r + 0.01, lip_z - 0.004), (rim_r + 0.01, lip_z + 0.006),
                     (rim_r - 0.004, lip_z + 0.01), (rim_r - 0.012, lip_z + 0.01)], segments=64, closed=True,
                    name="rim_lip")
    _set_material(lip, "rim")
    # a brake disc and caliper behind the spokes, seen through them: smaller than the rim, set well
    # back and dark, so the gaps between the spokes still read as gaps
    disc_t = 0.026
    disc_z = face_z - face_t / 2 - 0.042 - disc_t / 2
    disc_r = 0.8 * min(rim_r * 0.8, rim_r - 0.03)
    disc = h.cylinder(disc_r, disc_t, segments=48, location=(0, 0, disc_z), name="brake_disc")
    _set_material(disc, "trim")
    cal_r = disc_r - 0.016
    angle = math.radians(35)  # toward the back and up, as a car's caliper usually sits
    caliper = h.box((0.04, min(0.104, disc_r * 0.75), disc_t + 0.024),
                    location=(cal_r * math.cos(angle), cal_r * math.sin(angle), disc_z),
                    rotation_deg=(0, 0, math.degrees(angle)), name="caliper")
    h.bevel(caliper, 0.008, segments=2)
    _set_material(caliper, "accent")
    pieces = [tyre, barrel, face, cap, hub, lip, disc, caliper]
    turn = Matrix.Rotation(math.radians(90), 4, "Y")  # build axis Z becomes the axle X, outer face +X
    for p in pieces:
        _rotate(p, turn)
    return pieces


def _airfoil(chord, thickness, camber=0.02, n=12):
    """Closed (y, z) outline of a cambered blade, leading edge at y = -chord / 2."""
    top, bottom = [], []
    for i in range(n + 1):
        t = i / n
        yt = 5 * thickness * (0.2969 * math.sqrt(t) - 0.126 * t - 0.3516 * t ** 2 + 0.2843 * t ** 3 - 0.1036 * t ** 4)
        yc = camber * 4 * t * (1 - t)
        y = -chord / 2 + chord * t
        top.append((y, yc + yt))
        bottom.append((y, yc - yt))
    return h._dedupe(top + list(reversed(bottom[1:-1])))


def popup_pod(width, length, face, rise=0.0, bury=0.04):
    """A raised pop-up headlight: seen from the side a wedge, its upright front face (the lamp) at
    y = 0 facing -Y, its lid sloping back to the hinge at y = length. The front face's foot is at z = 0
    and the hinge at z = rise (the hood's slope); both run `bury` down into the hood. Centred on x = 0.
    The lid is slot "paint", the face's surround "trim" and the lens "lamp_led" (lit)."""
    top = 0.025  # a short flat top behind the face before the lid slopes away
    pod = h.extrude([(0.0, -bury), (0.0, face), (top, face), (length, rise + 0.006), (length, rise - bury)],
                    width, plane="YZ", center=True, name="popup_pod")
    h.bevel(pod, 0.006, segments=2, angle_deg=40.0)
    _set_material(pod, "paint")
    surround = h.box((width - 0.008, 0.006, face - 0.006), location=(0, -0.002, face / 2 - 0.002),
                     name="popup_surround")
    _set_material(surround, "trim")
    lens = h.box((width - 0.03, 0.006, face - 0.03), location=(0, -0.005, face / 2), name="popup_lens")
    h.bevel(lens, 0.004, segments=2)
    _set_material(lens, "lamp_led")  # lit: the lamps are shown raised and on
    return [pod, surround, lens]


def wing(span=1.62, chord=None, height=None, upright_spacing=0.95, endplates=True, uprights=2, angle_deg=None,
         style="gt", plate_height=None):
    """A rear wing: a thin flat plank along X, tilted nose-down, on open truss brackets (two legs and a
    cross brace on a foot plate each), with flat angular end plates. style "gt" (tall brackets, big
    plates), "street" (lower, smaller plates) or "curved" (the plank dips in the middle and rises to the
    plates). `height` is the plank's middle above the bracket feet, which sit on Z = 0 around the origin
    (the assembly seats them on the deck); it is never less than car_sheet.MIN_WING_LIFT. chord, height,
    angle_deg and plate_height default to the style's own. Slot "accent". Every shape comes from
    car_sheet.wing_geometry, which also gives the plan the wing's size."""
    from .shells.car_sheet import wing_geometry  # plain Python, shared with the pipeline
    g = wing_geometry(span=span, chord=chord, height=height, upright_spacing=upright_spacing, endplates=endplates,
                      uprights=uprights, angle_deg=angle_deg, style=style, plate_height=plate_height)
    blade = h.extrude(g["blade"], span, plane="YZ", center=True, name="blade")
    if g["dip"]:  # the plank dips in the middle: edge loops along the span, then each lowered by its place
        bm = bmesh.new()
        bm.from_mesh(blade.data)
        for k in range(1, 16):
            geom = list(bm.verts) + list(bm.edges) + list(bm.faces)
            bmesh.ops.bisect_plane(bm, geom=geom, plane_co=(-span / 2 + span * k / 16, 0, 0), plane_no=(1, 0, 0),
                                   dist=1e-6)
        for v in bm.verts:
            v.co.z += g["lift"](v.co.x)
        bm.to_mesh(blade.data)
        bm.free()
        blade.data.update()
    pieces = [blade]
    for sign in (1, -1) if g["plate"] else ():
        plate = h.extrude(g["plate"], g["sheet"], plane="YZ", center=True, name="endplate")
        _rotate(plate, Matrix.Translation((sign * (span / 2 + g["sheet"] / 2), 0, 0)))
        pieces.append(plate)
    for x, bars in g["brackets"]:
        for bar in bars:
            leg = h.extrude(bar, g["sheet"], plane="YZ", center=True, name="bracket")
            _rotate(leg, Matrix.Translation((x, 0, 0)))
            pieces.append(leg)
        fy0, fy1, fz = g["foot"]
        pieces.append(h.box((0.035, fy1 - fy0, fz), location=(x, (fy0 + fy1) / 2, fz / 2), name="foot"))
    for piece in pieces:
        _set_material(piece, "accent")
    return pieces


def mirror_housing(length=0.17, height=0.10, depth=0.085, stalk=0.06):
    """A door mirror for the right side: stalk from the origin outward (+X), housing at its end with
    the glass facing back (+Y); slots paint, glass and trim. The assembly mirrors it for the left."""
    cage = h.box((length, depth, height), location=(stalk + length / 2, 0.0, stalk * 0.6 + height / 2), name="housing")
    h.bevel(cage, min(height, depth) * 0.3, segments=2)
    h.subdivide(cage, 2)
    _set_material(cage, "paint")
    glass = h.extrude(h.rounded_rect_points(length * 0.82, height * 0.72, 0.015), 0.006, plane="XZ", name="mirror_glass")
    _rotate(glass, Matrix.Translation((stalk + length / 2, depth / 2 + 0.004, stalk * 0.6 + height / 2)))
    _set_material(glass, "glass")
    arm = h.sweep([(-0.01, 0.0, 0.0), (stalk * 0.6, 0.0, stalk * 0.25), (stalk + 0.02, 0.0, stalk * 0.6 + height * 0.4)],
                  radius=0.012, name="stalk")
    _set_material(arm, "trim")
    return [cage, glass, arm]


def exhaust(radius=0.045, length=0.16, count=1, spacing=0.11):
    """Exhaust tips: tubes from the origin running toward +Y with an open end; slots chrome and trim."""
    pieces = []
    turn = Matrix.Rotation(math.radians(-90), 4, "X")  # build axis Z becomes +Y
    for i in range(max(1, int(count))):
        x = (i - (count - 1) / 2) * spacing
        wall = 0.004
        tube = h.revolve([(0.0, 0.02), (radius - wall, 0.02), (radius - wall, length), (radius, length),
                          (radius, 0.0), (0.0, 0.0)], segments=40, name="tip")
        _set_material(tube, "chrome")
        dark = h.cylinder(radius - wall - 0.0005, 0.004, segments=40, location=(0, 0, 0.025), name="tip_dark")
        _set_material(dark, "trim")
        for p in (tube, dark):
            _rotate(p, Matrix.Translation((x, 0, 0)) @ turn)
            pieces.append(p)
    return pieces
