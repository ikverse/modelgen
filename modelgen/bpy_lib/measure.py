"""Raw geometric measurements of the model in the scene. Runs inside Blender only.

Returns plain numbers and lists; modelgen\\tools\\checks.py judges them against the brief.
A part's mesh can hold several separate pieces ("islands"). Islands connect when they intersect
or come within the contact tolerance; anything not connected, through other islands, to the
lowest pieces on the floor is a floater.
"""

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
