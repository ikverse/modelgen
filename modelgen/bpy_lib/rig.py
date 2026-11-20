"""ModelGen's fixed photo studio and exporters. Runs inside Blender only.

The rig is the same for every model: a neutral curved backdrop, a three-point light set scaled to
the model, and a 50 mm camera framed on the model's bounding sphere, so renders of different
versions compare fairly. A car gets its own photo studio instead (add_car_lights): softboxes and
strips its glossy paint reflects, a darker glossy floor and a lower turntable.
"""

import json
import math
import os
import tempfile
from contextlib import contextmanager

import bmesh
import bpy
from mathutils import Matrix, Vector

from . import helpers, motion
from .shells.car import SHEET_KEY as CAR_SHEET_KEY

BACKDROP = "_mg_backdrop"
LENS_MM = 50.0
SENSOR_MM = 36.0
STUDIO = None  # "vehicle": light a model that is not a sheet-built car in the car studio (set_studio)


def set_studio(kind):
    """Choose the studio for the renders of this job: "vehicle" for the car studio, else the plain one."""
    global STUDIO
    STUDIO = kind or None


def model_objects():
    """The finished model: every mesh object tagged as a part."""
    return [o for o in bpy.context.scene.objects if o.type == "MESH" and o.get(helpers.PART_KEY)]


def frame(objs):
    lo, hi = helpers.bounds(objs)
    center = (lo + hi) / 2
    radius = max((hi - lo).length / 2, 1e-3)
    return center, radius, lo, hi


# --- render settings ----------------------------------------------------------------

def _enable_gpu(preference):
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except KeyError:
        return "CPU"
    order = ["OPTIX", "CUDA", "HIP", "ONEAPI"] if preference in ("auto", "", None) else [preference.upper()]
    if preference and preference.upper() == "CPU":
        return "CPU"
    for kind in order:
        try:
            prefs.compute_device_type = kind
        except TypeError:
            continue
        if hasattr(prefs, "refresh_devices"):
            prefs.refresh_devices()
        else:
            prefs.get_devices()
        found = [d for d in prefs.devices if d.type == kind]
        if found:
            for d in prefs.devices:
                d.use = d.type == kind
            return kind
    return "CPU"


def setup_render(size=768, samples=96, engine="CYCLES", device="auto"):
    scene = bpy.context.scene
    used = "CPU"
    if engine.upper() == "CYCLES":
        scene.render.engine = "CYCLES"
        used = _enable_gpu(device)
        scene.cycles.device = "GPU" if used != "CPU" else "CPU"
        scene.cycles.samples = samples
        scene.cycles.use_denoising = True
        if hasattr(scene.cycles, "use_adaptive_sampling"):
            scene.cycles.use_adaptive_sampling = True
    else:
        for name in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
            try:
                scene.render.engine = name
                break
            except TypeError:
                continue
    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    try:
        scene.view_settings.view_transform = "AgX"
    except TypeError:
        scene.view_settings.view_transform = "Filmic"
    return used


# --- studio -------------------------------------------------------------------------

def _gray_material(name, value, roughness=0.6):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = (value, value, value, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    return mat


def add_backdrop(center, radius, value=0.42, roughness=0.6):
    """A seamless curved backdrop (floor sweeping up into a back wall), mid-grey."""
    if BACKDROP in bpy.data.objects:
        return bpy.data.objects[BACKDROP]
    r = radius
    width = r * 16
    profile = [(-r * 10, 0.0), (r * 2.0, 0.0)]
    bend = r * 3.0
    for i in range(1, 13):
        a = math.radians(90 * i / 12)
        profile.append((r * 2.0 + bend * math.sin(a), bend * (1 - math.cos(a))))
    profile.append((r * 2.0 + bend, r * 12))
    bm = bmesh.new()
    left = [bm.verts.new((-width / 2, center.y + y, z - 0.0002)) for y, z in profile]
    right = [bm.verts.new((width / 2, center.y + y, z - 0.0002)) for y, z in profile]
    for i in range(len(profile) - 1):
        bm.faces.new((left[i], right[i], right[i + 1], left[i + 1]))
    me = bpy.data.meshes.new(BACKDROP)
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(BACKDROP, me)
    bpy.context.scene.collection.objects.link(obj)
    for p in me.polygons:
        p.use_smooth = True
    me.materials.append(_gray_material("_mg_backdrop_mat", value, roughness))
    return obj


def _area_light(name, power, size, location, target, size_y=None):
    """An area light aimed at target: square, or a rectangle size x size_y (a softbox or a strip)."""
    data = bpy.data.lights.new(name, "AREA")
    data.energy = power
    data.size = size
    if size_y is not None:
        data.shape = "RECTANGLE"
        data.size_y = size_y
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    direction = Vector(target) - Vector(location)
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    return obj


def _orbit(center, distance, azimuth_deg, elevation_deg):
    """A point at this azimuth (0 = in front, on -Y; positive turns towards +X) and elevation."""
    az, el = math.radians(azimuth_deg), math.radians(elevation_deg)
    return center + Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el))) * distance


def add_lights(center, radius):
    """Key light front-left above, softer fill front-right, rim light behind; world a dim grey.
    One stop darker exposure with AgX's punchy look (as in the car studio): measured on colour
    swatches, a lit face then renders close to its true colour (mid grey #808080 as about #868686);
    at the default exposure every colour came out about a stop too light and washed out (navy as
    light blue, maroon as pink), so the critic could never see a colour change land."""
    _true_colour_exposure()
    if "_mg_key" in bpy.data.objects:
        return
    k = (radius / 0.5) ** 2  # tuned for a half-metre model; light falls off with distance squared
    _area_light("_mg_key", 360 * k, radius * 2.5, _orbit(center, radius * 6, -45, 40), center)
    _area_light("_mg_fill", 120 * k, radius * 4.0, _orbit(center, radius * 6, 50, 15), center)
    _area_light("_mg_rim", 240 * k, radius * 2.0, _orbit(center, radius * 6, 160, 45), center)
    world = bpy.context.scene.world or bpy.data.worlds.new("_mg_world")
    bpy.context.scene.world = world
    world.use_nodes = True
    bg = next((n for n in world.node_tree.nodes if n.type == "BACKGROUND"), None)
    if bg:
        bg.inputs["Color"].default_value = (0.5, 0.5, 0.52, 1.0)
        bg.inputs["Strength"].default_value = 0.25


def _true_colour_exposure():
    view = bpy.context.scene.view_settings
    view.exposure = -1.0
    try:
        view.look = "AgX - Punchy"
    except TypeError:  # an older Blender without AgX looks
        pass


def is_car(objs):
    """Whether the model is a car built from a sheet (its body carries the sheet)."""
    return any(o.get(CAR_SHEET_KEY) for o in objs)


def add_car_lights(center, lo, hi):
    """A car photo studio. Glossy paint shows what it reflects, so the lights are long softboxes and
    strips that draw clean highlight lines along the roof, hood and doors, over a world that runs dark
    at the horizon to light overhead. One stop darker exposure with a punchy look keeps saturated
    paint its own colour instead of washing out to pastel."""
    if "_mg_car_top_l" in bpy.data.objects:
        return
    length, width, height = hi.y - lo.y, hi.x - lo.x, hi.z - lo.z
    k = (max(length, 1.0) / 4.6) ** 2  # tuned for a 4.6 m car
    c = Vector((center.x, center.y, 0.0))
    # two overhead strips set off to the sides, not one softbox straight above: the roof, hood and glass
    # show highlight lines instead of mirroring a white panel (which reads as pale paint), and a camera
    # looking straight down does not look into them
    for side in (-1, 1):
        _area_light(f"_mg_car_top_{'l' if side < 0 else 'r'}", 650 * k, length * 1.2,
                    c + Vector((side * width * 0.9, 0, height * 3.0)), c + Vector((0, 0, height * 0.6)), size_y=0.5)
    fills = [_area_light(f"_mg_car_side_{'l' if side < 0 else 'r'}", 900 * k, length * 1.3,
                         c + Vector((side * width * 2.2, 0, height * 1.6)), c + Vector((0, 0, height * 0.5)),
                         size_y=0.35) for side in (-1, 1)]
    fills.append(_area_light("_mg_car_front", 500 * k, width * 1.5, c + Vector((0, -length * 1.4, height * 1.2)),
                             c + Vector((0, 0, height * 0.4)), size_y=0.5))
    fills.append(_area_light("_mg_car_rear", 350 * k, width * 1.5, c + Vector((0, length * 1.4, height * 1.2)),
                             c + Vector((0, 0, height * 0.4)), size_y=0.5))
    # the side and end lights only light the car: mirrored in the side glass, roof edges and tailgate
    # glass they read as white holes, so only the overhead strips show in reflections
    for light in fills:
        light.visible_glossy = False
    world = bpy.context.scene.world or bpy.data.worlds.new("_mg_world")
    bpy.context.scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    bg = next((n for n in nt.nodes if n.type == "BACKGROUND"), None)
    if bg:
        coords = nt.nodes.new("ShaderNodeTexCoord")
        split = nt.nodes.new("ShaderNodeSeparateXYZ")
        up = nt.nodes.new("ShaderNodeMapRange")  # the view direction's height, -1..1, to 0..1
        up.inputs["From Min"].default_value = -1.0
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        ramp.color_ramp.elements[0].position = 0.48
        ramp.color_ramp.elements[0].color = (0.03, 0.03, 0.035, 1.0)
        ramp.color_ramp.elements[1].position = 0.75
        ramp.color_ramp.elements[1].color = (0.75, 0.76, 0.78, 1.0)
        nt.links.new(coords.outputs["Generated"], split.inputs[0])
        nt.links.new(split.outputs["Z"], up.inputs["Value"])
        nt.links.new(up.outputs["Result"], ramp.inputs["Fac"])
        nt.links.new(ramp.outputs["Color"], bg.inputs["Color"])
        bg.inputs["Strength"].default_value = 0.6
    view = bpy.context.scene.view_settings
    view.exposure = -1.0
    try:
        view.look = "AgX - Punchy"
    except TypeError:  # an older Blender without AgX looks
        pass


