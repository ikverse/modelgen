"""Raw geometric measurements of the model in the scene. Runs inside Blender only.

Returns plain numbers and lists; modelgen\\tools\\checks.py judges them against the brief.
A part's mesh can hold several separate pieces ("islands"). Islands connect when they intersect
or come within the contact tolerance; anything not connected, through other islands, to the
lowest pieces on the floor is a floater.
"""

import math
import random

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import helpers

SAMPLE_VERTS = 400


class _Island:
    def __init__(self, obj, index, verts, polys):
        self.obj, self.index = obj, index
        self.verts, self.polys = verts, polys
        used = sorted({i for p in polys for i in p})
        self.points = [verts[i] for i in used]
        self.lo = Vector(map(min, *self.points)) if len(self.points) > 1 else self.points[0].copy()
        self.hi = Vector(map(max, *self.points)) if len(self.points) > 1 else self.points[0].copy()
        self.bvh = BVHTree.FromPolygons(verts, polys, epsilon=0.0)

    @property
    def part(self):
        return self.obj.get(helpers.PART_KEY) or self.obj.name

    def near(self, other, tol):
        return all(self.lo[k] - tol <= other.hi[k] and other.lo[k] - tol <= self.hi[k] for k in range(3))

    def sample(self):
        if len(self.points) <= SAMPLE_VERTS:
            return self.points
        return random.Random(7).sample(self.points, SAMPLE_VERTS)


def _mesh_data(obj, dg):
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    mw = ev.matrix_world
    verts = [mw @ v.co for v in me.vertices]
    polys = [tuple(p.vertices) for p in me.polygons]
    edge_faces = {}
    for p in me.polygons:
        for key in p.edge_keys:
            edge_faces[key] = edge_faces.get(key, 0) + 1
    loose = sum(1 for e in me.edges if tuple(sorted(e.vertices)) not in edge_faces)
    ev.to_mesh_clear()
    return verts, polys, edge_faces, loose


