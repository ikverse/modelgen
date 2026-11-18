"""A car made from a ready-made one: a downloaded base car (assets_cc0/, public domain) loaded,
reshaped within safe limits, repainted, given other wheels and exported as a game-ready .glb.

The base cars were modelled by people, so the lamps, grilles, doors and windows are already right;
this module only changes what can change without breaking them:

- shape: the wheelbase (the stretch between the wheel openings moves, the openings themselves do not
  bend), the front and rear overhangs, the roof height above the beltline, the width, the ride height;
- paint: the body's main colour, with the shading, stripes, lamps and trim left as they are;
- wheels: another car's wheels from the same library, scaled to this car's wheel size.

Every loaded car is put into one form first (see load): nose towards -Y, standing on z = 0, each
wheel its own object with its origin at its centre. Two libraries are understood: Grab3D (a body
with one painted picture, separate windows and wheels) and Quaternius (a body with a material per
colour, the rear wheels in one mesh).

The shape maths (warp_point, clamp_shape) is plain Python; everything else runs inside Blender.
"""

import json
import math

LIMITS = {"wheelbase": (-0.06, 0.08),   # change as a fraction of the base's wheelbase
          "front": (0.90, 1.10),        # front overhang, times
          "rear": (0.88, 1.14),         # rear overhang, times
          "roof": (0.93, 1.07),         # height above the beltline, times
          "width": (0.97, 1.04),        # times
          "lift": (-0.03, 0.05),        # metres the body rises; the wheels grow so their tops rise as much
          "wheel": (0.95, 1.05)}        # wheel size, times; the body rises or sinks with the tyres' tops
# a hypercar made of a sports car: lower, wider, longer between the wheels. Its wheels stay their
# size: larger ones would lift the body by as much as it is lowered
HYPER = {"wheelbase": (0.05, 0.10), "front": (0.98, 1.06), "rear": (0.95, 1.05), "roof": (0.86, 0.91),
         "width": (1.05, 1.08), "lift": (-0.045, -0.025), "wheel": (1.0, 1.01)}
PROFILES = {None: LIMITS, "hyper": HYPER}
NEUTRAL = {"wheelbase": 0.0, "front": 1.0, "rear": 1.0, "roof": 1.0, "width": 1.0, "lift": 0.0, "wheel": 1.0}
MIN_CLEARANCE = 0.07   # metres: a lowered body's lowest point stays this far above the ground
TYRE = (0.035, 0.04, 0.045)
LIGHTEST = 0.45        # a body is never thinned to less than this share of its triangles
ARCH = 1.5        # the wheel opening kept rigid: this many wheel radii either side of the axle
OVERHANG = 1.4    # the overhangs start this many wheel radii beyond the axle
BELT_UP = 0.03    # the roof scales from this far above the glass's bottom edge
NOT_PAINT = ("window", "glass", "black", "grey", "gray", "headlight", "taillight", "light", "tire", "tyre",
             "rim", "chrome", "trim", "hubcap")
PAINT_NEAR, PAINT_FAR = 0.10, 0.18   # a picture's pixel is paint within NEAR of the paint colour, not beyond FAR


def clamp_shape(shape, profile=None):
    """A shape with every number present and inside its profile's limits (LIMITS, or HYPER for
    "hyper"); a number not given is the neutral one, or the nearest the profile allows."""
    return {key: min(hi, max(lo, float((shape or {}).get(key, NEUTRAL[key]))))
            for key, (lo, hi) in PROFILES[profile].items()}


def rise(frame, shape):
    """How far a shape lifts the body: its own lift, plus what the tyres' tops gain from their size."""
    return shape["lift"] + 2.0 * frame["radius"] * (shape["wheel"] - 1.0)