@contextmanager
def _plain_world():
    """The plain grey world for the duration (the wireframe set: dark lines need a light surround)."""
    world = bpy.context.scene.world
    bg = next((n for n in world.node_tree.nodes if n.type == "BACKGROUND"), None) if world and world.node_tree else None
    links = list(bg.inputs["Color"].links) if bg else []
    if not links:
        yield
        return
    source = links[0].from_socket
    world.node_tree.links.remove(links[0])
    old = tuple(bg.inputs["Color"].default_value)
    bg.inputs["Color"].default_value = (0.5, 0.5, 0.52, 1.0)
    try:
        yield
    finally:
        bg.inputs["Color"].default_value = old
        world.node_tree.links.new(source, bg.inputs["Color"])


def camera():
    cam = bpy.data.objects.get("_mg_camera")
    if cam is None:
        data = bpy.data.cameras.new("_mg_camera")
        data.lens = LENS_MM
        data.sensor_width = SENSOR_MM
        cam = bpy.data.objects.new("_mg_camera", data)
        bpy.context.scene.collection.objects.link(cam)
    bpy.context.scene.camera = cam
    return cam


def fit_distance(radius, margin=1.12):
    half_fov = math.atan((SENSOR_MM / 2) / LENS_MM)
    return radius / math.sin(half_fov) * margin


def _corners(lo, hi):
    return [Vector((x, y, z)) for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]


def aim_box(cam, lo, hi, azimuth_deg, elevation_deg, margin=1.08):
    """Aim at the centre of the box lo..hi from this direction, as close as keeps every corner of the
    box in the picture. A long or flat model then fills the frame at every angle; fitting its
    bounding sphere (aim) leaves it a third of the picture wide when seen end-on or from above."""
    center = (lo + hi) / 2
    radius = max((hi - lo).length / 2, 1e-3)
    out = (_orbit(center, 1.0, azimuth_deg, elevation_deg) - center).normalized()
    turn = (-out).to_track_quat("-Z", "Y")
    right, up = turn @ Vector((1, 0, 0)), turn @ Vector((0, 1, 0))
    tan_half = (SENSOR_MM / 2) / LENS_MM
    distance, half = radius * 0.2, 1e-4
    for corner in _corners(lo, hi):
        rel = corner - center
        across = max(abs(rel.dot(right)), abs(rel.dot(up)))
        half = max(half, across)
        distance = max(distance, across * margin / tan_half + rel.dot(out))
    if cam.data.type == "ORTHO":
        cam.data.ortho_scale = 2 * half * margin
        distance = fit_distance(radius)
    cam.location = center + out * distance
    cam.rotation_euler = turn.to_euler()
    cam.data.clip_start = radius * 0.01
    cam.data.clip_end = radius * 100
    _turn_backdrop(center, azimuth_deg)


def _car_like(objs):
    """A sheet-built car, or a job told to use the car studio: the car framing and lights."""
    return is_car(objs) or STUDIO == "vehicle"


def turntable_elevation(lo, hi):
    """How high the turntable camera stands for a model that is not a car: 20 degrees, or 35 for a
    flat scene (lower than a quarter of its footprint), which seen from 20 is a thin strip."""
    size = hi - lo
    return 35 if size.z < 0.25 * max(size.x, size.y) else 20


def aim(cam, center, radius, azimuth_deg, elevation_deg, margin=1.12):
    cam.location = _orbit(center, fit_distance(radius, margin), azimuth_deg, elevation_deg)
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam.data.clip_start = radius * 0.01
    cam.data.clip_end = radius * 100
    _turn_backdrop(center, azimuth_deg)


def _turn_backdrop(center, azimuth_deg):
    """Keep the curved backdrop behind the model as seen from the camera, so every angle has the
    same seamless background."""
    backdrop = bpy.data.objects.get(BACKDROP)
    if backdrop is None:
        return
    c = Vector((center.x, center.y, 0.0))
    backdrop.matrix_world = (Matrix.Translation(c) @ Matrix.Rotation(math.radians(azimuth_deg), 4, "Z")
                             @ Matrix.Translation(-c))


def stage():
    """Put the studio around the current model; returns (center, radius)."""
    objs = model_objects()
    if not objs:
        raise RuntimeError("there is no model in the scene to render")
    center, radius, lo, hi = frame(objs)
    if _car_like(objs):  # a darker, slightly glossy floor the car is reflected in
        add_backdrop(center, radius, 0.22, 0.3)
        add_car_lights(center, lo, hi)
    else:
        add_backdrop(center, radius)
        add_lights(center, radius)
    camera()
    return center, radius


def _render(path):
    bpy.context.scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    return path


# --- render sets ----------------------------------------------------------------------

VIEWS = {"front": (0, 5), "right": (90, 5), "back": (180, 5), "top": (0, 89.5), "three_quarter": (-35, 25),
         "back_three_quarter": (145, 25),
         # flat (orthographic) views and the low three-quarter views of a car: named by where the camera
         # stands, so "front_left" sees the front and the left (-X) side
         "side": (90, 0), "front_flat": (0, 0), "rear_flat": (180, 0), "top_flat": (0, 89.5),
         "front_left": (-50, 16), "front_right": (50, 16), "rear_left": (-140, 16), "rear_right": (140, 16),
         "left_flat": (-90, 0)}
ORTHO = {"side", "front_flat", "rear_flat", "top_flat", "left_flat"}


def render_views(out_dir, names, prefix="view"):
    center, radius = stage()
    cam = camera()
    objs = model_objects()
    sheet_car = is_car(objs)  # a sheet-built car keeps its fixed framing: its outlines are compared by scale
    _, _, lo, hi = frame(objs)
    paths = []
    try:
        for name in names:
            az, el = VIEWS[name]
            cam.data.type = "ORTHO" if name in ORTHO else "PERSP"
            if sheet_car:
                cam.data.ortho_scale = radius * 2.1
                aim(cam, center, radius, az, el)
            else:
                aim_box(cam, lo, hi, az, el)
            paths.append(_render(os.path.join(out_dir, f"{prefix}_{name}.png")))
    finally:
        cam.data.type = "PERSP"
    return paths


def render_turntable(out_dir, count=8, elevation_deg=None):
    """The turntable, from 20 degrees up (12 for a car, nearer a car photo's eye height)."""
    center, radius = stage()
    cam = camera()
    objs = model_objects()
    sheet_car = is_car(objs)
    _, _, lo, hi = frame(objs)
    if elevation_deg is None:
        elevation_deg = 12 if _car_like(objs) else turntable_elevation(lo, hi)
    paths = []
    for i in range(count):
        az = -35 + 360 * i / count
        if sheet_car:
            aim(cam, center, radius, az, elevation_deg)
        else:
            aim_box(cam, lo, hi, az, elevation_deg)
        paths.append(_render(os.path.join(out_dir, f"turntable_{i:02d}.png")))
    return paths


