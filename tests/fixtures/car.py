"""A stylised R34-style coupe built only from the shell helpers and a fixed car sheet (numbers measured
off a side-view reference), for the Blender tests and as a worked example of a car."""

from bpy_lib import materials as m
from bpy_lib import shell
from bpy_lib.shells import car, car_archetypes, car_sheet

SHEET = {
    "style": "coupe", "length": 4.60, "width": 1.80, "height": 1.42,
    "wheelbase": 2.72, "front_overhang": 0.92, "wheel_diameter": 0.69, "wheel_width": 0.25, "track": 1.50,
    "ride_height": 0.15, "nose_z": 0.76, "cowl_z": 0.96, "windshield_base": -0.66, "roof_front": -0.20,
    "roof_rear": 0.92, "deck_front": 1.66, "deck_z": 1.08, "tail_z": 1.04, "belt_z": 0.97,
    "wing_y": 2.05, "look": "angular", "flare_style": "boxed",
}
S = car_sheet.resolve(SHEET)


def build_body():
    return car.body(SHEET)


def build_wheel():
    return shell.wheel(diameter=S["wheel_diameter"], width=S["wheel_width"], hub_reach=S["hub_reach"], spokes=6)


def build_side_mirror():
    return shell.mirror_housing()


def build_wing():
    return shell.wing(span=1.66, chord=0.30, height=0.32, upright_spacing=S["wing_spacing"])


def build_exhaust():
    return shell.exhaust(radius=0.05)


def assemble(parts):
    car.assemble(parts)


def apply(parts):
    m.car_finish(parts, paint=(0.07, 0.25, 0.68), style="stylised", accent=(0.45, 0.45, 0.47))


# --- one body per style, and the detail tools on a plain capsule ---------------------------------

def _style_body(style):
    def build():
        return car.body({"style": style})
    return build


for _style in car_sheet.STYLES:
    globals()[f"build_style_{_style}"] = _style_body(_style)


def _example_body(name):
    def build():
        return car.body(car_archetypes.ARCHETYPES[name]["sheet"])
    return build


for _name in car_archetypes.ARCHETYPES:
    globals()[f"build_example_{_name}"] = _example_body(_name)


# --- details that break the body: the build stops and names them ---------------------------------

def build_detail_that_wrecks_the_body():
    """A detail whose cut leaves a sliver of the body (a stand-in that deletes most faces, as a lamp
    outline running past the body's edge once did)."""
    import bmesh

    real = shell.apply_feature

    def apply(obj, feature, inner=None):
        if feature.get("name") != "plate":
            return real(obj, feature, inner)
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.calc_center_median().y < 1.5], context="FACES")
        bm.to_mesh(obj.data)
        bm.free()
        return obj

    shell.apply_feature = apply
    try:
        return car.body(car_archetypes.ARCHETYPES["city_car"]["sheet"])
    finally:
        shell.apply_feature = real


def build_detail_that_leaves_a_hole():
    """A detail whose cut leaves open edges however it is nudged (a stand-in: the real cut, then one
    face deleted, as a lamp whose mirrored halves met at x = 0 once did)."""
    import bmesh

    real = shell.apply_feature

    def apply(obj, feature, inner=None):
        real(obj, feature, inner)
        if feature.get("name") == "plate":
            bm = bmesh.new()
            bm.from_mesh(obj.data)
            bm.faces.ensure_lookup_table()
            bmesh.ops.delete(bm, geom=[bm.faces[0]], context="FACES_ONLY")
            bm.to_mesh(obj.data)
            bm.free()
        return obj

    shell.apply_feature = apply
    try:
        return car.body(car_archetypes.ARCHETYPES["city_car"]["sheet"])
    finally:
        shell.apply_feature = real


def build_details():
    """A capsule skin with one detail of every kind cut into it: framed recesses in each view (one
    mirrored), a nested recess, and grooves in the side and top views."""
    import math
    rings = []
    for i in range(11):
        y = -0.5 + i * 0.1
        r = 0.25 * math.sin(math.pi * (0.08 + 0.84 * i / 10))
        rings.append([(r * math.sin(2 * math.pi * k / 16), y, 0.3 + r * math.cos(2 * math.pi * k / 16))
                      for k in range(16)])
    obj = shell.skin(rings, levels=2, name="capsule")
    shell.recess(obj, "side", car_sheet.outline_points({"rect": [0.0, 0.32, 0.3, 0.12, 0.02]}), 0.012, "glass",
                 frame=0.02)
    shell.recess(obj, "front", car_sheet.outline_points({"circle": [0.07, 0.3, 0.04]}), 0.01, "lamp_front",
                 until=-0.2, mirror_x=True)
    shell.recess(obj, "rear", car_sheet.outline_points({"rect": [0.0, 0.3, 0.12, 0.06, 0.01]}), 0.008, "trim",
                 until=0.2)
    shell.recess(obj, "rear", car_sheet.outline_points({"circle": [0.0, 0.3, 0.02]}), 0.016, "lamp_rear", until=0.2)
    shell.recess(obj, "top", car_sheet.outline_points({"rect": [0.0, 0.1, 0.12, 0.2, 0.03]}), 0.01, "glass",
                 until=0.4, frame=0.015)
    shell.groove(obj, "side", [(-0.25, 0.22), (-0.25, 0.4)], 0.006)
    shell.groove(obj, "top", [(-0.08, -0.2), (0.08, -0.2)], 0.006)
    return obj