def frame_of(front_y, rear_y, radius, belt):
    """What warp_point needs to know about a car: its axles (front_y < rear_y), wheel radius and the
    height of its beltline."""
    a0, a1 = front_y + ARCH * radius, rear_y - ARCH * radius
    if a1 - a0 < 0.05:                       # a very short car: the openings meet, stretch at the middle
        mid = 0.5 * (front_y + rear_y)
        a0, a1 = mid - 0.025, mid + 0.025
    return {"front_y": front_y, "rear_y": rear_y, "radius": radius, "belt": belt, "stretch": (a0, a1),
            "nose": front_y - OVERHANG * radius, "tail": rear_y + OVERHANG * radius}


def warp_point(p, frame, shape):
    """A body point (x, y, z) moved by a shape. The nose is towards -Y; the front axle stays put."""
    x, y, z = p
    if z > frame["belt"]:
        z = frame["belt"] + (z - frame["belt"]) * shape["roof"]
    if y < frame["nose"]:
        y = frame["nose"] + (y - frame["nose"]) * shape["front"]
    elif y > frame["tail"]:
        y = frame["tail"] + (y - frame["tail"]) * shape["rear"]
    a0, a1 = frame["stretch"]
    grow = shape["wheelbase"] * (frame["rear_y"] - frame["front_y"])
    y += grow * min(1.0, max(0.0, (y - a0) / (a1 - a0)))
    return (x * shape["width"], y, z + rise(frame, shape))


def warp_wheel(centre, frame, shape):
    """Where a wheel's centre goes, and how much the wheel grows."""
    x, y, z = centre
    grow = shape["wheelbase"] * (frame["rear_y"] - frame["front_y"])
    if y > 0.5 * (frame["front_y"] + frame["rear_y"]):
        y += grow
    # the tyre stands on the ground, so its top rises twice as much as its radius grows: half the lift
    # on top of its own size keeps the gap to the opening above it
    scale = shape["wheel"] + 0.5 * shape["lift"] / frame["radius"]
    return (x * shape["width"], y, z * scale), scale


def srgb_to_linear(c):
    return tuple(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c)


def linear_to_srgb(c):
    return tuple(v * 12.92 if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055 for v in c)


# ---------------------------------------------------------------- inside Blender


class Car:
    """A loaded base car in the one form: root (an empty), shell (body meshes, glass included), glass
    (separate window meshes, if any), wheels (one object each, origin at the centre)."""

    def __init__(self, root, shell, glass, wheels, library, path):
        self.root, self.shell, self.glass, self.wheels = root, shell, glass, wheels
        self.library, self.path = library, str(path)

    @property
    def objects(self):
        return [self.root] + self.shell + self.wheels


def _world(o):
    m = o.matrix_world
    return [m @ v.co for v in o.data.vertices]


def _set_world(o, pts):
    inv = o.matrix_world.inverted()
    for v, p in zip(o.data.vertices, pts):
        v.co = inv @ p
    o.data.update()


def _bounds(objs):
    from mathutils import Vector
    pts = [p for o in objs for p in _world(o)]
    return (Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts))),
            Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts))))


def _centre_origin(o):
    """Put an object's origin at the middle of its bounds, with no rotation or scale left on it."""
    import bpy
    from mathutils import Matrix
    pts = _world(o)
    lo, hi = _bounds([o])
    centre = (lo + hi) / 2
    o.parent = None
    o.matrix_world = Matrix.Translation(centre)
    bpy.context.view_layer.update()
    for v, p in zip(o.data.vertices, pts):
        v.co = p - centre
    o.data.update()


def _split_axle(o):
    """A mesh holding the wheels of both sides, as two objects."""
    import bmesh
    other = o.copy()
    other.data = o.data.copy()
    for coll in o.users_collection:
        coll.objects.link(other)
    for obj, keep_left in ((o, True), (other, False)):
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        m = obj.matrix_world
        gone = [f for f in bm.faces if ((m @ f.calc_center_median()).x > 0) != keep_left]
        bmesh.ops.delete(bm, geom=gone, context="FACES")
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
    o.name, other.name = o.name + "_l", o.name + "_r"
    return [o, other]


def _material_faces(o, words):
    """The polygons of a mesh whose material's name has one of the words."""
    idx = {i for i, s in enumerate(o.material_slots)
           if s.material and any(w in s.material.name.lower() for w in words)}
    return [f for f in o.data.polygons if f.material_index in idx]


