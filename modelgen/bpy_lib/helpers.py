"""ModelGen helper library for building parts in Blender. Runs inside Blender only.

Conventions: units are metres, Z is up, the model stands on Z = 0, the front faces -Y. Every
function that makes geometry returns a new mesh object linked to the scene. Profiles are lists of
tuples. Modifier helpers apply their modifier by default, so the result is plain mesh. When a part
is finished, ModelGen gives every edge sharper than 30 degrees a small bevel automatically.
"""

import math

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector

PART_KEY = "mg_part"
ATTACH_KEY = "mg_attached_to"
NO_BEVEL_KEY = "mg_no_bevel"


# --- units and point lists ----------------------------------------------------------

def mm(value):
    """Millimetres to metres."""
    return value / 1000.0


def cm(value):
    """Centimetres to metres."""
    return value / 100.0


def arc_points(radius, start_deg, end_deg, n=16, center=(0.0, 0.0)):
    """Points (x, y) on a circular arc, from start_deg to end_deg inclusive."""
    cx, cy = center
    out = []
    for i in range(n + 1):
        a = math.radians(start_deg + (end_deg - start_deg) * i / n)
        out.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
    return out


def circle_points(radius, n=64, center=(0.0, 0.0)):
    """n points (x, y) around a circle, counter-clockwise, without repeating the first."""
    cx, cy = center
    return [(cx + radius * math.cos(2 * math.pi * i / n), cy + radius * math.sin(2 * math.pi * i / n))
            for i in range(n)]


def ellipse_points(rx, ry, n=64, center=(0.0, 0.0)):
    """n points (x, y) around an ellipse, counter-clockwise."""
    cx, cy = center
    return [(cx + rx * math.cos(2 * math.pi * i / n), cy + ry * math.sin(2 * math.pi * i / n))
            for i in range(n)]


def rounded_rect_points(width, depth, radius, corner_segments=8, center=(0.0, 0.0)):
    """Points (x, y) around a rectangle with rounded corners, counter-clockwise."""
    cx, cy = center
    r = max(0.0, min(radius, width / 2 - 1e-6, depth / 2 - 1e-6))
    hx, hy = width / 2 - r, depth / 2 - r
    out = []
    for (ox, oy, start) in ((hx, hy, 0), (-hx, hy, 90), (-hx, -hy, 180), (hx, -hy, 270)):
        for i in range(corner_segments + 1):
            a = math.radians(start + 90 * i / corner_segments)
            out.append((cx + ox + r * math.cos(a), cy + oy + r * math.sin(a)))
    return _dedupe(out)


def superellipse_points(width, depth, exponent=4.0, n=64, center=(0.0, 0.0)):
    """Points (x, y) on a superellipse ("squircle"): exponent 2 is an ellipse, higher is boxier."""
    cx, cy = center
    out = []
    for i in range(n):
        t = 2 * math.pi * i / n
        c, s = math.cos(t), math.sin(t)
        x = (width / 2) * math.copysign(abs(c) ** (2 / exponent), c)
        y = (depth / 2) * math.copysign(abs(s) ** (2 / exponent), s)
        out.append((cx + x, cy + y))
    return out


def smooth_points(points, samples=8, closed=False):
    """A smooth Catmull-Rom curve through the given points (2D or 3D), `samples` points per span.
    Good for organic revolve profiles: give a few key (radius, z) points, get a smooth outline."""
    pts = [tuple(p) for p in points]
    if len(pts) < 3:
        return pts
    dim = len(pts[0])
    n = len(pts)
    spans = n if closed else n - 1
    out = []
    for i in range(spans):
        p0 = pts[(i - 1) % n] if (closed or i > 0) else pts[0]
        p1 = pts[i % n]
        p2 = pts[(i + 1) % n]
        p3 = pts[(i + 2) % n] if (closed or i + 2 < n) else pts[-1]
        for s in range(samples):
            t = s / samples
            t2, t3 = t * t, t * t * t
            out.append(tuple(0.5 * ((2 * p1[k]) + (-p0[k] + p2[k]) * t
                                    + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
                                    + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3) for k in range(dim)))
    if not closed:
        out.append(pts[-1])
    return out