def _wire_material():
    mat = bpy.data.materials.get("_mg_wire") or bpy.data.materials.new("_mg_wire")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    wire = nt.nodes.new("ShaderNodeWireframe")
    wire.use_pixel_size = True
    wire.inputs["Size"].default_value = 1.0
    base = nt.nodes.new("ShaderNodeBsdfDiffuse")
    base.inputs["Color"].default_value = (0.8, 0.8, 0.8, 1.0)
    line = nt.nodes.new("ShaderNodeEmission")
    line.inputs["Color"].default_value = (0.02, 0.02, 0.02, 1.0)
    mix = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(wire.outputs["Fac"], mix.inputs["Fac"])
    nt.links.new(base.outputs["BSDF"], mix.inputs[1])
    nt.links.new(line.outputs["Emission"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    return mat


def render_wireframe(out_dir, angles=(-35, 145)):
    center, radius = stage()
    cam = camera()
    layer = bpy.context.view_layer
    backdrop = bpy.data.objects[BACKDROP]
    backdrop.hide_render = True
    layer.material_override = _wire_material()
    paths = []
    objs = model_objects()
    _, _, lo, hi = frame(objs)
    try:
        with _plain_world():
            for i, az in enumerate(angles):
                if is_car(objs):
                    aim(cam, center, radius, az, 25)
                else:
                    aim_box(cam, lo, hi, az, max(25, turntable_elevation(lo, hi)))
                paths.append(_render(os.path.join(out_dir, f"wireframe_{i:02d}.png")))
    finally:
        layer.material_override = None
        backdrop.hide_render = False
    return paths


def closeup_targets(count=3):
    """The objects the close-ups frame. A sheet-built car: its most detailed meshes. Any other model:
    one object per part, the most detailed parts first, and parts that span most of the model last
    (a close-up of the whole body is the turntable again), so the close-ups show different parts."""
    objs = model_objects()
    by_faces = sorted(objs, key=lambda o: len(o.data.polygons), reverse=True)
    if is_car(objs):
        seen, ranked = set(), []
        for obj in by_faces:
            if obj.data.name not in seen:
                seen.add(obj.data.name)
                ranked.append(obj)
        return ranked[:count]
    _, whole, _, _ = frame(objs)
    best = {}
    for obj in by_faces:
        best.setdefault(obj.get(helpers.PART_KEY) or obj.name, obj)
    ranked = list(best.values())
    small = [o for o in ranked if frame([o])[1] < 0.6 * whole]
    return (small + [o for o in ranked if o not in small])[:count]


def _inside_solid(scene, dg, point, reach):
    """Whether a point is inside a closed part of the model: a ray going straight up from it leaves
    through an odd number of faces."""
    backdrop = bpy.data.objects.get(BACKDROP)
    origin, crossings = Vector(point), 0
    for _ in range(64):
        hit, where, _, _, found, _ = scene.ray_cast(dg, origin, Vector((0, 0, 1)), distance=reach)
        if not hit:
            break
        if backdrop is None or found.name != backdrop.name:
            crossings += 1
        origin = where + Vector((0, 0, 1e-4))
    return crossings % 2 == 1


def _clear_view(obj, center, radius, whole, whole_radius, margin=1.25):
    """(azimuth, elevation) for a part's close-up: the first direction from which the part is really
    seen, so the camera never sits inside the model or behind another part (a wheel under a bus is
    seen from the road, a door from the street). Tried in order: the usual front-left, the part's
    own side of the model, then all the way round, lower before higher."""
    off = Vector((center.x - whole.x, center.y - whole.y, 0.0))
    side = math.degrees(math.atan2(off.x, -off.y)) if off.length > 0.05 * whole_radius else -30
    azimuths = [-30, side - 25, side + 25, side] + [side + turn for turn in (60, -60, 100, -100, 140, -140, 180)]
    scene = bpy.context.scene
    dg = bpy.context.evaluated_depsgraph_get()
    backdrop = bpy.data.objects.get(BACKDROP)
    distance = fit_distance(radius, margin)
    best, best_seen = (-30, 28), -1
    for elevation in (28, 50, 12):
        for azimuth in azimuths:
            eye = _orbit(center, distance, azimuth, elevation)
            if _inside_solid(scene, dg, eye, whole_radius * 4):
                continue
            seen, middle = 0, False
            # the centre and four points halfway to the part's edge: seen when the ray from the eye meets the part first
            right = (center - eye).cross(Vector((0, 0, 1))).normalized()
            up = right.cross(center - eye).normalized()
            for dx, dy in ((0, 0), (0.45, 0), (-0.45, 0), (0, 0.45), (0, -0.45)):
                target = center + right * dx * radius + up * dy * radius
                way = target - eye
                hit, _, _, _, found, _ = scene.ray_cast(dg, eye, way.normalized(), distance=way.length + radius)
                if hit and found is not None and found.name == obj.name:
                    seen += 1
                    middle = middle or (dx, dy) == (0, 0)
                elif not hit or (backdrop is not None and found is not None and found.name == backdrop.name):
                    seen += 0.5  # nothing in the way: the part is thin or hollow there
            if seen >= 4 or (middle and seen >= 3):  # a narrow part fills only the middle of its own frame
                return azimuth, elevation
            if seen > best_seen:
                best, best_seen = (azimuth, elevation), seen
    return best


def render_closeups(out_dir, count=3):
    """Close-ups of the parts with the most detail (most faces), each framed on its own bounds."""
    whole, whole_radius = stage()
    cam = camera()
    sheet_car = is_car(model_objects())
    paths = []
    for obj in closeup_targets(count):
        center, radius, _, _ = frame([obj])
        azimuth, elevation = (-30, 28) if sheet_car else _clear_view(obj, center, radius, whole, whole_radius)
        aim(cam, center, radius, azimuth, elevation, margin=1.25)
        name = obj.get(helpers.PART_KEY) or obj.name
        paths.append(_render(os.path.join(out_dir, f"closeup_{name}.png")))
    return paths


def render_in_context(out_dir, part, views=("three_quarter", "back_three_quarter")):
    """One part where it sits in the whole model, for its builder: two close-ups framed on the part
    with its neighbours around it (real materials), then the whole model from each view with the
    part painted bright orange so it is easy to find. Returns the image paths in that order."""
    center, radius = stage()
    cam = camera()
    mine = [o for o in model_objects() if (o.get(helpers.PART_KEY) or o.name) == part]
    if not mine:
        raise RuntimeError(f"part '{part}' is not in the model")
    paths = []
    p_center, p_radius, _, _ = frame(mine)
    p_radius = max(p_radius, radius * 0.08)
    for i, (az, el) in enumerate(((-30, 28), (150, 28))):
        aim(cam, p_center, p_radius, az, el, margin=1.9)
        paths.append(_render(os.path.join(out_dir, f"context_close_{i + 1}.png")))
    mat = bpy.data.materials.new("_mg_highlight")
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = (1.0, 0.32, 0.02, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.5
    for key in ("Emission Color", "Emission"):
        if key in bsdf.inputs:
            bsdf.inputs[key].default_value = (1.0, 0.32, 0.02, 1.0)
            break
    if "Emission Strength" in bsdf.inputs:
        bsdf.inputs["Emission Strength"].default_value = 0.15  # more would wash the orange out to peach
    for obj in mine:
        if not obj.material_slots:
            obj.data.materials.append(mat)
        for slot in obj.material_slots:
            slot.material = mat
    objs = model_objects()
    _, _, lo, hi = frame(objs)
    for name in views:
        az, el = VIEWS[name]
        if is_car(objs):
            aim(cam, center, radius, az, el)
        else:
            aim_box(cam, lo, hi, az, el)
        paths.append(_render(os.path.join(out_dir, f"context_{name}.png")))
    return paths


def _marker_balls():
    """A ball at every smoke or steam marker, for the frame strip only: grey for smoke, white for
    steam, as big as a puff at birth. (The page draws the real puffs; a render has none.)"""
    balls = []
    bpy.context.view_layer.update()  # a marker made in this same job has no world position until now
    for o in [o for o in bpy.context.scene.objects if o.get(motion.FX_KEY)]:
        kind = o[motion.FX_KEY]
        size = float(o.get(motion.FX_SIZE_KEY) or 0.1)
        ball = helpers.sphere(size / 2, segments=24, rings=12, location=o.matrix_world.translation,
                              name=f"_mg_ball_{kind}")
        # linear values: smoke dark charcoal, steam near-white, so the two never read alike under the studio lights
        ball.data.materials.append(_gray_material(f"_mg_ball_{kind}_mat", 0.95 if kind == "steam" else 0.025, 0.5))
        balls.append(ball)
    return balls


def render_frames(out_dir, count=6):
    """The animator's and the critic's look at the motion: frames spread evenly across one loop,
    from the side and close up on the biggest spinning wheel on that side (so a turn shows), or of
    the whole model when nothing spins; then, for each smoke or steam marker, a close-up of it as a
    ball where it sits. The scene is put back on its first frame and the balls removed."""
    center, radius = stage()
    cam = camera()
    scene = bpy.context.scene
    spun = [o for o in model_objects() if o.get(motion.SPIN_KEY)]
    if spun:
        wheel = max(spun, key=lambda o: (o.matrix_world.translation.x >= center.x - 1e-6, float(o[motion.SPIN_KEY])))
        hub, _, _, _ = frame([wheel])
        aim(cam, hub, float(wheel[motion.SPIN_KEY]) * 1.6, 90, 6)
        _turn_backdrop(center, 90)
    else:
        aim(cam, center, radius, *VIEWS["three_quarter"])
    total = max(1, scene.frame_end - scene.frame_start + 1)
    paths = []
    try:
        for i in range(count):
            scene.frame_set(scene.frame_start + round(i * total / count))
            paths.append(_render(os.path.join(out_dir, f"frame_{i:02d}.png")))
    finally:
        scene.frame_set(scene.frame_start)
    balls = _marker_balls()
    if balls:
        try:
            # one close-up per marker: markers can sit metres apart (a chimney and a safety valve),
            # and a ball framed with the others would be a few pixels
            for i, ball in enumerate(balls, 1):
                spot, r, _, _ = frame([ball])
                aim(cam, spot, max(r * 3, radius * 0.05), -35, 25)
                _turn_backdrop(center, -35)
                kind = ball.name.split("_")[-1].split(".")[0]
                paths.append(_render(os.path.join(out_dir, f"markers_{i}_{kind}.png")))
        finally:
            for b in balls:
                mesh = b.data
                bpy.data.objects.remove(b, do_unlink=True)
                bpy.data.meshes.remove(mesh)
    return paths


# --- angle pictures for other programs (the /v1 API's "views") ------------------------------
#
# Transparent pictures of the finished model from fixed angles, for apps that draw in 2D: no
# backdrop, no floor. The soft shadow under it is a picture of its own (<view>_shadow.png), so an
# app can put it on its own ground. views.json says where each camera stood and where the model
# and its lowest point are in each picture, so it can be scaled to its real size.

ASSET_VIEWS = {"front": 0, "front34": 45, "side": 90, "back34": 135, "back": 180, "top": None}
ASSET_ELEVATION = {"persp": 15, "ortho": 0}
SHADOW_CATCHER = "_mg_shadow_catcher"


def _picture_box(cam, objs, size):
    """Where the model is in the picture: its box in pixels (x0, y0, x1, y1, from the top left) and
    the height in pixels of its lowest point."""
    from bpy_extras.object_utils import world_to_camera_view
    scene = bpy.context.scene
    xs, ys = [], []
    dg = bpy.context.evaluated_depsgraph_get()
    for obj in objs:
        ev = obj.evaluated_get(dg)
        mesh = ev.to_mesh()
        try:
            step = max(1, len(mesh.vertices) // 4000)  # enough points for a box, quick on dense meshes
            for v in list(mesh.vertices)[::step]:
                p = world_to_camera_view(scene, cam, ev.matrix_world @ v.co)
                xs.append(p.x * size)
                ys.append((1 - p.y) * size)
        finally:
            ev.to_mesh_clear()
    if not xs:
        return None
    return [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)]


FLAT_LIGHT = 0.3  # how much of the flat look is lit shading; the rest is the material's own flat colour


def _flat_style(objs):
    """The flat look, closer to drawn 2D art than the photo studio: every material shows its own
    colours and patterns (a label, a gradient) mostly unlit, with a little light for its form, no
    gloss, and a dark outline (Freestyle) round the model and its edges."""
    done = set()
    for obj in objs:
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None or mat.name in done:
                continue
            done.add(mat.name)
            bsdf = _principled(mat)
            out = _output(mat) if bsdf is not None else None
            if out is None:
                continue
            nt = mat.node_tree
            base = bsdf.inputs.get("Base Color")
            emit = nt.nodes.new("ShaderNodeEmission")
            lit = nt.nodes.new("ShaderNodeBsdfDiffuse")
            mix = nt.nodes.new("ShaderNodeMixShader")
            mix.inputs["Fac"].default_value = FLAT_LIGHT
            for target in (emit.inputs["Color"], lit.inputs["Color"]):
                if base is not None and base.links:
                    nt.links.new(base.links[0].from_socket, target)
                elif base is not None:
                    target.default_value = base.default_value
            emit.inputs["Strength"].default_value = 1.0
            nt.links.new(emit.outputs[0], mix.inputs[1])
            nt.links.new(lit.outputs[0], mix.inputs[2])
            nt.links.new(mix.outputs[0], out.inputs["Surface"])
    scene = bpy.context.scene
    scene.render.use_freestyle = True
    scene.render.line_thickness_mode = "ABSOLUTE"
    scene.render.line_thickness = max(1.0, scene.render.resolution_x / 512)
    layer = bpy.context.view_layer
    layer.use_freestyle = True
    for lineset in layer.freestyle_settings.linesets:
        lineset.linestyle.color = (0.06, 0.06, 0.07)
    try:
        scene.view_settings.view_transform = "Standard"  # true flat colours, not the photo look
        scene.view_settings.look = "None"
    except TypeError:
        pass
    scene.view_settings.exposure = 0.0


def _shadow_catcher(lo, hi):
    """A large floor that only shows the model's shadow (Cycles' shadow catcher)."""
    obj = bpy.data.objects.get(SHADOW_CATCHER)
    if obj is None:
        bpy.ops.mesh.primitive_plane_add(size=1.0)
        obj = bpy.context.active_object
        obj.name = SHADOW_CATCHER
        obj.is_shadow_catcher = True
    span = max((hi - lo).length, 0.01) * 8
    obj.scale = (span, span, 1.0)
    obj.location = ((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z)
    return obj


PALETTE = ["#e4572e", "#2e86ab", "#f2c14e", "#4cb944", "#8e5bd6", "#f28c28", "#17bebb", "#e84a8a"]
EXPLAINER_LIGHT = 0.42  # how much of the explainer look is lit shading; the rest is the part's own flat colour


def _linear(hex_colour):
    """A #rrggbb colour as Blender's linear RGB."""
    text = hex_colour.lstrip("#")
    out = []
    for i in (0, 2, 4):
        c = int(text[i:i + 2], 16) / 255.0
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return tuple(out)


def part_colours(objs, colours=None):
    """Each part's explainer colour: the one asked for, else the palette in the parts' order."""
    names = []
    for o in objs:
        n = o.get(helpers.PART_KEY) or o.name
        if n not in names:
            names.append(n)
    given = dict(colours or {})
    out, i = {}, 0
    for n in sorted(names):
        if given.get(n):
            out[n] = given[n]
        else:
            while PALETTE[i % len(PALETTE)] in given.values() and i < 64:
                i += 1
            out[n] = PALETTE[i % len(PALETTE)]
            i += 1
    return out


def _fast_engine(samples=16):
    """EEVEE for the explainer look: flat colours and an outline need no path tracing."""
    scene = bpy.context.scene
    for name in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
        try:
            scene.render.engine = name
            break
        except TypeError:
            continue
    eevee = getattr(scene, "eevee", None)
    if eevee is not None and hasattr(eevee, "taa_render_samples"):
        eevee.taa_render_samples = samples


def _outline(width_px):
    scene = bpy.context.scene
    scene.render.use_freestyle = True
    scene.render.line_thickness_mode = "ABSOLUTE"
    scene.render.line_thickness = width_px
    layer = bpy.context.view_layer
    layer.use_freestyle = True
    for lineset in layer.freestyle_settings.linesets:
        lineset.linestyle.color = (0.05, 0.05, 0.06)


def _explainer_style(objs, colours=None):
    """The explainer look for learning videos: each part one clean saturated colour (from a palette
    of eight that stay distinct, or the colour asked for), soft even light with a little shading for
    its form, no gloss or grime, and a crisp dark outline. Returns {part: "#rrggbb"}."""
    chosen = part_colours(objs, colours)
    made = {}
    for obj in objs:
        name = obj.get(helpers.PART_KEY) or obj.name
        mat = made.get(name)
        if mat is None:
            mat = bpy.data.materials.new(f"_mg_explainer_{name}")
            mat.use_nodes = True
            nt = mat.node_tree
            nt.nodes.clear()
            rgb = (*_linear(chosen[name]), 1.0)
            out = nt.nodes.new("ShaderNodeOutputMaterial")
            emit = nt.nodes.new("ShaderNodeEmission")
            lit = nt.nodes.new("ShaderNodeBsdfDiffuse")
            mix = nt.nodes.new("ShaderNodeMixShader")
            emit.inputs["Color"].default_value = rgb
            emit.inputs["Strength"].default_value = 1.0
            lit.inputs["Color"].default_value = rgb
            mix.inputs["Fac"].default_value = EXPLAINER_LIGHT
            nt.links.new(emit.outputs[0], mix.inputs[1])
            nt.links.new(lit.outputs[0], mix.inputs[2])
            nt.links.new(mix.outputs[0], out.inputs["Surface"])
            mat.diffuse_color = rgb
            made[name] = mat
        if not obj.data.materials:
            obj.data.materials.append(mat)
        for i in range(len(obj.data.materials)):
            old = obj.data.materials[i]
            if old is not None and old.get("mg_cut"):
                continue  # a cutaway's cut faces keep their accent colour
            obj.data.materials[i] = mat
    _outline(max(1.5, bpy.context.scene.render.resolution_x / 420))
    scene = bpy.context.scene
    try:
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
    except TypeError:
        pass
    scene.view_settings.exposure = 0.0
    world = scene.world
    bg = next((n for n in world.node_tree.nodes if n.type == "BACKGROUND"), None) if world and world.node_tree else None
    if bg is not None and not bg.inputs["Color"].links:
        bg.inputs["Color"].default_value = (0.8, 0.8, 0.8, 1.0)
        bg.inputs["Strength"].default_value = 0.8
    return chosen


def _apply_style(objs, style, colours=None):
    """Put the model in a picture style; returns the explainer colours (or None)."""
    if style == "flat":
        _flat_style(objs)
    elif style == "explainer":
        chosen = _explainer_style(objs, colours)
        _fast_engine()
        return chosen
    return None


def render_asset_views(out_dir, camera_mode="persp", style="realistic", shadows=True, turntable=0, only=None,
                       colours=None):
    """The angle pictures: front, front34, side, back34, back and top as <view>.png, each but top with
    <view>_shadow.png, and turntable frames turntable_00.png ... if asked. only: render just these
    sets ("views", "shadows", "turntable"), so a long render can be split over several Blender runs.
    style: "realistic", "flat" or "explainer" (colours: {part: "#rrggbb"} for it).
    Returns {"views": {...}, "files": [...]} for views.json."""
    os.makedirs(out_dir, exist_ok=True)
    objs = model_objects()
    if not objs:
        raise RuntimeError("there is no model in the scene to render")
    stage()
    scene = bpy.context.scene
    backdrop = bpy.data.objects.get(BACKDROP)
    if backdrop is not None:
        backdrop.hide_render = True
    scene.render.film_transparent = True
    scene.render.image_settings.color_mode = "RGBA"
    size = scene.render.resolution_x
    cam = camera()
    cam.data.type = "ORTHO" if camera_mode == "ortho" else "PERSP"
    _, radius, lo, hi = frame(objs)
    elevation = ASSET_ELEVATION.get(camera_mode, 15)
    chosen = _apply_style(objs, style, colours)
    sets = set(only or ("views", "shadows", "turntable"))
    files, views = [], {}

    def place(az, el):
        aim_box(cam, lo, hi, az, el)
        info = {"azimuth_deg": az, "elevation_deg": el, "camera": camera_mode}
        if cam.data.type == "ORTHO":
            info["ortho_scale_m"] = round(cam.data.ortho_scale, 4)
            info["pixels_per_metre"] = round(size / cam.data.ortho_scale, 2)
        else:
            info["lens_mm"], info["distance_m"] = LENS_MM, round((cam.location - (lo + hi) / 2).length, 4)
        box = _picture_box(cam, objs, size)
        if box:
            info["box_px"], info["ground_px"] = box, box[3]
        return info

    angles = [(name, az if az is not None else 0, el) for name, az in ASSET_VIEWS.items()
              for el in [89.5 if az is None else elevation]]
    if "views" in sets:
        for name, az, el in angles:
            views[name] = place(az, el)
            files.append(_render(os.path.join(out_dir, f"{name}.png")))
    if "turntable" in sets and turntable:
        for i in range(int(turntable)):
            place(360 * i / int(turntable), elevation)
            files.append(_render(os.path.join(out_dir, f"turntable_{i:02d}.png")))
    if "shadows" in sets and shadows and elevation >= 5:  # a camera on the floor sees the shadow edge-on
        scene.render.engine = "CYCLES"  # the shadow catcher is Cycles only, also in the flat style
        scene.render.use_freestyle = False  # the flat style's outline would trace the shadow floor's edge
        catcher = _shadow_catcher(lo, hi)
        for obj in objs:
            obj.visible_camera = False  # the model still casts its shadow, but is not in the picture
        try:
            for name, az, el in angles:
                if name == "top":
                    continue  # straight down the shadow is hidden under the model
                place(az, el)
                files.append(_render(os.path.join(out_dir, f"{name}_shadow.png")))
        finally:
            for obj in objs:
                obj.visible_camera = True
            bpy.data.objects.remove(catcher, do_unlink=True)
    cam.data.type = "PERSP"
    out = {"views": views, "files": files, "size_px": size,
           "size_m": {"width": round(hi.x - lo.x, 4), "depth": round(hi.y - lo.y, 4),
                      "height": round(hi.z - lo.z, 4)}}
    if chosen:
        out["colours"] = chosen
    return out


def _loop_bounds(objs, samples=8):
    """The box the model fills over its whole loop (a rack slides, a crank swings), so every frame
    of a motion sequence is framed the same."""
    scene = bpy.context.scene
    first, last = scene.frame_start, scene.frame_end
    lo = hi = None
    for k in range(samples):
        scene.frame_set(first + round((last - first) * k / max(samples - 1, 1)))
        a, b = helpers.bounds(objs)
        lo = a if lo is None else Vector(map(min, lo, a))
        hi = b if hi is None else Vector(map(max, hi, b))
    scene.frame_set(first)
    return lo, hi


def _project(cam, point, size):
    from bpy_extras.object_utils import world_to_camera_view
    p = world_to_camera_view(bpy.context.scene, cam, point)
    return [round(p.x * size, 1), round((1 - p.y) * size, 1)]


CUT_COLOUR = "#ff2e63"   # the faces a cutaway slices open
EXPLODED_FRAMES = 48     # out and back in two seconds


def _cut_material():
    mat = bpy.data.materials.get("_mg_cut")
    if mat is None:
        mat = bpy.data.materials.new("_mg_cut")
        mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        emit = nt.nodes.new("ShaderNodeEmission")
        emit.inputs["Color"].default_value = (*_linear(CUT_COLOUR), 1.0)
        nt.links.new(emit.outputs[0], out.inputs["Surface"])
        mat.diffuse_color = (*_linear(CUT_COLOUR), 1.0)
        mat["mg_cut"] = 1
    return mat


def _cut_open(objs, cam, plane="auto"):
    """Slice housings open: the half of each nearest the camera (through its own axis) is cut away,
    and the faces the cut leaves are a flat accent colour. plane "top", "side" or "front" cuts away
    the half on that side instead."""
    fixed = {"top": Vector((0, 0, 1)), "side": Vector((1, 0, 0)), "front": Vector((0, -1, 0))}
    for obj in objs:
        bpy.context.view_layer.update()
        centre = obj.matrix_world.translation.copy()
        axis = (obj.matrix_world.to_3x3() @ Vector((0, 0, 1))).normalized()
        if plane in fixed:
            n = fixed[plane]
        else:
            d = cam.location - centre
            n = d - axis * d.dot(axis)
            n = n.normalized() if n.length > 1e-6 * max(d.length, 1e-9) else Vector((0, 0, 1))
        # slice with the plane and fill every cut opening: works piece by piece on a housing made of
        # several overlapping solids, where a boolean cut would not
        helpers.apply_modifiers(obj)
        if not any(m is not None and m.get("mg_cut") for m in obj.data.materials):
            obj.data.materials.append(_cut_material())
        slot = next(i for i, m in enumerate(obj.data.materials) if m is not None and m.get("mg_cut"))
        inv = obj.matrix_world.inverted()
        co = inv @ centre
        no = (obj.matrix_world.to_3x3().transposed() @ n).normalized()  # a normal into the part's own frame
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        # tag each separate solid before slicing (the slice can split one, a tube losing its end caps)
        layer = bm.verts.layers.int.new("mg_piece")
        bm.verts.index_update()
        parent = list(range(len(bm.verts)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for e in bm.edges:
            a, b = find(e.verts[0].index), find(e.verts[1].index)
            if a != b:
                parent[a] = b
        for v in bm.verts:
            v[layer] = find(v.index)
        bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], plane_co=co, plane_no=no,
                               clear_outer=True)
        # fill the cut outlines all together (they lie in one plane): an outline inside another is a
        # hole, so a hollow scroll's wall shows as a ring (the cut also took its end caps away, so its
        # two walls are no longer joined and cannot be told apart by what they are joined to)
        from mathutils.geometry import tessellate_polygon
        open_edges = [e for e in bm.edges if len(e.link_faces) == 1]
        by_piece = {}
        for e in open_edges:
            # a vertex the slice made may carry no tag of its own: use the face it borders
            f = e.link_faces[0]
            tags = [v[layer] for v in f.verts]
            by_piece.setdefault(max(set(tags), key=tags.count), []).append(e)
        for edges in by_piece.values():
            nxt = {}
            for e in edges:
                a, b = e.verts
                nxt.setdefault(a, []).append(b)
                nxt.setdefault(b, []).append(a)
            loops, seen = [], set()
            for start in nxt:
                if start in seen:
                    continue
                loop, prev, cur = [start], None, start
                seen.add(start)
                while True:
                    step = next((v for v in nxt[cur] if v is not prev and v not in seen), None)
                    if step is None:
                        break
                    loop.append(step)
                    seen.add(step)
                    prev, cur = cur, step
                if len(loop) >= 3:
                    loops.append(loop)
            if not loops:
                continue
            # in the cut plane: an outline inside another is a hole (a hollow scroll's wall section)
            u = no.orthogonal().normalized()
            w = no.cross(u)
            flat2 = [[(v.co.dot(u), v.co.dot(w)) for v in loop] for loop in loops]

            def inside(pt, poly):
                x, y = pt
                hit = False
                for k in range(len(poly)):
                    (x1, y1), (x2, y2) = poly[k], poly[k - 1]
                    if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                        hit = not hit
                return hit

            within = {i: [j for j in range(len(loops)) if j != i and inside(flat2[i][0], flat2[j])]
                      for i in range(len(loops))}  # the outlines each one lies inside
            depth = {i: len(within[i]) for i in within}
            holes = {i: [j for j in within if i in within[j] and depth[j] == depth[i] + 1] for i in within}
            for i, loop in enumerate(loops):
                if depth[i] % 2:
                    continue  # a hole: filled as part of the ring round it
                if holes[i]:
                    for j in holes[i]:
                        try:
                            made = bmesh.ops.bridge_loops(bm, edges=[e for e in edges if e.verts[0] in loop
                                                                      or e.verts[0] in loops[j]])
                            for f in made.get("faces", []):
                                f.material_index = slot
                        except (ValueError, RuntimeError):
                            for tri in tessellate_polygon([[v.co for v in loop], [v.co for v in loops[j]]]):
                                both = loop + loops[j]
                                bm.faces.new([both[t] for t in tri]).material_index = slot
                else:
                    try:
                        bm.faces.new(loop).material_index = slot
                    except ValueError:
                        pass
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()


def _flows_px(objs, cam, size):
    """The air paths kept on housings (intake, compressed, exhaust...), in picture pixels, for the
    Village to draw: they are not rendered."""
    out = []
    for obj in objs:
        raw = obj.get(motion.GEAR_KEY)
        try:
            info = json.loads(raw) if raw else {}
        except ValueError:
            info = {}
        for name, pts in (info.get("flows") or {}).items():
            out.append({"name": name, "part": obj.get(helpers.PART_KEY) or obj.name,
                        "points_px": [_project(cam, obj.matrix_world @ Vector(p), size) for p in pts]})
    return out


def render_motion(out_dir, camera_mode="persp", style="explainer", angle="front34", first=None, last=None,
                  colours=None, mode="motion", cut=None, plane="auto", offsets=None):
    """The model moving through its loop as transparent pictures frame_000.png ... from one angle
    ("front", "front34" or "side"), frames first..last of the loop (0-based, all when left out), so
    a long loop can be split over two Blender runs. Every frame is framed the same (the box the model
    fills over the whole loop). mode "cutaway" first slices the parts named in `cut` open (plane:
    "auto", "top", "side" or "front"); mode "exploded" instead slides every part out by its offset
    (metres, from the mechanism sheet) and back, over EXPLODED_FRAMES frames, the motion held at the
    first frame. Returns the files and, for each frame, where each part is: its colour, its box in
    the picture and its centre (a gear's axis), for labels and arrows (parts.json), and the air paths
    in picture pixels."""
    os.makedirs(out_dir, exist_ok=True)
    objs = model_objects()
    if not objs:
        raise RuntimeError("there is no model in the scene to render")
    stage()
    scene = bpy.context.scene
    backdrop = bpy.data.objects.get(BACKDROP)
    if backdrop is not None:
        backdrop.hide_render = True
    scene.render.film_transparent = True
    scene.render.image_settings.color_mode = "RGBA"
    size = scene.render.resolution_x
    cam = camera()
    cam.data.type = "ORTHO" if camera_mode == "ortho" else "PERSP"
    if style == "realistic" and scene.render.engine == "CYCLES":
        scene.cycles.samples = min(scene.cycles.samples, 48)
    az = ASSET_VIEWS.get(angle)
    if az is None:
        raise ValueError(f"angle must be one of front, front34, side (not {angle})")
    el = ASSET_ELEVATION.get(camera_mode, 15)
    lo, hi = _loop_bounds(objs)
    moves = {}
    if mode == "exploded":
        scene.frame_set(scene.frame_start)
        for o in objs:
            off = (offsets or {}).get(o.get(helpers.PART_KEY) or o.name)
            if off:
                moves[o.name] = (o, o.location.copy(), Vector(off))
        held = {}  # a part keyed to move (a piston) would be put back by its keys: its action waits aside
        for o, base, off in moves.values():
            if o.animation_data and o.animation_data.action:
                held[o.name] = (o, o.animation_data.action, o.rotation_euler.copy())
                o.animation_data.action = None
        for o, base, off in moves.values():
            o.location = base + off
        a, b = helpers.bounds(objs)
        lo, hi = Vector(map(min, lo, a)), Vector(map(max, hi, b))
        for o, base, off in moves.values():
            o.location = base
    aim_box(cam, lo, hi, az, el)
    if mode == "cutaway" and cut:
        _cut_open([o for o in objs if (o.get(helpers.PART_KEY) or o.name) in set(cut)], cam, plane)
    chosen = _apply_style(objs, style, colours) or {}
    view = {"azimuth_deg": az, "elevation_deg": el, "camera": camera_mode, "angle": angle}
    if cam.data.type == "ORTHO":
        view["pixels_per_metre"] = round(size / cam.data.ortho_scale, 2)
    else:
        view["lens_mm"], view["distance_m"] = LENS_MM, round((cam.location - (lo + hi) / 2).length, 4)
    frames = EXPLODED_FRAMES if mode == "exploded" else scene.frame_end - scene.frame_start + 1
    start = 0 if first is None else max(0, int(first))
    stop = frames - 1 if last is None else min(frames - 1, int(last))
    groups = {}
    for o in objs:
        groups.setdefault(o.get(helpers.PART_KEY) or o.name, []).append(o)
    scene.frame_set(scene.frame_start)
    flows = _flows_px(objs, cam, size) if mode != "exploded" else []
    files, where = [], {}
    for i in range(start, stop + 1):
        if mode == "exploded":
            f = 0.5 - 0.5 * math.cos(2 * math.pi * i / frames)  # out and back, easing at both ends
            for o, base, off in moves.values():
                o.location = base + off * f
            bpy.context.view_layer.update()
        else:
            scene.frame_set(scene.frame_start + i)
        rows = []
        for name in sorted(groups):
            box = _picture_box(cam, groups[name], size)
            origin = groups[name][0].matrix_world.translation
            rows.append({"name": name, "colour": chosen.get(name), "box_px": box,
                         "centre_px": _project(cam, origin, size)})
        where[i] = rows
        files.append(_render(os.path.join(out_dir, f"frame_{i:03d}.png")))
    for o, base, off in moves.values():
        o.location = base
    for o, action, rot in (held.values() if mode == "exploded" else []):
        o.animation_data.action = action
        o.rotation_euler = rot
    scene.frame_set(scene.frame_start)
    cam.data.type = "PERSP"
    return {"files": files, "parts": where, "size_px": size, "view": view, "frames": frames, "fps": scene.render.fps,
            "colours": chosen, "loop": scene.get("mg_mechanism_loop"), "flows": flows, "mode": mode}


# --- export -------------------------------------------------------------------------

def _plain_materials():
    """The browser and game engines cannot read node patterns. Bake every node-driven channel into
    pictures first (_bake_channels); whatever could not be baked then gets a plain value: a library
    material its recorded colour, metal and roughness, any other material its best guess."""
    baked = _bake_channels()
    for mat in bpy.data.materials:
        if "mg_base_color" not in mat.keys() or not mat.node_tree:
            continue
        bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            continue
        for socket_name, key in (("Base Color", "mg_base_color"), ("Metallic", "mg_metallic"),
                                 ("Roughness", "mg_roughness")):
            sock = bsdf.inputs.get(socket_name)
            if sock is None or _is_baked(sock):
                continue
            for link in list(sock.links):
                mat.node_tree.links.remove(link)
            value = mat[key]
            sock.default_value = tuple(value) if socket_name == "Base Color" else float(value)
        normal = bsdf.inputs.get("Normal")
        if normal is not None and not _is_baked(normal):
            for link in list(normal.links):
                mat.node_tree.links.remove(link)
    _plain_node_roughness()
    _flatten_emission_materials()
    return baked


def _range_middle(sock):
    """The middle of the first Map Range's output range up the chain feeding this socket (what a
    noise swings between), or None."""
    seen, stack = set(), [sock]
    while stack:
        for link in stack.pop().links:
            node = link.from_node
            if node.name in seen:
                continue
            seen.add(node.name)
            if node.type == "MAP_RANGE":
                lo, hi = node.inputs.get("To Min"), node.inputs.get("To Max")
                if lo is not None and hi is not None and not lo.links and not hi.links:
                    return (float(lo.default_value) + float(hi.default_value)) / 2
            stack += [i for i in node.inputs if i.links]
    return None


def _plain_node_roughness():
    """A Roughness fed by a node chain (a brushed-metal noise) that could not be baked reaches the GLB
    as fully rough (1.0), so metal shows as flat grey. Give it one value instead: the middle of a Map
    Range's output range in the chain (what the noise swings between), else the socket's own value."""
    for mat in bpy.data.materials:
        nt = mat.node_tree
        if not mat.use_nodes or nt is None or "mg_base_color" in mat.keys():
            continue
        for bsdf in (n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"):
            sock = bsdf.inputs.get("Roughness")
            if sock is None or not sock.links or sock.links[0].from_node.type == "TEX_IMAGE" or _is_baked(sock):
                continue
            value = _range_middle(sock)
            for link in list(sock.links):
                nt.links.remove(link)
            if value is not None:
                sock.default_value = min(max(value, 0.0), 1.0)
            mat.update_tag()


BAKED_ATTR = "mg_baked_colour"
BAKE_UV = "mg_bake_uv"
BAKE_SAMPLES = 16          # rays per pixel: fine lines thinner than a pixel (mortar) blend in as in the renders
BAKED_LABEL = "_mg_baked"   # the label on every node the bake adds to a material for the export
# Principled inputs a picture can carry. "rm" is roughness and metal in one picture (green and
# blue, as glTF stores them); see-through goes into the colour picture's alpha.
CHANNELS = {"base": "Base Color", "rough": "Roughness", "metal": "Metallic", "normal": "Normal",
            "emit": "Emission Color", "alpha": "Alpha"}
# Outputs whose value depends on where the camera is: a bake would freeze one arbitrary view of
# them (a facing gradient on stylised paint), so a chain that uses one is left to the plain values.
VIEW_OUTPUTS = {("LAYER_WEIGHT", None), ("FRESNEL", None), ("LIGHT_PATH", None), ("CAMERA", None),
                ("NEW_GEOMETRY", "Incoming"), ("NEW_GEOMETRY", "Backfacing"), ("TEX_COORD", "Camera"),
                ("TEX_COORD", "Window"), ("TEX_COORD", "Reflection"), ("AMBIENT_OCCLUSION", None)}


def _is_baked(sock):
    return bool(sock.links) and sock.links[0].from_node.label == BAKED_LABEL


def _bakeable(sock):
    """True when the socket is fed by a node chain the GLB cannot carry and a bake can capture."""
    if not sock.links:
        return False
    first = sock.links[0].from_node
    if first.type in ("TEX_IMAGE", "VERTEX_COLOR", "ATTRIBUTE") or first.label == BAKED_LABEL:
        return False
    if sock.name == "Normal" and first.type == "NORMAL_MAP" and first.inputs["Color"].links \
            and first.inputs["Color"].links[0].from_node.type == "TEX_IMAGE":
        return False  # a normal map picture exports as it is
    seen, stack = set(), [sock]
    while stack:
        for link in stack.pop().links:
            node = link.from_node
            if (node.type, None) in VIEW_OUTPUTS or (node.type, link.from_socket.name) in VIEW_OUTPUTS:
                return False
            if node.name in seen:
                continue
            seen.add(node.name)
            stack += [i for i in node.inputs if i.links]
    return True


def _principled(mat):
    nt = mat.node_tree if mat is not None and mat.use_nodes else None
    return next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None) if nt is not None else None


def _needs(mat):
    """{channel: socket} for each Principled input of the material that needs baking."""
    bsdf = _principled(mat)
    if bsdf is None:
        return {}
    out = {}
    for key, name in CHANNELS.items():
        sock = bsdf.inputs.get(name)
        if sock is not None and _bakeable(sock):
            out[key] = sock
    return out


def _guess_colour(socket):
    """A plain colour standing in for a node chain: the mean of the fixed colours feeding it
    (a mix's two colours, a ramp's stops, an RGB node), or None when it has none."""
    cols, seen, stack = [], set(), [socket]
    while stack:
        for link in stack.pop().links:
            node = link.from_node
            if node.name in seen:
                continue
            seen.add(node.name)
            if node.type == "RGB":
                cols.append(tuple(node.outputs[0].default_value)[:3])
            elif node.type == "VALTORGB":
                cols += [tuple(e.color)[:3] for e in node.color_ramp.elements]
            for inp in node.inputs:
                if not getattr(inp, "enabled", True):
                    continue
                if inp.links:
                    stack.append(inp)
                elif inp.type == "RGBA" and not (node.type == "MIX" and getattr(node, "data_type", "RGBA") != "RGBA"):
                    cols.append(tuple(inp.default_value)[:3])
    if not cols:
        return None
    return tuple(sum(c[i] for c in cols) / len(cols) for i in range(3))


def _bake_groups(objs, names):
    """The objects baked together: parts that share a material to bake go in one group, since a
    material can point at only one picture. Each group gets one layout and one set of pictures, so
    every material keeps its own name in the file."""
    groups = []
    for o in objs:
        mine = {s.material.name for s in o.material_slots if s.material and s.material.name in names}
        joined = [g for g in groups if g["mats"] & mine]
        group = {"objs": [o], "mats": set(mine)}
        for g in joined:
            group["objs"] = g["objs"] + group["objs"]
            group["mats"] |= g["mats"]
            groups.remove(g)
        groups.append(group)
    return [g["objs"] for g in groups]


def _bake_uv(objs):
    """The group's own layout for the baked pictures: one Smart UV Project over all its objects at
    once, so their islands share the picture without overlapping. The model's other UV maps stay as
    they were. It is left active: the exporter takes the surface directions (tangents) for the normal
    map from the active UV map."""
    # the bake layout must sit in the same slot on every part: a material read through a different
    # slot on two parts is written to the file twice (two materials of one name)
    slot = max(len([u for u in o.data.uv_layers if u.name != BAKE_UV]) for o in objs)
    for o in objs:
        layers = o.data.uv_layers
        if layers.get(BAKE_UV) is None:
            while len(layers) < slot:
                layers.new(name=f"mg_pad_uv_{len(layers)}")
            layers.new(name=BAKE_UV)
        o.data.uv_layers.active = o.data.uv_layers[BAKE_UV]
    bpy.ops.object.mode_set(mode="EDIT")  # every selected object of the group, edited together
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.004)
    bpy.ops.object.mode_set(mode="OBJECT")
    for o in objs:  # edit mode rebuilds the layers: look the layer up again
        o.data.uv_layers.active = o.data.uv_layers[BAKE_UV]


def _bake_size(objs):
    """Picture size by the group's surface: 512 px for small parts (a rivet, a pull tab), 1024 for
    ordinary ones, 2048 for a large surface (a car body), so detail stays about even."""
    area = 0.0
    for o in objs:
        scale = o.matrix_world.to_scale()
        area += sum(p.area for p in o.data.polygons) * abs(scale.x * scale.y * scale.z) ** (2.0 / 3.0)
    return 512 if area < 0.01 else 1024 if area < 1.0 else 2048


def _output(mat):
    nt = mat.node_tree
    if not mat.use_nodes or nt is None:
        return None
    return next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None) \
        or next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)


@contextmanager
def _glowing(mats, feed):
    """While inside, each material shows only a glow: feed(mat) gives the socket to glow with, or a
    fixed RGBA. The bake reads that glow (EMIT) as the channel's value."""
    added = []
    try:
        for mat in mats:
            out = _output(mat)
            if out is None:
                continue
            nt = mat.node_tree
            surface = out.inputs["Surface"]
            prev = surface.links[0].from_socket if surface.links else None
            em = nt.nodes.new("ShaderNodeEmission")
            em.inputs["Strength"].default_value = 1.0
            extra = []
            src = feed(mat, nt, extra)
            if isinstance(src, tuple):
                em.inputs["Color"].default_value = src
            else:
                nt.links.new(src, em.inputs["Color"])
            nt.links.new(em.outputs["Emission"], surface)
            added.append((mat, out, prev, [em] + extra))
        yield
    finally:
        for mat, out, prev, nodes in added:
            for n in nodes:
                mat.node_tree.nodes.remove(n)
            if prev is not None:
                mat.node_tree.links.new(prev, out.inputs["Surface"])


@contextmanager
def _bake_target(mats, picture):
    """While inside, each material's active node is an image node on this picture: the bake writes there."""
    spots = []
    try:
        for m in mats:
            if m.node_tree is None:
                continue
            node = m.node_tree.nodes.new("ShaderNodeTexImage")
            node.image = picture
            m.node_tree.nodes.active = node
            spots.append((m, node))
        yield
    finally:
        for m, node in spots:
            m.node_tree.nodes.remove(node)


def _new_picture(name, size, data, alpha=False, fill=(0.0, 0.0, 0.0, 1.0)):
    """A blank picture filled with the part's usual value: the bake only paints the islands (and a
    margin), and the space between them blends into their edges when the picture is shown small,
    so it must not be black (a roughness of 0 there made mirror-shiny blotches at the seams)."""
    img = bpy.data.images.new(name, size, size, alpha=alpha, is_data=data)
    if data:
        img.colorspace_settings.name = "Non-Color"
    img.generated_color = fill
    return img


def _as_jpeg(img, quality=90):
    """Turn a baked picture into a JPEG file image: the exporter keeps a picture as JPEG only when
    it comes from a JPEG file. The file is written to the temp folder, packed, then deleted."""
    folder = tempfile.mkdtemp(prefix="mg_bake_")
    path = os.path.join(folder, f"{img.name}.jpg")  # the exporter names the picture after its file
    img.filepath_raw = path
    img.file_format = "JPEG"
    try:
        img.save(filepath=path, quality=quality)
    except TypeError:  # an older Blender without the quality argument
        img.save()
    img.source = "FILE"
    img.pack()
    try:
        os.remove(path)
        os.rmdir(folder)
    except OSError:
        pass


def _bake_group(objs, label, need, size):
    """Bake one group's channels (its objects selected). need: {material: {channel: socket}}.
    Returns {material name: {channel: picture}}; raises when the picture bake does not work."""
    scene = bpy.context.scene
    mats = list({m for o in objs for m in (s.material for s in o.material_slots) if m is not None})
    wanted = {ch for chans in need.values() for ch in chans}
    pictures = {}

    def value_of(mat, name):
        bsdf = _principled(mat)
        sock = bsdf.inputs.get(name) if bsdf is not None else None
        return float(sock.default_value) if sock is not None else 0.0

    def emit_bake(key, data, feed, alpha=False, fill=(0.0, 0.0, 0.0, 1.0)):
        img = _new_picture(f"{label}_{key}", size, data, alpha, fill)
        with _glowing(mats, feed), _bake_target(mats, img):
            bpy.ops.object.bake(type="EMIT", target="IMAGE_TEXTURES", margin=16)
        pictures[key] = img
        return img

    def usual(key, name, plain):
        """The channel's usual value over the materials that bake it, for the empty space."""
        vals = []
        for mat, chans in need.items():
            if key in chans:
                v = _range_middle(chans[key]) if plain else _guess_colour(chans[key])
                if v is None:
                    v = value_of(mat, name) if plain else None
                if v is not None:
                    vals.append(v if plain else tuple(v))
        if not vals:
            return None
        if plain:
            return sum(vals) / len(vals)
        return tuple(sum(c[i] for c in vals) / len(vals) for i in range(3))

    if "base" in wanted or "alpha" in wanted:
        def feed_base(mat, nt, extra):
            chans = need.get(mat, {})
            if "base" in chans:
                return chans["base"].links[0].from_socket
            bsdf = _principled(mat)
            if mat in need and bsdf is not None:  # its colour is fixed: the picture holds it as it is
                return tuple(bsdf.inputs["Base Color"].default_value)
            return (1.0, 1.0, 1.0, 1.0)
        c = usual("base", "Base Color", False) or (0.8, 0.8, 0.8)
        emit_bake("base", False, feed_base, alpha="alpha" in wanted, fill=(c[0], c[1], c[2], 1.0))
    if "rough" in wanted or "metal" in wanted:
        def feed_rm(mat, nt, extra):
            chans = need.get(mat, {})
            comb = nt.nodes.new("ShaderNodeCombineColor")
            extra.append(comb)
            comb.inputs[0].default_value = 1.0
            for idx, (key, name) in ((1, ("rough", "Roughness")), (2, ("metal", "Metallic"))):
                if key in chans:
                    nt.links.new(chans[key].links[0].from_socket, comb.inputs[idx])
                else:
                    comb.inputs[idx].default_value = value_of(mat, name)
            return comb.outputs[0]
        first = next(iter(need))
        r = usual("rough", "Roughness", True)
        m = usual("metal", "Metallic", True)
        emit_bake("rm", True, feed_rm, fill=(1.0, value_of(first, "Roughness") if r is None else r,
                                              value_of(first, "Metallic") if m is None else m, 1.0))
    if "emit" in wanted:
        emit_bake("emit", False, lambda mat, nt, extra: need[mat]["emit"].links[0].from_socket
                  if "emit" in need.get(mat, {}) else (0.0, 0.0, 0.0, 1.0))
    if "alpha" in wanted:
        tmp = emit_bake("alpha_tmp", True, lambda mat, nt, extra: need[mat]["alpha"].links[0].from_socket
                        if "alpha" in need.get(mat, {}) else (1.0, 1.0, 1.0, 1.0))
        base, n = pictures["base"], size * size * 4
        px, ax = [0.0] * n, [0.0] * n
        base.pixels.foreach_get(px)
        tmp.pixels.foreach_get(ax)
        px[3::4] = ax[0::4]
        base.pixels.foreach_set(px)
        bpy.data.images.remove(pictures.pop("alpha_tmp"))
    if "normal" in wanted:
        img = _new_picture(f"{label}_normal", size, True, fill=(0.5, 0.5, 1.0, 1.0))  # flat
        samples = scene.cycles.samples
        scene.cycles.samples = BAKE_SAMPLES
        try:
            with _bake_target(mats, img):
                bpy.ops.object.bake(type="NORMAL", normal_space="TANGENT", target="IMAGE_TEXTURES", margin=16)
        finally:
            scene.cycles.samples = samples
        pictures["normal"] = img
    for key, img in pictures.items():
        if key in ("base", "emit") and not (key == "base" and "alpha" in wanted):
            _as_jpeg(img)  # colour pictures: several times smaller; data pictures stay exact (PNG)
        else:
            img.pack()
    return {m.name: {ch: pictures["rm" if ch in ("rough", "metal") else "base" if ch == "alpha" else ch]
                     for ch in chans} for m, chans in need.items()}


def _wire_pictures(mat, chans):
    """Point the material's baked inputs at their pictures (through the bake layout)."""
    nt = mat.node_tree
    bsdf = _principled(mat)
    uv = nt.nodes.new("ShaderNodeUVMap")
    uv.uv_map = BAKE_UV
    uv.label = BAKED_LABEL
    texs = {}

    def tex(img):
        if img.name not in texs:
            t = nt.nodes.new("ShaderNodeTexImage")
            t.image = img
            t.label = BAKED_LABEL
            nt.links.new(uv.outputs["UV"], t.inputs["Vector"])
            texs[img.name] = t
        return texs[img.name]

    def plug(name, from_socket):
        sock = bsdf.inputs[name]
        for link in list(sock.links):
            nt.links.remove(link)
        nt.links.new(from_socket, sock)

    if "base" in chans:
        plug("Base Color", tex(chans["base"]).outputs["Color"])
    if "alpha" in chans:
        plug("Alpha", tex(chans["alpha"]).outputs["Alpha"])
        if "base" not in chans:
            plug("Base Color", tex(chans["alpha"]).outputs["Color"])
    if "rough" in chans or "metal" in chans:
        sep = nt.nodes.new("ShaderNodeSeparateColor")
        sep.label = BAKED_LABEL
        nt.links.new(tex(chans.get("rough") or chans.get("metal")).outputs["Color"], sep.inputs[0])
        if "rough" in chans:
            plug("Roughness", sep.outputs[1])
        if "metal" in chans:
            plug("Metallic", sep.outputs[2])
    if "emit" in chans:
        plug("Emission Color", tex(chans["emit"]).outputs["Color"])
    if "normal" in chans:
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nm.space = "TANGENT"
        nm.uv_map = BAKE_UV
        nm.label = BAKED_LABEL
        nt.links.new(tex(chans["normal"]).outputs["Color"], nm.inputs["Color"])
        plug("Normal", nm.outputs["Normal"])
    mat.update_tag()


def _fallback(mat, chans):
    """When an object's pictures could not be baked: each channel left drops its chain and keeps a
    plain value (a colour's guess here; the library materials and _plain_node_roughness fill in
    the rest)."""
    nt = mat.node_tree
    for key, sock in chans.items():
        if key == "rough":
            continue  # _plain_node_roughness finds its value from the chain
        guess = _guess_colour(sock) if key in ("base", "emit") else None
        for link in list(sock.links):
            nt.links.remove(link)
        if guess is not None:
            sock.default_value = (guess[0], guess[1], guess[2], 1.0)
    mat.update_tag()


def _bake_channels():
    """Bake what Blender draws for every node-driven material input (colour, roughness and metal,
    surface detail, glow, see-through) into pictures on a layout of their own (one per group of parts sharing a material), packed into
    the file, and point the materials at them: the GLB then shows what the renders show, in the
    page and in a game engine. Fixed values stay values. A chain that depends on the view (a facing
    gradient) is not baked. If a group's pictures fail, its colour falls back to per-corner
    colours, and everything else to plain values. Returns {object name: [channels baked]}."""
    names = {m.name for m in bpy.data.materials if _needs(m)}
    if not names:
        return {}
    uses = lambda o: [s.material for s in o.material_slots if s.material]
    objs = [o for o in model_objects() if any(m.name in names for m in uses(o))]
    groups = _bake_groups(objs, names)
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    engine = scene.render.engine
    samples = scene.cycles.samples if hasattr(scene, "cycles") else None
    selected = [o for o in scene.objects if o.select_get()]
    active = view_layer.objects.active
    done, report = {}, {}
    clear = scene.render.bake.use_clear
    try:
        scene.render.engine = "CYCLES"
        scene.cycles.samples = BAKE_SAMPLES
        scene.render.bake.use_clear = False  # keep each picture's fill (_new_picture) between the islands
        for group in groups:
            mats = {m for o in group for m in uses(o)}
            need = {m: c for m, c in ((m, _needs(m)) for m in mats) if c}
            if not need:
                continue
            for x in scene.objects:
                x.select_set(x in group)
            view_layer.objects.active = group[0]
            label = group[0].name if len(group) == 1 else f"{group[0].name}_group"
            try:
                _bake_uv(group)
                result = _bake_group(group, label, need, _bake_size(group))
                for m in need:
                    done[m.name] = ("pictures", result[m.name])
                report[label] = sorted({ch for c in need.values() for ch in c})
            except Exception as exc:
                print(f"[rig] could not bake pictures for {label}: {exc}")
                if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
                    bpy.ops.object.mode_set(mode="OBJECT")
                colours = any("base" in c for c in need.values())
                for o in group if colours else []:
                    try:
                        for x in scene.objects:
                            x.select_set(x is o)
                        view_layer.objects.active = o
                        mesh = o.data
                        attr = mesh.color_attributes.get(BAKED_ATTR) or                             mesh.color_attributes.new(BAKED_ATTR, "FLOAT_COLOR", "CORNER")
                        mesh.color_attributes.active_color = attr
                        with _glowing(list(set(uses(o))), lambda mat, nt, extra: need[mat]["base"].links[0].from_socket
                                      if "base" in need.get(mat, {}) else (1.0, 1.0, 1.0, 1.0)):
                            bpy.ops.object.bake(type="EMIT", target="VERTEX_COLORS")
                    except Exception as exc2:
                        print(f"[rig] could not bake the colours of {o.name}: {exc2}")
                        colours = False
                for m, c in need.items():
                    done[m.name] = ("fallback", c, colours)
    finally:
        if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        scene.render.bake.use_clear = clear
        scene.render.engine = engine
        if samples is not None:
            scene.cycles.samples = samples
        for x in scene.objects:
            x.select_set(x in selected)
        view_layer.objects.active = active
    for name, entry in done.items():
        mat = bpy.data.materials[name]
        if entry[0] == "pictures":
            _wire_pictures(mat, entry[1])
            continue
        _, chans, colours = entry
        if colours and "base" in chans:
            nt = mat.node_tree
            sock = chans["base"]
            for link in list(sock.links):
                nt.links.remove(link)
            node = nt.nodes.new("ShaderNodeVertexColor")
            node.layer_name = BAKED_ATTR
            node.label = BAKED_LABEL
            nt.links.new(node.outputs["Color"], sock)
            chans = {k: v for k, v in chans.items() if k != "base"}
        _fallback(mat, chans)
    view_layer.update()
    return report


def _from_node(socket, node_type):
    """The node of this type feeding the socket, directly or through reroutes, or None."""
    while socket is not None and socket.links:
        node = socket.links[0].from_node
        if node.type == node_type:
            return node
        if node.type != "REROUTE":
            return None
        socket = node.inputs[0]
    return None


def _plain_bsdf(mat, rgb, alpha=1.0):
    """Swap the material's nodes for one Principled BSDF of this (linear) colour."""
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Metallic"].default_value = 0.0
    bsdf.inputs["Roughness"].default_value = 0.6
    if alpha < 1.0:
        bsdf.inputs["Alpha"].default_value = alpha
        for attr, value in (("blend_method", "BLEND"), ("surface_render_method", "BLENDED")):
            if hasattr(mat, attr):
                try:
                    setattr(mat, attr, value)
                except (TypeError, ValueError):
                    pass
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])