def load(path):
    """Import a base car into an empty scene, in the one form."""
    import bpy
    from mathutils import Matrix
    bpy.ops.wm.read_factory_settings(use_empty=True)
    return add(path)


def add(path):
    """Import a base car into the current scene, in the one form."""
    import bpy
    from mathutils import Matrix
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    bpy.context.view_layer.update()
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == "MESH"]
    wheels = [o for o in meshes if "wheel" in o.name.lower()]
    shell = [o for o in meshes if o not in wheels]
    if not wheels or not shell:
        raise ValueError(f"{path}: no body or no wheels found")
    library = "grab3d" if any(o.name.lower().endswith("_body") for o in shell) else "quaternius"
    lamps = [(o.matrix_world @ f.center).y for o in shell for f in _material_faces(o, ("headlight",))]
    if lamps and sum(lamps) / len(lamps) > 0:        # nose towards +Y: turn the car round
        turn = Matrix.Rotation(math.pi, 4, "Z")
        for o in meshes:
            _set_world(o, [turn @ p for p in _world(o)])
    split = []
    for o in wheels:
        lo, hi = _bounds([o])
        split += _split_axle(o) if lo.x < -0.2 and hi.x > 0.2 and hi.x - lo.x > 1.0 else [o]
    wheels = split
    for o in wheels:
        _centre_origin(o)
    lo, _ = _bounds(shell + wheels)
    root = bpy.data.objects.new("car", None)
    bpy.context.scene.collection.objects.link(root)
    for o in shell + wheels:
        pts = _world(o)
        if o in wheels:
            o.location.z -= lo.z
        o.parent = root
        o.matrix_parent_inverse = Matrix.Identity(4)
        if o in shell:
            o.matrix_world = Matrix.Identity(4)
            bpy.context.view_layer.update()
            for v, p in zip(o.data.vertices, pts):
                v.co = (p.x, p.y, p.z - lo.z)
            o.data.update()
    for o in new:
        if o.type == "EMPTY":
            bpy.data.objects.remove(o)
    bpy.context.view_layer.update()
    glass = [o for o in shell if "window" in o.name.lower()]
    return Car(root, shell, glass, wheels, library, path)


def _wheel_radius(o):
    lo, hi = _bounds([o])
    return (hi.z - lo.z) / 2


def frame(car):
    """The car's frame for warp_point, measured from its wheels and glass."""
    ys = sorted(o.matrix_world.translation.y for o in car.wheels)
    mid = 0.5 * (ys[0] + ys[-1])
    front = [y for y in ys if y < mid] or ys[:1]
    rear = [y for y in ys if y >= mid] or ys[-1:]
    radius = sum(_wheel_radius(o) for o in car.wheels) / len(car.wheels)
    if car.glass:
        belt = min(p.z for o in car.glass for p in _world(o))
    else:
        zs = [(o.matrix_world @ o.data.vertices[i].co).z for o in car.shell
              for f in _material_faces(o, ("window", "glass")) for i in f.vertices]
        belt = min(zs) if zs else 2.0 * radius + 0.25
    return frame_of(sum(front) / len(front), sum(rear) / len(rear), radius, belt + BELT_UP)


def _paint_picture(o):
    """(material slot, picture node) of a body painted by a picture, or None."""
    for slot in o.material_slots:
        if slot.material and slot.material.node_tree:
            for node in slot.material.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image and "basecolor" in node.image.name.lower():
                    return slot, node
    return None