def _dedupe(points, eps=1e-9):
    out = []
    for p in points:
        if not out or any(abs(a - b) > eps for a, b in zip(p, out[-1])):
            out.append(p)
    if len(out) > 1 and all(abs(a - b) <= eps for a, b in zip(out[0], out[-1])):
        out.pop()
    return out


# --- scene and object basics ----------------------------------------------------------

def reset_scene():
    """Remove every object and unused datablock, leaving an empty scene."""
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.curves, bpy.data.materials, bpy.data.cameras,
                 bpy.data.lights, bpy.data.textures, bpy.data.node_groups, bpy.data.images):
        for block in list(coll):
            if block.users == 0:
                coll.remove(block)


def _link(obj):
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _from_bmesh(bm, name):
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return _link(bpy.data.objects.new(name, me))


def _anchor_offset(bm, anchor):
    """Move the geometry so the given point of its bounding box sits at the origin."""
    if anchor in (None, "origin"):
        return
    zs = [v.co.z for v in bm.verts]
    xs = [v.co.x for v in bm.verts]
    ys = [v.co.y for v in bm.verts]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    if anchor == "center":
        off = Vector((-cx, -cy, -(min(zs) + max(zs)) / 2))
    elif anchor == "bottom":
        off = Vector((-cx, -cy, -min(zs)))
    elif anchor == "top":
        off = Vector((-cx, -cy, -max(zs)))
    else:
        raise ValueError(f"unknown anchor {anchor!r}: use center, bottom, top or origin")
    bmesh.ops.translate(bm, vec=off, verts=bm.verts[:])


def _finish(bm, name, location, rotation_deg, anchor):
    _anchor_offset(bm, anchor)
    obj = _from_bmesh(bm, name)
    place(obj, location=location, rotation_deg=rotation_deg)
    return obj


def place(obj, location=None, rotation_deg=None, scale=None):
    """Set an object's location (metres), rotation (XYZ Euler in degrees) and scale."""
    if location is not None:
        obj.location = Vector(location)
    if rotation_deg is not None:
        obj.rotation_euler = Euler([math.radians(a) for a in rotation_deg], "XYZ")
    if scale is not None:
        obj.scale = Vector(scale) if not isinstance(scale, (int, float)) else Vector((scale,) * 3)
    return obj


def instance(obj, location=None, rotation_deg=None):
    """A copy of the object that shares its mesh (for repeated parts such as legs)."""
    copy = obj.copy()
    for coll in obj.users_collection:
        coll.objects.link(copy)
    return place(copy, location=location, rotation_deg=rotation_deg)


def attach(obj, to):
    """Declare that obj touches or is embedded in the part named `to` (so it is not an overlap)."""
    names = [n for n in (obj.get(ATTACH_KEY) or "").split(",") if n]
    if to not in names:
        names.append(to)
    obj[ATTACH_KEY] = ",".join(names)
    return obj


def bounds(objs):
    """World-space bounding box (min, max) of an object or a list of objects, modifiers included."""
    if not isinstance(objs, (list, tuple)):
        objs = [objs]
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    lo = Vector((math.inf,) * 3)
    hi = Vector((-math.inf,) * 3)
    for obj in objs:
        ev = obj.evaluated_get(dg)
        for corner in ev.bound_box:
            p = ev.matrix_world @ Vector(corner)
            lo = Vector(map(min, lo, p))
            hi = Vector(map(max, hi, p))
    return lo, hi


def size(objs):
    """World-space size (x, y, z) of an object or a list of objects."""
    lo, hi = bounds(objs)
    return hi - lo


def drop_to_floor(objs):
    """Move the objects together so the lowest point sits exactly on Z = 0."""
    if not isinstance(objs, (list, tuple)):
        objs = [objs]
    lo, _ = bounds(objs)
    for obj in objs:
        obj.location.z -= lo.z
    return objs


