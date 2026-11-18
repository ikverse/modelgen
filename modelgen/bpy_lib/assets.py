"""Ready-made models (glTF or GLB files) brought into the scene as one part. Runs inside Blender only.

`load` imports a model file and returns ONE mesh object with the model's own materials and
textures: standing on Z = 0, centred on the origin in X and Y, at its real-world size unless a
height or a size is asked for, optionally tinted. The object is tagged `mg_asset` so ModelGen
leaves its surface alone (no bevels, no re-shading, no new materials). `make` is the whole job of
turning one model file into a game asset: load, measure, export a .glb, save a .blend.
"""

import json
import os

import bmesh
import bpy
from mathutils import Matrix, Vector

from . import helpers as h

ASSET_KEY = "mg_asset"
TINT_NODE = "mg_tint"


def _linear(color):
    return tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in tuple(color)[:3])


def _import(path):
    """Import a .gltf or .glb; returns the objects it added."""
    before = set(bpy.data.objects)
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".gltf", ".glb"):
        raise ValueError(f"cannot read {ext or 'this'} file as a model: use .gltf or .glb")
    bpy.ops.import_scene.gltf(filepath=str(path))
    return [o for o in bpy.data.objects if o not in before]


def _as_one(objs, name):
    """The imported meshes joined into one object (materials and UVs kept); everything else removed."""
    bpy.context.view_layer.update()
    meshes = [o for o in objs if o.type == "MESH" and len(o.data.polygons)]
    if not meshes:
        raise ValueError("the model file holds no mesh")
    for o in meshes:  # out of any parent, keeping where it stands, so the empties can go
        world = o.matrix_world.copy()
        o.parent = None
        o.matrix_world = world
    for o in [o for o in objs if o not in meshes]:
        bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.view_layer.update()
    for o in meshes:
        o.animation_data_clear()
    return h.join(meshes, name)


def _stand(obj, height=None, size=None):
    """On the floor, centred in X and Y, scaled evenly to the height or to fit inside the size."""
    me = obj.data
    lo = Vector(map(min, *[v.co for v in me.vertices]))
    hi = Vector(map(max, *[v.co for v in me.vertices]))
    dims = hi - lo
    k = 1.0
    if height and dims.z > 1e-9:
        k = float(height) / dims.z
    elif size:
        fits = [float(want) / have for want, have in zip(size, dims) if want and have > 1e-9]
        if fits:
            k = min(fits)
    centre = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
    me.transform(Matrix.Scale(k, 4) @ Matrix.Translation(-centre))
    me.update()
    return obj


def _recoloured(image, color, strength=0.85):
    """A copy of a colour texture turned to `color` (picker sRGB): each pixel keeps how light or dark
    it was against the texture's usual tone, so the wear, grain and shading stay."""
    import numpy as np
    w, h_ = image.size
    px = np.empty(w * h_ * 4, dtype=np.float32)
    image.pixels.foreach_get(px)
    px = px.reshape(-1, 4)
    lum = px[:, 0] * 0.2126 + px[:, 1] * 0.7152 + px[:, 2] * 0.0722
    usual = max(float(np.median(lum)), 0.02)
    shade = np.clip(lum / usual, 0.0, 2.5)
    target = np.array(tuple(color)[:3], dtype=np.float32)
    px[:, :3] = np.clip(px[:, :3] * (1 - strength) + shade[:, None] * target * strength, 0.0, 1.0)
    out = bpy.data.images.new(image.name + "_tint", w, h_, alpha=True)
    out.colorspace_settings.name = image.colorspace_settings.name
    out.pixels.foreach_set(px.ravel())
    out.pack()
    return out