def _picture_paint(o, image):
    """The colour that covers most of a body's surface in its picture (sRGB), and the picture's
    pixels as an (n, 4) array."""
    import numpy as np
    w, h = image.size
    px = np.empty(w * h * 4, dtype=np.float32)
    image.pixels.foreach_get(px)
    px = px.reshape(-1, 4)
    me = o.data
    uv = np.empty(len(me.loops) * 2, dtype=np.float32)
    me.uv_layers.active.data.foreach_get("uv", uv)
    uv = uv.reshape(-1, 2)
    totals = np.empty(len(me.polygons), dtype=np.int32)
    areas = np.empty(len(me.polygons), dtype=np.float32)
    starts = np.empty(len(me.polygons), dtype=np.int32)
    me.polygons.foreach_get("loop_total", totals)
    me.polygons.foreach_get("area", areas)
    me.polygons.foreach_get("loop_start", starts)
    centre = np.add.reduceat(uv, starts, axis=0) / totals[:, None]      # each face's middle in the picture
    col = np.clip((centre[:, 0] % 1.0 * w).astype(np.int64), 0, w - 1)
    row = np.clip((centre[:, 1] % 1.0 * h).astype(np.int64), 0, h - 1)
    rgb = px[row * w + col, :3]
    normal = np.empty(len(me.polygons) * 3, dtype=np.float32)
    me.polygons.foreach_get("normal", normal)
    seen = normal.reshape(-1, 3)[:, 2] > -0.3                           # not the underside, which nobody sees
    bright = seen & (rgb.max(1) > 0.35)                                 # nor the dark chassis and shadows
    if not bright.any():
        bright = seen if seen.any() else np.ones(len(areas), dtype=bool)
    # faces vote by tint, not brightness: the picture has the shading baked in, so one paint is many shades
    tint = rgb - rgb.mean(1, keepdims=True)
    q = np.clip(np.rint(tint / 0.07).astype(np.int64) + 8, 0, 15)
    key = q[:, 0] * 256 + q[:, 1] * 16 + q[:, 2]
    weight = np.bincount(key[bright], weights=areas[bright], minlength=4096)
    top = int(weight.argmax())
    pick = bright & (key == top)
    paint = (rgb[pick] * areas[pick, None]).sum(0) / areas[pick].sum()
    return tuple(float(v) for v in paint), px


def paint_colour(car):
    """The car's main paint colour as sRGB, or None when it cannot be told."""
    best = None
    for o in car.shell:
        if o in car.glass:
            continue
        found = _paint_picture(o)
        if found:
            return _picture_paint(o, found[1].image)[0]
        slot = _paint_slot(o)
        if slot is not None:
            bsdf = o.material_slots[slot].material.node_tree.nodes.get("Principled BSDF")
            if bsdf:
                best = linear_to_srgb(tuple(bsdf.inputs["Base Color"].default_value[:3]))
    return best


def _paint_slot(o):
    """The material slot that covers most of a body and is not glass, lamps, tyres or trim."""
    area = {}
    for f in o.data.polygons:
        area[f.material_index] = area.get(f.material_index, 0.0) + f.area
    for i in sorted(area, key=lambda k: -area[k]):
        m = o.material_slots[i].material if i < len(o.material_slots) else None
        if m and not any(w in m.name.lower() for w in NOT_PAINT):
            return i
    return None


def repaint(car, colour):
    """Give the body a new main colour (sRGB). The shading in a painted picture stays, and so does
    everything that is not the paint colour: stripes, lamps, bumpers, a two-tone roof."""
    import bpy
    import numpy as np
    done = False
    for o in car.shell:
        if o in car.glass:
            continue
        found = _paint_picture(o)
        if found:
            slot, node = found
            old, px = _picture_paint(o, node.image)
            rgb = px[:, :3]
            lum, old_lum = rgb.mean(1), sum(old) / 3
            tint = np.linalg.norm((rgb - lum[:, None]) - (np.array(old, dtype=np.float32) - old_lum), axis=1)
            dist = np.sqrt(tint ** 2 + (0.5 * (lum - old_lum)) ** 2)
            w = np.clip((PAINT_FAR - dist) / (PAINT_FAR - PAINT_NEAR), 0.0, 1.0)[:, None]
            shade = (lum / max(old_lum, 1e-3))[:, None]
            new = np.clip(np.array(colour, dtype=np.float32)[None, :] * shade, 0.0, 1.0)
            px[:, :3] = rgb * (1 - w) + new * w
            src = node.image
            img = bpy.data.images.new(src.name + "_repaint", src.size[0], src.size[1], alpha=True)
            img.colorspace_settings.name = src.colorspace_settings.name
            img.pixels.foreach_set(px.ravel())
            img.update()
            img.pack()
            slot.material = slot.material.copy()
            for n in slot.material.node_tree.nodes:
                if n.type == "TEX_IMAGE" and n.image == src:
                    n.image = img
            done = True
            continue
        i = _paint_slot(o)
        if i is not None:
            mat = o.material_slots[i].material.copy()
            o.material_slots[i].material = mat
            bsdf = mat.node_tree.nodes.get("Principled BSDF")
            if bsdf:
                bsdf.inputs["Base Color"].default_value = (*srgb_to_linear(colour), 1.0)
                done = True
    return done


