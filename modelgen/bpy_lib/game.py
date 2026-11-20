"""The game-ready export of a finished model. Runs inside Blender only, on an opened model.blend
that is never saved again: everything here changes the scene only for the files it writes.

    <name>_game.glb        the asset: within the triangle limit, pivot at the centre of its base,
                           real-world scale (metres), parts named after the plan, motion included
    <name>_LOD1.glb, ...   lower-detail versions (half and a fifth of the triangles by default)
    <name>_collision.glb   one box the size of the model, for a simple collider

The other exports (GLB, FBX, OBJ, STL) stay as the agents built the model, at full detail.
"""

import os

import bmesh
import bpy
from mathutils import Vector

from . import helpers, motion, rig

KEEP_BELOW = 120  # an object with fewer triangles than this is never reduced: it has none to spare


def _triangles(obj):
    me = obj.data
    me.calc_loop_triangles()
    return len(me.loop_triangles)


def _total(objs):
    return sum(_triangles(o) for o in objs)


def _own_mesh(obj):
    """Copies of a part share one mesh; give this object its own before changing it."""
    if obj.data.users > 1:
        obj.data = obj.data.copy()


def _weld(obj, distance=1e-5):
    """Join the vertices that sit on top of each other. An imported model's triangles are often all
    separate (split along texture seams), and edges can only be collapsed where triangles share
    them; the texture coordinates stay, they belong to the faces' corners."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=distance)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def _reduce(objs, target):
    """Collapse edges until the objects together have about `target` triangles. Small objects are
    left alone and the rest share the cut evenly, so a button keeps its shape while a dense body
    gives up the most. Returns the triangle count reached."""
    total = _total(objs)
    if total <= target:
        return total
    small = [o for o in objs if _triangles(o) < KEEP_BELOW]
    kept = sum(_triangles(o) for o in small)
    big = [o for o in objs if o not in small]
    spare = max(1, sum(_triangles(o) for o in big))
    ratio = max(0.02, min(1.0, (target - kept) / spare))
    for obj in big:
        _own_mesh(obj)
        _weld(obj)
        mod = obj.modifiers.new("mg_lod", "DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = ratio
        mod.use_collapse_triangulate = True
        helpers.apply_modifiers(obj)
    return _total(objs)


def _animated():
    return bool(motion.motion_objects()) or any(o.animation_data and o.animation_data.action
                                                for o in bpy.context.scene.objects)


def _centre_pivot(objs):
    """Move the model so the centre of its base is the origin. Skipped when the model moves: its
    keyframes hold positions, and it was built about its own centre line anyway."""
    lo, hi = helpers.bounds(objs)
    shift = Vector((-(lo.x + hi.x) / 2, -(lo.y + hi.y) / 2, -lo.z))
    if shift.length < 1e-6 or _animated() or rig._skeletons(objs):
        return [0.0, 0.0, 0.0]  # a character is built standing at the origin; moving its skin off its skeleton would tear it
    roots = [o for o in objs if o.parent is None or o.parent not in objs]
    for obj in roots:
        obj.matrix_world.translation = obj.matrix_world.translation + shift
    bpy.context.view_layer.update()
    return [round(v, 5) for v in shift]


def _clean_names(objs):
    """Each object and its mesh named after its part: wheel, wheel_2, wheel_3..."""
    count = {}
    for obj in sorted(objs, key=lambda o: o.name):
        part = str(obj.get(helpers.PART_KEY) or obj.name).split(".")[0]
        count[part] = count.get(part, 0) + 1
        name = part if count[part] == 1 else f"{part}_{count[part]}"
        obj.name = name
        if obj.data.users == 1:
            obj.data.name = name


def _export(path, objs):
    rig._select(objs)
    rig._export_gltf(path)
    if not os.path.isfile(path):
        raise RuntimeError(f"the exporter wrote no file for {os.path.basename(path)}")
    return path


def _collision_box(name, lo, hi):
    size = hi - lo
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector((max(size.x, 1e-4), max(size.y, 1e-4), max(size.z, 1e-4))), verts=bm.verts[:])
    bmesh.ops.translate(bm, vec=(lo + hi) / 2, verts=bm.verts[:])
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def export_game(out_dir, basename="model", budget=12000, lods=(0.5, 0.2)):
    """Write the game files of the model in the scene into out_dir and return what was made:
    {"files": {...}, "triangles": {"built", "game", "LOD1", ...}, "budget", "reduced", "size",
    "pivot_shift"}. budget is the triangle limit of the asset; lods are the lower-detail versions
    as shares of the asset's triangles."""
    os.makedirs(out_dir, exist_ok=True)
    objs = rig.model_objects()
    if not objs:
        raise RuntimeError("there is no model in the scene to export")
    for obj in objs:
        helpers.apply_modifiers(obj)
    rig._plain_materials()
    built = _total(objs)
    shift = _centre_pivot(objs)
    _clean_names(objs)
    lo, hi = helpers.bounds(objs)
    files, triangles = {}, {"built": built}
    triangles["game"] = _reduce(objs, int(budget))
    files["game"] = _export(os.path.join(out_dir, f"{basename}_game.glb"), objs)
    for i, share in enumerate(lods, 1):
        triangles[f"LOD{i}"] = _reduce(objs, max(12, int(triangles["game"] * float(share))))
        files[f"LOD{i}"] = _export(os.path.join(out_dir, f"{basename}_LOD{i}.glb"), objs)
    box = _collision_box(f"{basename}_collision", lo, hi)
    for o in bpy.context.scene.objects:
        o.select_set(o is box)
    bpy.context.view_layer.objects.active = box
    path = os.path.join(out_dir, f"{basename}_collision.glb")
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True, export_animations=False)
    files["collision"] = path
    return {"files": files, "triangles": triangles, "budget": int(budget), "reduced": triangles["game"] < built,
            "size": [round(v, 4) for v in (hi - lo)], "pivot_shift": shift}