def _flatten_emission_materials():
    """Toon materials draw with an Emission node whose colour comes from a colour ramp lit by the
    surface's facing, and an outline ("ink") is an Emission shown only from behind. A GLB carries
    neither: it would get a white glow. Give each a plain colour (the lit tone, or black for ink) and
    keep what the page needs to draw them again as custom properties (exported as extras):
    mg_toon_colors (the ramp's colours, linear RGB, flattened), mg_toon_stops (their positions),
    mg_toon_light (the light direction, Blender axes), and mg_ink for an outline."""
    for mat in bpy.data.materials:
        nt = mat.node_tree
        if not mat.use_nodes or nt is None or "mg_base_color" in mat.keys():
            continue
        if any(n.type == "BSDF_PRINCIPLED" for n in nt.nodes):
            continue
        out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None) \
            or next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
        if out is None or not out.inputs["Surface"].links:
            continue
        emissions = [n for n in nt.nodes if n.type == "EMISSION"]
        if not emissions:
            continue
        surface = out.inputs["Surface"].links[0].from_node
        if surface.type == "MIX_SHADER" and any(n.type == "BSDF_TRANSPARENT" for n in nt.nodes) \
                and _from_node(surface.inputs[0], "NEW_GEOMETRY") is not None:
            mat["mg_ink"] = 1
            _plain_bsdf(mat, (0.0, 0.0, 0.0), alpha=0.0)
            continue
        ramp, emission = None, emissions[0]
        for e in emissions:
            ramp = _from_node(e.inputs["Color"], "VALTORGB")
            if ramp is not None:
                emission = e
                break
        if ramp is None:  # a plain glow (a lamp, a neon tube) exports fine as it is
            continue
        elements = sorted(ramp.color_ramp.elements, key=lambda el: el.position)
        mat["mg_toon_colors"] = [round(float(v), 5) for el in elements for v in el.color[:3]]
        mat["mg_toon_stops"] = [round(float(el.position), 4) for el in elements]
        dot = next((n for n in nt.nodes if n.type == "VECT_MATH" and n.operation == "DOT_PRODUCT"), None)
        if dot is not None:
            mat["mg_toon_light"] = [round(float(v), 4) for v in dot.inputs[1].default_value]
        lit = elements[-1].color
        _plain_bsdf(mat, (lit[0], lit[1], lit[2]))
    # the exporter reads the evaluated copies of the materials: make them take the new properties
    for mat in bpy.data.materials:
        if "mg_ink" in mat.keys() or "mg_toon_colors" in mat.keys():
            mat.update_tag()
    bpy.context.view_layer.update()