def _islands(obj, verts, polys):
    parent = list(range(len(verts)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for p in polys:
        root = find(p[0])
        for v in p[1:]:
            r = find(v)
            if r != root:
                parent[r] = root
    groups = {}
    for p in polys:
        groups.setdefault(find(p[0]), []).append(p)
    return [_Island(obj, i, verts, ps) for i, ps in enumerate(groups.values())]


def _attached(a, b):
    names_a = set(filter(None, (a.obj.get(helpers.ATTACH_KEY) or "").split(",")))
    names_b = set(filter(None, (b.obj.get(helpers.ATTACH_KEY) or "").split(",")))
    return b.part in names_a or b.obj.name in names_a or a.part in names_b or a.obj.name in names_b


def _vec(v):
    return [round(float(x), 5) for x in v]


def _outline_modifiers(objs):
    """Inverted-hull outlines (a solidify with flipped normals) are ink, not geometry: a 6 mm shell
    would otherwise push the model below the floor and add pieces to every part."""
    return [m for o in objs for m in o.modifiers
            if m.type == "SOLIDIFY" and m.use_flip_normals and m.show_viewport]


def measure_scene(floor_tolerance=0.002, contact_tolerance=0.0015, objects=None):
    objs = objects or [o for o in bpy.context.scene.objects if o.type == "MESH" and o.get(helpers.PART_KEY)]
    hidden = _outline_modifiers(objs)
    for m in hidden:
        m.show_viewport = False
    try:
        bpy.context.view_layer.update()
        return _measure(bpy.context.evaluated_depsgraph_get(), objs, floor_tolerance, contact_tolerance)
    finally:
        for m in hidden:
            m.show_viewport = True
        bpy.context.view_layer.update()


def _thickness(islands, samples=160):
    """Local wall thickness at sampled faces: a ray cast inward from each face along its reversed
    normal, to the opposite side of the same solid. Returns (min, median, max) in metres, or None."""
    rng = random.Random(11)
    found = []
    for isl in islands:
        polys = isl.polys if len(isl.polys) <= samples else rng.sample(isl.polys, samples)
        for p in polys:
            pts = [isl.verts[i] for i in p]
            if len(pts) < 3:
                continue
            n = (pts[1] - pts[0]).cross(pts[2] - pts[0])
            if n.length < 1e-12:
                continue
            n.normalize()
            c = sum(pts, Vector()) / len(pts)
            hit, _, _, dist = isl.bvh.ray_cast(c - n * 1e-5, -n)
            if hit is not None and dist and dist > 1e-5:
                found.append(dist)
    if not found:
        return None
    found.sort()
    return [round(found[0], 5), round(found[len(found) // 2], 5), round(found[-1], 5)]


def part_report(part, contact_tolerance=0.0015, floor_tolerance=0.002):
    """One part measured in the finished scene: its real size, piece count, wall thickness (min,
    median, max), the parts it touches, and the gap to its nearest other parts. For a builder
    checking its part in place and for the critic before it asks for a change in size."""
    all_objs = [o for o in bpy.context.scene.objects if o.type == "MESH" and o.get(helpers.PART_KEY)]
    hidden = _outline_modifiers(all_objs)
    for m in hidden:
        m.show_viewport = False
    try:
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        mine, others = [], {}
        for obj in all_objs:
            verts, polys, _, _ = _mesh_data(obj, dg)
            if not polys:
                continue
            isl = _islands(obj, verts, polys)
            if (obj.get(helpers.PART_KEY) or obj.name) == part:
                mine += isl
            else:
                others.setdefault(obj.get(helpers.PART_KEY) or obj.name, []).extend(isl)
    finally:
        for m in hidden:
            m.show_viewport = True
        bpy.context.view_layer.update()
    if not mine:
        return {"part": part, "found": False,
                "error": f"no object of part '{part}' in the model; parts: {sorted(others)}"}
    lo = Vector(map(min, *(i.lo for i in mine))) if len(mine) > 1 else mine[0].lo
    hi = Vector(map(max, *(i.hi for i in mine))) if len(mine) > 1 else mine[0].hi
    gaps = []
    for name, isls in others.items():
        best = None
        for a in mine:
            for b in isls:
                for p in a.sample()[:120]:
                    hit = b.bvh.find_nearest(p)
                    if hit[0] is not None and (best is None or hit[3] < best):
                        best = hit[3]
                for p in b.sample()[:120]:
                    hit = a.bvh.find_nearest(p)
                    if hit[0] is not None and (best is None or hit[3] < best):
                        best = hit[3]
        if best is not None:
            gaps.append((best, name))
    gaps.sort()
    attach = set()
    for isl in mine:
        attach |= set(filter(None, (isl.obj.get(helpers.ATTACH_KEY) or "").split(",")))
    # a piece of this part touching nothing else (no other part, no other piece of it, not the floor)
    loose = 0
    for isl in mine:
        on_floor = isl.lo.z <= floor_tolerance
        near_other = any(isl.near(b, contact_tolerance) and (isl.bvh.overlap(b.bvh) or any(
            b.bvh.find_nearest(p, contact_tolerance)[0] is not None for p in isl.sample()[:120]))
            for isls in others.values() for b in isls)
        near_own = any(o is not isl and isl.near(o, contact_tolerance) for o in mine)
        if not (on_floor or near_other or near_own):
            loose += 1
    return {
        "part": part, "found": True,
        "size": _vec(hi - lo), "min": _vec(lo), "max": _vec(hi),
        "pieces": len(mine), "pieces_touching_nothing": loose,
        "wall_thickness_min_median_max": _thickness(mine),
        "touches": sorted(n for d, n in gaps if d <= contact_tolerance),
        "nearest_parts_gap_m": [{"part": n, "gap_m": round(d, 4)} for d, n in gaps[:4]],
        "attaches_to": sorted(attach),
        "gap_to_attached_m": {n: round(d, 4) for d, n in gaps if n in attach},
    }


# --- the mesh check: meshing parts measured while they move ---------------------------------

RAYS = [Vector(v).normalized() for v in ((0.5773, 0.5774, 0.5775), (-0.31, 0.83, -0.46), (0.71, -0.42, -0.56))]


def _crossings_odd(bvh, p, ray):
    count, origin = 0, p.copy()
    for _ in range(200):
        hit = bvh.ray_cast(origin, ray)
        if hit[0] is None:
            break
        count += 1
        origin = hit[0] + ray * 1e-7
    return count % 2 == 1


def _inside(bvh, p):
    """Whether a point is inside a closed solid: an odd number of crossings along a ray, by the
    vote of three rays (one ray can graze an edge or run along two nearly touching faces)."""
    return sum(_crossings_odd(bvh, p, ray) for ray in RAYS) >= 2


def _solid(obj, dg):
    """One part as a solid to test against: its world vertices and a BVH of its real triangles
    (Blender's own triangulation: a concave many-sided face split as a fan would reach outside it)."""
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    mw = ev.matrix_world
    verts = [mw @ v.co for v in me.vertices]
    me.calc_loop_triangles()
    tris = [tuple(t.vertices) for t in me.loop_triangles]
    ev.to_mesh_clear()
    bvh = BVHTree.FromPolygons(verts, tris, epsilon=0.0)
    lo = Vector(map(min, *verts)) if len(verts) > 1 else verts[0]
    hi = Vector(map(max, *verts)) if len(verts) > 1 else verts[0]
    return {"verts": verts, "bvh": bvh, "lo": lo, "hi": hi}


def _boxes_meet(a, b, pad):
    return all(a["lo"][k] - pad <= b["hi"][k] and b["lo"][k] - pad <= a["hi"][k] for k in range(3))


def _depth_and_gap(a, b, reach):
    """How deep the two solids cut into each other (the deepest vertex of one inside the other) and
    how close they come where they do not (the nearest vertex of one to the other's surface, looked
    for within reach), in metres."""
    depth, gap = 0.0, None
    hits = a["bvh"].overlap(b["bvh"])
    for one, other in ((a, b), (b, a)):
        for p in one["verts"]:
            if not all(other["lo"][k] - reach <= p[k] <= other["hi"][k] + reach for k in range(3)):
                continue
            found = other["bvh"].find_nearest(p, reach)
            if found[0] is None:
                continue
            if hits and _inside(other["bvh"], p):
                depth = max(depth, found[3])
            elif gap is None or found[3] < gap:
                gap = found[3]
    return depth, gap


def _angle_about(m0, m1, axis):
    """How far an object turned about an axis between two of its world matrices, radians (-pi..pi)."""
    q = (m1.to_3x3() @ m0.to_3x3().inverted()).to_quaternion()
    return 2 * math.atan2(Vector((q.x, q.y, q.z)).dot(axis), q.w)


def mesh_report(pairs, moments=12, reach=None):
    """The mesh check, measured on the model as it moves. pairs: from the mechanism sheet, each
    {"a", "b", "kind": "mesh" | "rack" | "shaft", "module_m", "centre_m", "ratio", "same_direction",
    "metres_per_radian"}. At `moments` times spread over the loop: how deep meshing teeth cut into
    each other and the gap between them; once: the centre distance; over every frame: the speed
    ratio and direction. Any other two parts that come near each other are checked for passing
    through each other ("clashes"); a gear and its shaft are fixed together and not."""
    scene = bpy.context.scene
    by = {}
    for o in scene.objects:
        if o.type == "MESH" and o.get(helpers.PART_KEY):
            by[o[helpers.PART_KEY]] = o
    first, last = scene.frame_start, scene.frame_end
    span = last + 1 - first
    # parts fixed or pinned together are not clashes; a piston in its cylinder still must not touch it
    joined = {frozenset((p["a"], p["b"])) for p in pairs if p["kind"] not in ("liner", "mount", "pin_mount")}
    tracked = {n: [] for n in by}
    samples = max(span, 24)
    for k in range(samples + 1):  # every frame (and more on a short loop): the matrices only
        t = first + span * k / samples
        scene.frame_set(int(math.floor(t)), subframe=t - math.floor(t))
        for n, o in by.items():
            tracked[n].append(o.matrix_world.copy())
    out = {"moments": moments, "pairs": [], "clashes": []}
    worst = {}
    clashes = {}
    for k in range(moments):
        t = first + round(span * k / moments)  # whole frames: a chain's links are keyed frame by frame
        scene.frame_set(int(t))
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        solids = {n: _solid(o, dg) for n, o in by.items()}
        for p in pairs:
            if p["kind"] in ("shaft", "drive", "train", "mount", "pin_mount") + ENGINE or p["a"] not in solids \
                    or p["b"] not in solids:
                continue
            m = p.get("module_m") or 0.002
            depth, gap = _depth_and_gap(solids[p["a"]], solids[p["b"]], reach or 2.5 * m)
            w = worst.setdefault((p["a"], p["b"]), {"depth": 0.0, "gap": 0.0, "at_depth": None, "at_gap": None})
            if depth > w["depth"]:
                w["depth"], w["at_depth"] = depth, k
            g = gap if gap is not None else 2.5 * m
            if depth <= 0 and g > w["gap"]:
                w["gap"], w["at_gap"] = g, k
        names = sorted(solids)
        for i, na in enumerate(names):
            for nb in names[i + 1:]:
                if frozenset((na, nb)) in joined:
                    continue
                sa, sb = solids[na], solids[nb]
                if not _boxes_meet(sa, sb, 0.0) or not sa["bvh"].overlap(sb["bvh"]):
                    continue
                depth, _ = _depth_and_gap(sa, sb, 1e-4)
                if depth > clashes.get((na, nb), (0.0, None))[0] or (na, nb) not in clashes:
                    clashes[(na, nb)] = (max(depth, clashes.get((na, nb), (0.0, None))[0]), k)
    scene.frame_set(first)
    bpy.context.view_layer.update()
    for p in pairs:
        if p["a"] not in by or p["b"] not in by:
            out["pairs"].append({**p, "found": False})
            continue
        if p["kind"] in ENGINE:
            out["pairs"].append(_engine_row(p, tracked[p["a"]], tracked[p["b"]], by.get(p["b"])))
            continue
        if p["kind"] in ("shaft", "mount", "pin_mount"):
            out["pairs"].append(_mount_row(p, tracked[p["a"]], tracked[p["b"]], _built_pins(by.get(p["b"])),
                                           _built_length(by.get(p["b"])) if p["kind"] == "shaft" else None))
            continue
        oa, ob = by[p["a"]], by[p["b"]]
        axis_a =(tracked[p["a"]][0].to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
        axis_b = (tracked[p["b"]][0].to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
        row = {"a": p["a"], "b": p["b"], "kind": p["kind"], "found": True}
        da = [_angle_about(tracked[p["a"]][i], tracked[p["a"]][i + 1], axis_a) for i in range(samples)]
        if p["kind"] == "rack":
            along = (tracked[p["b"]][0].to_3x3() @ Vector((1.0, 0.0, 0.0))).normalized()
            db = [(tracked[p["b"]][i + 1].translation - tracked[p["b"]][i].translation).dot(along)
                  for i in range(samples)]
        else:
            db = [_angle_about(tracked[p["b"]][i], tracked[p["b"]][i + 1], axis_b) for i in range(samples)]
        moved = sum(x * x for x in da)
        row["ratio"] = round(sum(x * y for x, y in zip(da, db)) / moved, 6) if moved > 1e-12 else None
        ca, cb = tracked[p["a"]][0].translation, tracked[p["b"]][0].translation
        if p["kind"] == "rack":
            up = (tracked[p["b"]][0].to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
            row["centre_m"] = round(abs((ca - cb).dot(up)), 6)
            row["metres_per_radian_expected"] = p.get("metres_per_radian")
        elif p["kind"] in ("mesh", "planet", "drive", "train"):
            if p["kind"] in ("mesh", "planet"):
                d = (cb - ca) - axis_a * (cb - ca).dot(axis_a)
                row["centre_m"] = round(d.length, 6)
            if p["kind"] != "planet":  # a planet rides round: its speed is the set's, checked as a train
                row["ratio_expected"] = p.get("ratio")
                row["same_direction_expected"] = p.get("same_direction")
        if p["kind"] != "shaft":
            row["centre_expected_m"] = p.get("centre_m")
            w = worst.get((p["a"], p["b"]), {})
            row["max_depth_m"] = round(w.get("depth", 0.0), 6)
            row["max_gap_m"] = round(w.get("gap", 0.0), 6)
            row["module_m"] = p.get("module_m")
            row["worst_moment"] = w.get("at_depth") if w.get("depth") else w.get("at_gap")
        out["pairs"].append(row)
    out["clashes"] = [{"a": a, "b": b, "max_depth_m": round(d, 6), "moment": k}
                      for (a, b), (d, k) in sorted(clashes.items())]
    return out


ENGINE = ("crank", "pin", "ring", "liner")


def _built_pins(obj):
    """The pins a built carrier really has (kits.carrier keeps their centres in its mg_gear), or None."""
    import json
    raw = obj.get("mg_gear") if obj is not None else None
    try:
        pins = json.loads(raw).get("pins") if raw else None
    except (TypeError, ValueError):
        return None
    return [Vector(v) for v in pins] if pins else None


def _built_length(obj):
    """A built shaft's length (kits.shaft keeps it in its mg_gear), metres, or None."""
    import json
    raw = obj.get("mg_gear") if obj is not None else None
    try:
        return float(json.loads(raw).get("length")) if raw else None
    except (TypeError, ValueError):
        return None


def _mount_row(p, ta, tb, built_pins=None, shaft_length=None):
    """A part carried by another, measured at every sampled moment: how far its own axis's origin
    strays from the line it must sit on (a shaft's axis, a carrier's pin, a bolt's axis), and how far
    its own axis tilts from that line. A planet is measured against the pins its carrier was really
    built with (the nearest of them), not where they were meant to be. A gear standing beside its
    shaft, or a planet off its pin, shows here even when nothing passes through anything."""
    locals_ = built_pins if p["kind"] == "pin_mount" and built_pins else [Vector(p.get("pin_local_m") or (0.0, 0.0, 0.0))]
    off, tilt, beyond = 0.0, 0.0, 0.0
    for ma, mb in zip(ta, tb):
        axis = (mb.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
        if shaft_length:  # the part's middle must be on the shaft, not past its end
            beyond = max(beyond, abs((ma.translation - mb.translation).dot(axis)) - shaft_length / 2)
        here = []
        for local in locals_:
            d = ma.translation - mb @ local
            here.append((d - axis * d.dot(axis)).length)
        off = max(off, min(here))
        own = (ma.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
        tilt = max(tilt, math.degrees(math.acos(max(-1.0, min(1.0, abs(own.dot(axis)))))))
    return {"a": p["a"], "b": p["b"], "kind": p["kind"], "found": True, "max_offset_m": round(off, 7),
            "max_tilt_deg": round(tilt, 4), "beyond_end_m": round(max(beyond, 0.0), 7)}


def _built_crankpin(obj, throw):
    """Where a built crankshaft's crankpin for `throw` really is (its own frame, metres), from the
    throws kits.crankshaft keeps in its mg_gear, or None."""
    import json
    raw = obj.get("mg_gear") if obj is not None else None
    try:
        g = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None
    if not g or "throw_angles" not in g or throw is None or throw >= len(g["throw_angles"]):
        return None
    a = math.radians(g["throw_angles"][throw])
    r = g["crank_radius"]
    return Vector((-r * math.sin(a), r * math.cos(a), g["throw_z"][throw]))


def _engine_row(p, ta, tb, b_obj=None):
    """An engine link measured over every sampled moment of the loop: a rod's big end on its crankpin
    ("crank") and its small end on the piston's pin ("pin"), as the largest distance apart; a piston
    in its cylinder ("liner"): how far off the axis it strays, how much room is left above the crown
    at the top and below the lowest ring at the bottom, and its travel."""
    row = {"a": p["a"], "b": p["b"], "kind": p["kind"], "found": True}
    if p["kind"] == "crank":  # the crankpin the crankshaft was really built with, not where it was meant to be
        pin = _built_crankpin(b_obj, p.get("throw")) or Vector(p["pin_local_m"])
        row["max_offset_m"] = round(max((ma.translation - mb @ pin).length for ma, mb in zip(ta, tb)), 6)
    elif p["kind"] == "pin":
        reach = Vector((0.0, p["length_m"], 0.0))
        row["max_offset_m"] = round(max((ma @ reach - mb.translation).length for ma, mb in zip(ta, tb)), 6)
    elif p["kind"] == "liner":
        off, heights = 0.0, []
        for ma, mb in zip(ta, tb):
            up = (mb.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
            d = ma.translation - mb.translation
            h = d.dot(up)
            off = max(off, (d - up * h).length)
            heights.append(h)
        row["max_off_axis_m"] = round(off, 6)
        row["top_room_m"] = round(p["top_m"] - (max(heights) + p["crown_m"]), 6)
        row["bottom_room_m"] = round(min(heights) + p["rings_low_m"] - p["bottom_m"], 6)
        row["stroke_m"] = round(max(heights) - min(heights), 6)
        row["stroke_expected_m"] = p["stroke_m"]
    return row


def _measure(dg, objs, floor_tolerance, contact_tolerance):
    parts, islands = [], []
    for obj in objs:
        verts, polys, edge_faces, loose = _mesh_data(obj, dg)
        if not polys:
            parts.append({"object": obj.name, "part": obj.get(helpers.PART_KEY) or obj.name, "faces": 0,
                          "verts": len(verts), "islands": 0, "non_manifold_edges": loose, "size": [0, 0, 0]})
            continue
        own = _islands(obj, verts, polys)
        islands += own
        lo = Vector(map(min, *(i.lo for i in own))) if len(own) > 1 else own[0].lo
        hi = Vector(map(max, *(i.hi for i in own))) if len(own) > 1 else own[0].hi
        parts.append({
            "object": obj.name,
            "part": obj.get(helpers.PART_KEY) or obj.name,
            "attached_to": [n for n in (obj.get(helpers.ATTACH_KEY) or "").split(",") if n],
            "verts": len(verts),
            "faces": len(polys),
            "triangles": sum(len(p) - 2 for p in polys),
            "asset": bool(obj.get("mg_asset")),  # a ready-made model placed as a part: not held to the gates
            "islands": len(own),
            "non_manifold_edges": sum(1 for n in edge_faces.values() if n != 2) + loose,
            "min": _vec(lo), "max": _vec(hi), "size": _vec(hi - lo),
        })
    if not islands:
        return {"parts": parts, "island_count": 0, "empty": True}

    lo = Vector(map(min, *(i.lo for i in islands))) if len(islands) > 1 else islands[0].lo
    hi = Vector(map(max, *(i.hi for i in islands))) if len(islands) > 1 else islands[0].hi

    links = {i: set() for i in range(len(islands))}
    overlaps = {}
    for a in range(len(islands)):
        for b in range(a + 1, len(islands)):
            ia, ib = islands[a], islands[b]
            if not ia.near(ib, contact_tolerance):
                continue
            hits = ia.bvh.overlap(ib.bvh)
            touching = bool(hits)
            if not touching:
                touching = any(ib.bvh.find_nearest(p, contact_tolerance)[0] is not None for p in ia.sample()) or \
                    any(ia.bvh.find_nearest(p, contact_tolerance)[0] is not None for p in ib.sample())
            if touching:
                links[a].add(b)
                links[b].add(a)
            if hits and ia.obj is not ib.obj:
                key = tuple(sorted((ia.obj.name, ib.obj.name)))
                entry = overlaps.setdefault(key, {"a": key[0], "b": key[1], "part_a": ia.part, "part_b": ib.part,
                                                  "attached": _attached(ia, ib), "pairs": 0})
                entry["pairs"] += len(hits)

    grounded = [i for i, isl in enumerate(islands) if isl.lo.z <= lo.z + floor_tolerance]
    reached, todo = set(grounded), list(grounded)
    while todo:
        for nxt in links[todo.pop()]:
            if nxt not in reached:
                reached.add(nxt)
                todo.append(nxt)
    floaters = [{"object": isl.obj.name, "part": isl.part, "island": isl.index,
                 "center": _vec((isl.lo + isl.hi) / 2), "size": _vec(isl.hi - isl.lo)}
                for i, isl in enumerate(islands) if i not in reached]

    return {
        "bbox": {"min": _vec(lo), "max": _vec(hi), "size": _vec(hi - lo)},
        "min_z": round(float(lo.z), 5),
        "parts": parts,
        "island_count": len(islands),
        "grounded_islands": len(grounded),
        "floaters": floaters,
        "overlaps": list(overlaps.values()),
    }