def reshape(car, shape, profile=None):
    """Move the body's points and the wheels by a shape (see LIMITS). A body is not lowered closer
    to the ground than MIN_CLEARANCE."""
    import bpy
    from mathutils import Matrix, Vector
    shape = clamp_shape(shape, profile)
    fr = frame(car)
    down = rise(fr, shape)
    if down < 0:
        lowest = _bounds(car.shell)[0].z
        shape["lift"] += max(down, min(0.0, MIN_CLEARANCE - lowest)) - down
    for o in car.shell:
        _set_world(o, [Vector(warp_point(tuple(p), fr, shape)) for p in _world(o)])
    for o in car.wheels:
        centre, scale = warp_wheel(tuple(o.matrix_world.translation), fr, shape)
        m = o.matrix_world.copy()
        m.translation = (0.0, 0.0, 0.0)
        o.matrix_world = Matrix.Translation(centre) @ Matrix.Scale(scale, 4) @ m
    bpy.context.view_layer.update()
    return shape


def _set_colour(mat, colour):
    """A material's colour set plainly (sRGB), any picture taken off it."""
    bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.node_tree else None
    if bsdf:
        base = bsdf.inputs["Base Color"]
        for link in list(base.links):
            mat.node_tree.links.remove(link)
        base.default_value = (*srgb_to_linear(colour), 1.0)


def _plain_wheels(car, finish):
    """Borrowed wheels in colours that go with any paint: the rims and hubcaps in finish["rim"],
    coloured trim with them, the tyres black. finish["whitewalls"] keeps a white tyre band."""
    rim, white = tuple(finish["rim"]), finish.get("whitewalls", False)
    copies = {}
    for o in car.wheels:
        for slot in o.material_slots:
            m = slot.material
            if m is None:
                continue
            name = m.name.lower().split(".")[0]
            if name not in copies:
                new = m.copy()
                bsdf = new.node_tree.nodes.get("Principled BSDF") if new.node_tree else None
                now = linear_to_srgb(tuple(bsdf.inputs["Base Color"].default_value[:3])) if bsdf else (0, 0, 0)
                if "tire" in name or "tyre" in name:
                    if not white:
                        _set_colour(new, TYRE)
                elif "hubcap" in name or "rim" in name:
                    _set_colour(new, rim)
                elif "white" in name:
                    if not white:
                        _set_colour(new, TYRE)
                elif max(now) - min(now) > 0.2:          # a coloured ring or cap
                    _set_colour(new, rim)
                copies[name] = new
            slot.material = copies[name]