def apply_transform(obj):
    """Bake location, rotation and scale into the mesh, leaving the object at the identity."""
    obj.data = obj.data.copy() if obj.data.users > 1 else obj.data
    # matrix_basis follows location, rotation and scale at once; matrix_world lags until the scene
    # updates, so a placement set just before this call would be lost
    obj.data.transform(obj.matrix_basis)
    obj.matrix_basis = Matrix.Identity(4)
    return obj


def apply_modifiers(obj):
    """Apply every modifier on the object (the result is plain mesh). An Armature modifier (a
    character's skin) is kept and left out of the result: the mesh stays in its rest pose, its
    vertex groups kept, still driven by the skeleton."""
    if obj.type == "MESH" and not obj.modifiers:
        return obj
    skins = [m for m in obj.modifiers if m.type == "ARMATURE"] if obj.type == "MESH" else []
    if skins and len(skins) == len(obj.modifiers):
        return obj
    shown = [m.show_viewport for m in skins]
    for m in skins:
        m.show_viewport = False
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=True, depsgraph=dg)
    for m, on in zip(skins, shown):
        m.show_viewport = on
    if obj.type == "MESH":
        old = obj.data
        for m in [m for m in obj.modifiers if m.type != "ARMATURE"]:
            obj.modifiers.remove(m)
        obj.data = me
        if old.users == 0:
            bpy.data.meshes.remove(old)
        return obj
    new = _link(bpy.data.objects.new(obj.name, me))  # a curve or text becomes a mesh object
    new.matrix_world = obj.matrix_world.copy()
    for key in obj.keys():
        new[key] = obj[key]
    bpy.data.objects.remove(obj, do_unlink=True)
    return new


def merge_by_distance(obj, distance=1e-6):
    """Weld vertices closer than `distance` (closes seams) and make the normals consistent."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=distance)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return obj


def close_small_holes(obj, max_sides=32, max_share=0.05):
    """Fill the small holes in a mesh (open boundary loops of at most max_sides edges) so it is a
    closed solid, and make the normals consistent. A mesh that is mostly open (more than max_share
    of its edges on a boundary: a deliberate thin sheet) is left as it is."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    boundary = [e for e in bm.edges if e.is_boundary]
    if boundary and len(boundary) <= max_share * len(bm.edges):
        bmesh.ops.holes_fill(bm, edges=boundary, sides=max_sides)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        bm.to_mesh(obj.data)
        obj.data.update()
    bm.free()
    return obj


def delete(obj):
    """Remove an object from the scene."""
    bpy.data.objects.remove(obj, do_unlink=True)


# --- primitives -------------------------------------------------------------------