def apply_tint(obj, color):
    """Turn the model to a picker (sRGB) colour. A material with a colour texture gets a recoloured
    copy of it (light and dark, grain and wear kept), which a .glb carries like any texture; one
    without gets the colour itself. Tinting again starts from the model's own texture."""
    for i, mat in enumerate(obj.data.materials):
        if mat is None or not mat.use_nodes:
            continue
        if mat.users > 1:
            mat = obj.data.materials[i] = mat.copy()
        bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            continue
        base = bsdf.inputs["Base Color"]
        node = base.links[0].from_node if base.is_linked else None
        if node is not None and node.type == "TEX_IMAGE" and node.image is not None:
            source = bpy.data.images.get(node.get(TINT_NODE) or "") or node.image
            node[TINT_NODE] = source.name
            node.image = _recoloured(source, color)
        elif node is None:
            base.default_value = (*_linear(color), base.default_value[3])
    obj["mg_tint"] = list(color)[:3]
    return obj


def load(path, height=None, size=None, tint=None, name="asset"):
    """A ready-made model as one part. path: a .gltf or .glb file. height: scale it evenly to this
    height in metres; or size (x, y, z): scale it evenly to fit inside (a side may be None). tint: a
    picker colour its colour textures are turned to. Returns the object, standing on Z = 0 and centred
    in X and Y, keeping the model's own materials, textures and the way it faces."""
    obj = _as_one(_import(path), name)
    _stand(obj, height, size)
    if tint:
        apply_tint(obj, tint)
    obj[ASSET_KEY] = os.path.splitext(os.path.basename(str(path)))[0]
    obj[h.NO_BEVEL_KEY] = 1
    return obj


def triangles(obj):
    obj.data.calc_loop_triangles()
    return len(obj.data.loop_triangles)


def lighten(obj, max_triangles):
    """Thin the mesh to at most max_triangles (its UVs and materials stay); returns True when it did."""
    have = triangles(obj)
    if not max_triangles or have <= max_triangles:
        return False
    bm = bmesh.new()  # weld first: imported triangles are often all separate, and separate ones cannot collapse
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-5)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    mod = obj.modifiers.new("lighten", "DECIMATE")
    mod.ratio = max(0.01, float(max_triangles) / have)
    h.apply_modifiers(obj)
    return True


def measure(obj):
    me = obj.data
    lo = Vector(map(min, *[obj.matrix_world @ v.co for v in me.vertices]))
    hi = Vector(map(max, *[obj.matrix_world @ v.co for v in me.vertices]))
    return {"width": round(hi.x - lo.x, 4), "length": round(hi.y - lo.y, 4), "height": round(hi.z - lo.z, 4),
            "ground": round(lo.z, 5), "triangles": triangles(obj),
            "materials": len([m for m in me.materials if m]),
            "textured": any(n.type == "TEX_IMAGE" for m in me.materials if m and m.use_nodes
                            for n in m.node_tree.nodes)}


def make(job):
    """One model file made into a game asset. job: file, asset (its catalogue id), name, height or
    size, tint, max_triangles, glb, blend. Prints MG_ASSET and the result as JSON."""
    h.reset_scene()
    obj = load(job["file"], height=job.get("height"), size=job.get("size"), tint=job.get("tint"),
               name=job.get("name") or "asset")
    obj[ASSET_KEY] = job.get("asset") or obj[ASSET_KEY]
    obj[h.PART_KEY] = job.get("name") or "asset"
    lightened = lighten(obj, job.get("max_triangles"))
    after = measure(obj)
    if job.get("blend"):
        os.makedirs(os.path.dirname(job["blend"]), exist_ok=True)
        try:
            bpy.ops.file.pack_all()  # the .blend carries its textures, so it opens anywhere
        except RuntimeError:
            pass
        bpy.ops.wm.save_as_mainfile(filepath=job["blend"])
    for o in bpy.context.scene.objects:
        o.select_set(o is obj)
    bpy.context.view_layer.objects.active = obj
    os.makedirs(os.path.dirname(job["glb"]), exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=job["glb"], export_format="GLB", use_selection=True, export_apply=True,
                              export_extras=True)
    print("MG_ASSET " + json.dumps({"after": after, "lightened": lightened, "tinted": bool(job.get("tint"))}))