def swap_wheels(car, donor_path, finish=None):
    """Give the car the wheels of another base car of the same library, scaled to its wheel size and,
    with a finish, recoloured plainly (see _plain_wheels). Returns False (and changes nothing) when
    the two do not have the same wheels to exchange."""
    import bpy
    from mathutils import Matrix
    donor = add(donor_path)
    try:
        if donor.library != car.library or len(donor.wheels) != len(car.wheels):
            return False
        mid = 0.5 * (min(o.matrix_world.translation.y for o in car.wheels)
                     + max(o.matrix_world.translation.y for o in car.wheels))
        dmid = 0.5 * (min(o.matrix_world.translation.y for o in donor.wheels)
                      + max(o.matrix_world.translation.y for o in donor.wheels))

        def corner(o, middle):
            t = o.matrix_world.translation
            return (t.x > 0.05) - (t.x < -0.05), t.y >= middle

        theirs = {corner(o, dmid): o for o in donor.wheels}
        if any(corner(o, mid) not in theirs for o in car.wheels):
            return False
        for o in car.wheels:
            d = theirs[corner(o, mid)]
            scale = _wheel_radius(o) / _wheel_radius(d)
            centre = o.matrix_world.translation.copy()
            turn = d.matrix_world.copy()
            turn.translation = (0.0, 0.0, 0.0)
            o.data = d.data.copy()
            o.matrix_world = Matrix.Translation(centre) @ Matrix.Scale(scale, 4) @ turn
        bpy.context.view_layer.update()
        if finish and car.library == "grab3d":
            _plain_wheels(car, finish)
        return True
    finally:
        for o in donor.objects:
            bpy.data.objects.remove(o)


def _triangles(o):
    o.data.calc_loop_triangles()
    return len(o.data.loop_triangles)


def triangles(car):
    return sum(_triangles(o) for o in car.shell + car.wheels)


def lighten(car, target):
    """Thin the body of a car with more than target triangles down towards it (never below LIGHTEST
    of the body). Returns True when the body was thinned."""
    import bpy
    total = triangles(car)
    if total <= target:
        return False
    body = max((o for o in car.shell if o not in car.glass), key=_triangles)
    mine = _triangles(body)
    ratio = max(LIGHTEST, (target - (total - mine)) / mine)
    mod = body.modifiers.new("mg_lighten", "DECIMATE")
    mod.ratio = ratio
    bpy.ops.object.select_all(action="DESELECT")
    body.select_set(True)
    bpy.context.view_layer.objects.active = body
    bpy.ops.object.modifier_apply(modifier=mod.name)
    return True


def describe(car):
    """The car in numbers (metres), for the catalogue and for checking a remix."""
    lo, hi = _bounds(car.shell + car.wheels)
    blo, bhi = _bounds(car.shell)
    fr = frame(car)
    xs = [abs(o.matrix_world.translation.x) for o in car.wheels]
    paint = paint_colour(car)
    arch = fr["radius"] * ARCH
    return {"library": car.library,
            "length": round(hi.y - lo.y, 3), "width": round(hi.x - lo.x, 3), "height": round(hi.z, 3),
            "wheelbase": round(fr["rear_y"] - fr["front_y"], 3), "track": round(2 * max(xs), 3),
            "front_overhang": round(fr["front_y"] - blo.y, 3), "rear_overhang": round(bhi.y - fr["rear_y"], 3),
            "body_half_width": round(max(abs(blo.x), abs(bhi.x)), 3),
            "wheel_radius": round(fr["radius"], 3), "wheels": len(car.wheels), "belt": round(fr["belt"], 3),
            "ground": round(lo.z, 3), "triangles": triangles(car),
            "wheel_centres": [[round(v, 3) for v in o.matrix_world.translation] for o in car.wheels],
            "wheel_top_gap": round(min(_arch_gap(car, o, arch) for o in car.wheels), 3),
            "paint": [round(v, 3) for v in paint] if paint else None}


def _arch_gap(car, wheel, reach):
    """How far the body's lowest point straight above a wheel's centre is above the tyre's top."""
    c = wheel.matrix_world.translation
    r = _wheel_radius(wheel)
    best = None
    for o in car.shell:
        for p in _world(o):
            if abs(p.y - c.y) < 0.06 and abs(p.x - c.x) < 0.12 and p.z > c.z:
                best = p.z if best is None else min(best, p.z)
    return (best - (c.z + r)) if best is not None else 0.0


def export(car, path):
    """The car as a .glb: nose towards -Y in Blender, which is +Z forward for a game engine."""
    import bpy
    bpy.ops.object.select_all(action="DESELECT")
    for o in car.objects:
        o.hide_set(False)
        o.select_set(True)
    bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB", use_selection=True)