def box(size, location=(0, 0, 0), rotation_deg=None, name="box", anchor="center"):
    """A box of size (x, y, z). anchor: "center", "bottom" (bottom face centred on the origin) or "top"."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector(size), verts=bm.verts[:])
    return _finish(bm, name, location, rotation_deg, anchor)


def cylinder(radius, height, segments=64, location=(0, 0, 0), rotation_deg=None, name="cylinder",
             anchor="center", radius_top=None):
    """A cylinder along Z; give radius_top for a tapered cylinder (a truncated cone)."""
    return cone(radius, radius if radius_top is None else radius_top, height, segments, location,
                rotation_deg, name, anchor)


def cone(radius_bottom, radius_top, height, segments=64, location=(0, 0, 0), rotation_deg=None,
         name="cone", anchor="center"):
    """A cone or truncated cone along Z (radius_top may be 0)."""
    bm = bmesh.new()
    kwargs = dict(cap_ends=True, cap_tris=False, segments=segments, depth=height)
    try:
        bmesh.ops.create_cone(bm, radius1=radius_bottom, radius2=radius_top, **kwargs)
    except TypeError:  # Blender before 3.0 named these by diameter
        bmesh.ops.create_cone(bm, diameter1=radius_bottom, diameter2=radius_top, **kwargs)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-7)
    return _finish(bm, name, location, rotation_deg, anchor)


def sphere(radius, segments=64, rings=32, location=(0, 0, 0), name="sphere", anchor="center"):
    """A UV sphere."""
    bm = bmesh.new()
    try:
        bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=rings, radius=radius)
    except TypeError:
        bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=rings, diameter=radius)
    return _finish(bm, name, location, None, anchor)


def torus(major_radius, minor_radius, major_segments=64, minor_segments=24, location=(0, 0, 0),
          rotation_deg=None, name="torus"):
    """A torus lying flat around Z."""
    profile = [(major_radius + x, y) for x, y in circle_points(minor_radius, minor_segments)]
    obj = revolve(profile, segments=major_segments, closed=True, name=name)
    return place(obj, location=location, rotation_deg=rotation_deg)


# --- profile methods ----------------------------------------------------------------

def revolve(profile, segments=96, closed=False, angle_deg=360.0, name="revolve", location=(0, 0, 0)):
    """Spin a profile of (radius, z) points around the Z axis, like a lathe.
    Open profile: put the first and last points on the axis (radius 0) to get a closed solid,
    e.g. a mug is outer wall up, rim, inner wall down, inner floor to the axis.
    closed=True joins the last point back to the first (a ring section, as for a torus)."""
    pts = _dedupe([(float(r), float(z)) for r, z in profile])
    bm = bmesh.new()
    verts = [bm.verts.new((r, 0.0, z)) for r, z in pts]
    edges = [bm.edges.new((verts[i], verts[i + 1])) for i in range(len(verts) - 1)]
    if closed:
        edges.append(bm.edges.new((verts[-1], verts[0])))
    full = abs(angle_deg - 360.0) < 1e-6
    kwargs = dict(geom=verts + edges, cent=(0, 0, 0), axis=(0, 0, 1), dvec=(0, 0, 0),
                  angle=math.radians(angle_deg), steps=segments, use_duplicate=False)
    try:
        bmesh.ops.spin(bm, use_merge=full, **kwargs)
    except TypeError:
        bmesh.ops.spin(bm, **kwargs)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-7)  # welds the seam and the poles
    bmesh.ops.dissolve_degenerate(bm, dist=1e-9, edges=bm.edges[:])
    obj = _from_bmesh(bm, name)
    return place(obj, location=location)


def extrude(profile, depth, name="extrude", plane="XY", center=False, location=(0, 0, 0),
            rotation_deg=None):
    """Extrude a closed 2D profile of (a, b) points by `depth`.
    plane "XY": profile is (x, y), extruded up +Z. "XZ": profile is (x, z), extruded along -Y.
    "YZ": profile is (y, z), extruded along +X. center=True centres the thickness on the origin."""
    pts = _dedupe([(float(a), float(b)) for a, b in profile])
    area = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
               for i in range(len(pts)))
    if area < 0:
        pts.reverse()  # counter-clockwise, so the cap faces the right way
    bm = bmesh.new()
    face = bm.faces.new([bm.verts.new((a, b, 0.0)) for a, b in pts])
    ret = bmesh.ops.extrude_face_region(bm, geom=[face])
    top = [g for g in ret["geom"] if isinstance(g, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, vec=(0, 0, depth), verts=top)
    if center:
        bmesh.ops.translate(bm, vec=(0, 0, -depth / 2), verts=bm.verts[:])
    if plane == "XZ":
        bmesh.ops.rotate(bm, cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(90), 3, "X"), verts=bm.verts[:])
    elif plane == "YZ":
        bmesh.ops.rotate(bm, cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(90), 3, "X"), verts=bm.verts[:])
        bmesh.ops.rotate(bm, cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(90), 3, "Z"), verts=bm.verts[:])
    elif plane != "XY":
        raise ValueError("plane must be XY, XZ or YZ")
    obj = _from_bmesh(bm, name)
    return place(obj, location=location, rotation_deg=rotation_deg)


def loft(sections, closed=True, cap=True, name="loft"):
    """Skin a surface through cross-sections. Each section is a list of (x, y, z) points, all with
    the same count, in the same winding. Caps close the first and last sections."""
    bm = bmesh.new()
    rings = [[bm.verts.new(tuple(p)) for p in section] for section in sections]
    n = len(rings[0])
    if any(len(r) != n for r in rings):
        raise ValueError("every loft section needs the same number of points")
    for a, b in zip(rings, rings[1:]):
        for j in range(n if closed else n - 1):
            k = (j + 1) % n
            bm.faces.new((a[j], a[k], b[k], b[j]))
    if cap and closed:
        bm.faces.new(list(reversed(rings[0])))
        bm.faces.new(rings[-1])
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-7)
    return _from_bmesh(bm, name)


def section(points2d, z):
    """Lift 2D (x, y) points to a loft section at height z."""
    return [(x, y, z) for x, y in points2d]


def sweep(path, radius=None, profile=None, radii=None, closed=False, caps=True, smooth=True,
          profile_segments=24, resolution=16, twist_deg=0.0, name="sweep"):
    """Sweep a cross-section along a 3D path of (x, y, z) points (handles, rails, tubes, frames).
    radius: a round section of that radius. profile: a closed list of (x, y) points instead.
    radii: one scale factor per path point (tapering). smooth=True passes a smooth curve through
    the points; smooth=False keeps straight segments with sharp corners."""
    curve = bpy.data.curves.new(name + "_curve", "CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = resolution
    curve.use_fill_caps = caps and not closed
    if smooth:
        spline = curve.splines.new("BEZIER")
        spline.bezier_points.add(len(path) - 1)
        for i, p in enumerate(path):
            bp = spline.bezier_points[i]
            bp.co = Vector(p)
            bp.handle_left_type = bp.handle_right_type = "AUTO"
            bp.radius = radii[i] if radii else 1.0
            bp.tilt = math.radians(twist_deg) * i / max(1, len(path) - 1)
    else:
        spline = curve.splines.new("POLY")
        spline.points.add(len(path) - 1)
        for i, p in enumerate(path):
            spline.points[i].co = (*p, 1.0)
            spline.points[i].radius = radii[i] if radii else 1.0
    spline.use_cyclic_u = closed
    helpers_to_remove = []
    if profile:
        pcurve = bpy.data.curves.new(name + "_profile", "CURVE")
        pcurve.dimensions = "2D"
        psp = pcurve.splines.new("POLY")
        pts = _dedupe(profile)
        psp.points.add(len(pts) - 1)
        for i, (x, y) in enumerate(pts):
            psp.points[i].co = (x, y, 0.0, 1.0)
        psp.use_cyclic_u = True
        pobj = _link(bpy.data.objects.new(name + "_profile", pcurve))
        helpers_to_remove.append(pobj)
        curve.bevel_mode = "OBJECT"
        curve.bevel_object = pobj
    else:
        curve.bevel_mode = "ROUND"
        curve.bevel_depth = radius or 0.005
        curve.bevel_resolution = max(1, profile_segments // 4 - 1)
    cobj = _link(bpy.data.objects.new(name, curve))
    obj = apply_modifiers(cobj)  # curve to mesh
    for h in helpers_to_remove:
        bpy.data.objects.remove(h, do_unlink=True)
    obj.name = name
    return merge_by_distance(obj, 1e-6)


# --- modifiers ----------------------------------------------------------------------

def boolean(target, cutters, operation="DIFFERENCE", keep_cutters=False):
    """Boolean the target with one cutter or a list: "DIFFERENCE" (holes, grooves), "UNION" or
    "INTERSECT". Uses the exact solver; the cutters are deleted unless keep_cutters=True."""
    if not isinstance(cutters, (list, tuple)):
        cutters = [cutters]
    for cutter in cutters:
        mod = target.modifiers.new("boolean", "BOOLEAN")
        mod.operation = operation
        mod.object = cutter
        try:
            mod.solver = "EXACT"
        except (AttributeError, TypeError):
            pass
        cutter.hide_render = True
        apply_modifiers(target)
        if not keep_cutters:
            bpy.data.objects.remove(cutter, do_unlink=True)
    return target


def union(target, others):
    """Fuse other solids into the target (a single closed shell)."""
    return boolean(target, others, "UNION")


def bevel(obj, width, segments=3, angle_deg=30.0, apply=True):
    """Round the edges sharper than angle_deg with a bevel of `width` metres."""
    mod = obj.modifiers.new("bevel", "BEVEL")
    mod.width = width
    mod.segments = segments
    mod.limit_method = "ANGLE"
    mod.angle_limit = math.radians(angle_deg)
    mod.use_clamp_overlap = True
    obj[NO_BEVEL_KEY] = 1  # it already has its own bevel
    return apply_modifiers(obj) if apply else obj


def mirror(obj, axis="X", merge=True, apply=True):
    """Mirror the object across its own origin on axis "X", "Y" or "Z" (or a combination "XY")."""
    mod = obj.modifiers.new("mirror", "MIRROR")
    mod.use_axis = ["X" in axis, "Y" in axis, "Z" in axis]
    mod.use_mirror_merge = merge
    mod.merge_threshold = 1e-4
    return apply_modifiers(obj) if apply else obj


def array(obj, count, offset, apply=True):
    """`count` copies of the object, each moved by `offset` (x, y, z) metres from the previous."""
    mod = obj.modifiers.new("array", "ARRAY")
    mod.count = count
    mod.use_relative_offset = False
    mod.use_constant_offset = True
    mod.constant_offset_displace = Vector(offset)
    return apply_modifiers(obj) if apply else obj


def radial_array(obj, count, axis="Z", center=(0, 0, 0)):
    """`count` copies of the object spaced evenly around an axis through `center`, joined into one."""
    apply_modifiers(obj)
    pieces = [obj]
    c = Vector(center)
    for i in range(1, count):
        copy = obj.copy()
        copy.data = obj.data.copy()
        _link(copy)
        rot = Matrix.Rotation(2 * math.pi * i / count, 4, axis)
        copy.matrix_world = Matrix.Translation(c) @ rot @ Matrix.Translation(-c) @ obj.matrix_world
        pieces.append(copy)
    return join(pieces, obj.name)


def subdivide(obj, levels=2, apply=True):
    """Catmull-Clark subdivision for soft, rounded forms (start from a low-poly cage)."""
    mod = obj.modifiers.new("subdivision", "SUBSURF")
    mod.levels = levels
    mod.render_levels = levels
    return apply_modifiers(obj) if apply else obj


def solidify(obj, thickness, offset=-1.0, apply=True):
    """Give a surface a wall thickness (offset -1 grows inward, 1 outward, 0 both ways)."""
    mod = obj.modifiers.new("solidify", "SOLIDIFY")
    mod.thickness = thickness
    mod.offset = offset
    mod.use_even_offset = True
    return apply_modifiers(obj) if apply else obj


def displace(obj, strength=0.002, scale=20.0, kind="CLOUDS", subdivide_first=0, apply=True):
    """Push the surface in and out with a noise texture (hammered metal, rough stone, bark).
    scale is in features per metre. kind: "CLOUDS", "VORONOI" or "MARBLE". Needs dense geometry:
    subdivide_first adds simple subdivision levels before displacing."""
    if subdivide_first:
        sub = obj.modifiers.new("dense", "SUBSURF")
        sub.subdivision_type = "SIMPLE"
        sub.levels = sub.render_levels = subdivide_first
    tex = bpy.data.textures.new(obj.name + "_displace", kind)
    tex.noise_scale = 1.0 / max(scale, 1e-6)
    mod = obj.modifiers.new("displace", "DISPLACE")
    mod.texture = tex
    mod.texture_coords = "LOCAL"
    mod.strength = strength
    mod.mid_level = 0.5
    return apply_modifiers(obj) if apply else obj


def smooth(obj, angle_deg=30.0):
    """Smooth shading, keeping edges sharper than angle_deg crisp."""
    me = obj.data
    if hasattr(me, "set_sharp_from_angle"):  # Blender 4.1 and later
        me.shade_smooth()
        me.set_sharp_from_angle(angle=math.radians(angle_deg))
    else:
        for p in me.polygons:
            p.use_smooth = True
        if hasattr(me, "use_auto_smooth"):
            me.use_auto_smooth = True
            me.auto_smooth_angle = math.radians(angle_deg)
    return obj


def flat_faces_flat(obj):
    """Weighted normals (by face area, keeping sharp edges): big flat faces shade perfectly flat
    and the small bevel faces take the bend. Without this, smooth shading after a boolean cut
    smears light and dark streaks across flat panels (the long thin triangles the cut leaves)."""
    mod = obj.modifiers.new("flat_faces", "WEIGHTED_NORMAL")
    mod.mode = "FACE_AREA"
    mod.keep_sharp = True
    return apply_modifiers(obj)


def join(objs, name):
    """Join objects (any transforms, modifiers and materials) into one mesh object at the origin."""
    objs = [apply_modifiers(o) for o in objs]
    bm = bmesh.new()
    materials = []
    for obj in objs:
        tmp = obj.data.copy()
        tmp.transform(obj.matrix_world)
        before = len(bm.faces)
        bm.from_mesh(tmp)
        bm.faces.ensure_lookup_table()
        slot_map = []
        for slot in obj.material_slots:
            if slot.material is None:
                slot_map.append(0)
                continue
            if slot.material not in materials:
                materials.append(slot.material)
            slot_map.append(materials.index(slot.material))
        for f in bm.faces[before:]:
            f.material_index = slot_map[f.material_index] if f.material_index < len(slot_map) else 0
        bpy.data.meshes.remove(tmp)
    keys = {k: objs[0][k] for k in objs[0].keys()} if objs else {}
    for obj in objs:
        bpy.data.objects.remove(obj, do_unlink=True)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for mat in materials:
        me.materials.append(mat)
    out = _link(bpy.data.objects.new(name, me))
    for k, v in keys.items():
        out[k] = v
    return out


# --- used by ModelGen when a part is done (builders do not call this) ----------------

def _min_dimension(obj):
    dims = [d for d in obj.dimensions if d > 1e-6]
    return min(dims) if dims else 0.0


def finalize_part(objs, name, bevel_width=0.0015):
    """Turn what a part's build() returned into one clean mesh object named after the part:
    curves become meshes, every sharp edge gets a small bevel, the pieces are joined and welded,
    shading is smoothed by angle, and the object is tagged as the part."""
    if objs is None:
        raise ValueError(f"build() for part '{name}' returned nothing; return the object you made")
    if not isinstance(objs, (list, tuple)):
        objs = [objs]
    meshes = []
    for obj in objs:
        obj = apply_modifiers(obj) if obj.type != "MESH" else obj
        if obj.type != "MESH":
            continue
        if not obj.get(NO_BEVEL_KEY) and not any(m.type == "BEVEL" for m in obj.modifiers) and bevel_width > 0:
            width = min(bevel_width, 0.1 * _min_dimension(obj)) if _min_dimension(obj) else bevel_width
            mod = obj.modifiers.new("edge_bevel", "BEVEL")
            mod.width = width
            mod.segments = 2
            mod.limit_method = "ANGLE"
            mod.angle_limit = math.radians(30)
            mod.use_clamp_overlap = True
        meshes.append(apply_modifiers(obj))
    if not meshes:
        raise ValueError(f"build() for part '{name}' made no mesh")
    if any(m.get("mg_asset") for m in meshes):
        # a ready-made model placed as a part: its mesh, normals and textures stay exactly as they are
        part = meshes[0] if len(meshes) == 1 else join(meshes, name)
        part.name = name
        part[PART_KEY] = name
        return part
    part = join(meshes, name)
    merge_by_distance(part, 1e-6)
    close_small_holes(part)
    smooth(part)
    flat_faces_flat(part)
    part[PART_KEY] = name
    if NO_BEVEL_KEY in part.keys():
        del part[NO_BEVEL_KEY]
    return part