def _first(*calls):
    """Run the first exporter this Blender version has (operators were renamed over the years)."""
    error = None
    for call in calls:
        try:
            return call()
        except (AttributeError, RuntimeError) as exc:
            error = exc
    raise error


def _skeletons(objs):
    """The armatures that drive any of these meshes (a character's skeleton)."""
    out = []
    for o in objs:
        for m in getattr(o, "modifiers", []):
            if m.type == "ARMATURE" and m.object is not None and m.object not in out:
                out.append(m.object)
    return out


def _select(objs):
    """Select the model and, with it, the motion's empties (markers and spin pivots) and a
    character's skeleton, which the GLB and FBX carry; the still formats only take meshes anyway."""
    objs = list(objs) + motion.motion_objects() + _skeletons(objs)
    for o in bpy.context.scene.objects:
        o.select_set(o in objs)
    bpy.context.view_layer.objects.active = objs[0]


def _export_gltf(path):
    """GLB with the motion: the keyframes of the loop and the custom properties (the markers' kind
    and size, the speed) as extras the page reads."""
    bpy.context.scene.frame_set(bpy.context.scene.frame_start)
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True, export_apply=True,
                              export_animations=True, export_extras=True,
                              export_tangents=True)  # surface directions for normal maps and brushed metal


def export_glb(path):
    """Export only a GLB of what is in the scene now: the page's live view of a build step. Run it
    last in a job: like export_all it simplifies the materials."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    objs = model_objects()
    if not objs:
        raise RuntimeError("there is no model in the scene to export")
    _plain_materials()
    _select(objs)
    _export_gltf(path)
    if not os.path.isfile(path):
        raise RuntimeError("the exporter wrote no file")
    return path


def export_all(out_dir, basename="model"):
    """Export the model as GLB, FBX, OBJ and STL. Call after saving the .blend: it simplifies the
    materials for the formats that cannot hold node patterns. GLB and FBX carry the motion; OBJ and
    STL cannot, so they hold the model as it stands on frame 1."""
    os.makedirs(out_dir, exist_ok=True)
    objs = model_objects()
    if not objs:
        raise RuntimeError("there is no model in the scene to export")
    _plain_materials()
    _select(objs)
    out, errors = {}, {}
    jobs = {
        "glb": _export_gltf,
        "fbx": lambda p: bpy.ops.export_scene.fbx(filepath=p, use_selection=True, bake_anim=True,
                                                  use_custom_props=True, add_leaf_bones=False),
        "obj": lambda p: _first(lambda: bpy.ops.wm.obj_export(filepath=p, export_selected_objects=True),
                                lambda: bpy.ops.export_scene.obj(filepath=p, use_selection=True)),
        "stl": lambda p: _first(lambda: bpy.ops.wm.stl_export(filepath=p, export_selected_objects=True),
                                lambda: bpy.ops.export_mesh.stl(filepath=p, use_selection=True)),
    }
    for ext, fn in jobs.items():
        path = os.path.join(out_dir, f"{basename}.{ext}")
        try:
            fn(path)
            if os.path.isfile(path):
                out[ext] = path
            else:
                errors[ext] = "the exporter wrote no file"
        except Exception as exc:
            errors[ext] = str(exc)
    if errors:
        out["errors"] = errors
    return out