def save(car, path):
    """The car as a .blend the project's renders can open: every mesh tagged as a part of the model
    (body, windows, wheel_1 ...), the pictures packed inside."""
    import bpy
    from .. import helpers
    for i, o in enumerate(car.wheels, 1):
        o[helpers.PART_KEY] = f"wheel_{i}"
    for o in car.shell:
        o[helpers.PART_KEY] = "windows" if o in car.glass else "body"
    try:
        bpy.ops.file.pack_all()
    except RuntimeError:
        pass  # nothing to pack
    bpy.ops.wm.save_as_mainfile(filepath=str(path))


def picture(car, path, size=(640, 420), samples=24):
    """A three-quarter front picture of the car on a plain floor."""
    import bpy
    from mathutils import Vector
    scene = bpy.context.scene
    made = []
    bpy.ops.mesh.primitive_plane_add(size=200)
    floor = bpy.context.object
    mat = bpy.data.materials.new("mg_floor")
    mat.use_nodes = True
    mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.42, 0.43, 0.45, 1)
    mat.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.9
    floor.data.materials.append(mat)
    world = bpy.data.worlds.new("mg_world")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.9, 0.92, 0.95, 1)
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.9
    scene.world = world
    az, el = math.radians(38), math.radians(16)
    sun = bpy.data.objects.new("mg_sun", bpy.data.lights.new("mg_sun", "SUN"))
    sun.data.energy, sun.data.angle = 3.0, math.radians(8)
    sun.rotation_euler = (math.radians(50), 0, az + math.radians(35))
    cam = bpy.data.objects.new("mg_cam", bpy.data.cameras.new("mg_cam"))
    for o in (sun, cam):
        scene.collection.objects.link(o)
    made += [floor, sun, cam]
    lo, hi = _bounds(car.shell + car.wheels)
    target = Vector((0.0, (lo.y + hi.y) / 2, hi.z * 0.45))
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = max(hi.y - lo.y, 3.0) * 1.12
    view = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
    cam.location = target + view * 40
    cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam
    scene.render.engine = "CYCLES"
    scene.cycles.samples = samples
    scene.cycles.use_denoising = True
    scene.render.resolution_x, scene.render.resolution_y = size
    scene.view_settings.view_transform = "Standard"
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    for o in made:
        bpy.data.objects.remove(o)


def remix(job):
    """One whole job: {"base", "shape", "profile", "paint", "wheels", "finish", "max_triangles", "glb",
    "blend", "picture"} (all but base optional). Prints the result as one MG_REMIX line and returns it."""
    car = load(job["base"])
    before = describe(car)
    out = {"base": job["base"], "before": before}
    if job.get("wheels"):
        out["wheels_swapped"] = swap_wheels(car, job["wheels"], job.get("finish"))
    out["shape"] = reshape(car, job.get("shape"), job.get("profile"))
    if job.get("paint"):
        out["repainted"] = repaint(car, tuple(job["paint"]))
    if job.get("max_triangles"):
        out["lightened"] = lighten(car, job["max_triangles"])
    out["after"] = describe(car)
    if job.get("glb"):
        export(car, job["glb"])
    if job.get("blend"):
        save(car, job["blend"])
    if job.get("picture"):
        picture(car, job["picture"])
    print("MG_REMIX " + json.dumps(out))
    return out


def catalogue(paths, pictures=None):
    """Every base car described; prints one MG_BASE line each. pictures: a folder for a picture of
    each, or None."""
    from pathlib import Path
    for path in paths:
        try:
            car = load(path)
            info = describe(car)
            if pictures:
                picture(car, str(Path(pictures) / (Path(path).stem + ".png")), size=(420, 280), samples=12)
        except Exception as exc:  # one bad file must not stop the rest
            info = {"error": f"{type(exc).__name__}: {exc}"}
        print("MG_BASE " + json.dumps({"file": str(path), **info}))
