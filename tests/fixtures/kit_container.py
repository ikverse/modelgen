"""Tableware built with the container kit (runs inside Blender): a mug with a fused handle, a bowl
and a vase, each one hollow solid with an inside of its own colour."""

from bpy_lib import helpers as h
from bpy_lib import kits as k
from bpy_lib import materials as m


def build_mug():
    body = k.vessel([(0.037, 0.0), (0.0405, 0.003), (0.0415, 0.095)], wall=0.004, foot=(0.002, 0.004), name="mug")
    grip = k.handle(top_z=0.078, bottom_z=0.026, body_radius=0.041, reach=0.022)
    return k.fuse(body, [grip])


def build_bowl():
    outline = h.smooth_points([(0.030, 0.0), (0.055, 0.012), (0.070, 0.035), (0.075, 0.060)], samples=5)
    return k.vessel(outline, wall=0.004, foot=(0.004, 0.005), name="bowl")


def build_vase():
    outline = h.smooth_points([(0.030, 0.0), (0.050, 0.050), (0.045, 0.110), (0.022, 0.160), (0.020, 0.190),
                               (0.026, 0.210)], samples=5)
    return k.vessel(outline, wall=0.003, name="vase")


def assemble(parts):
    h.place(parts["bowl"], location=(0.17, 0.0, 0.0))
    h.place(parts["vase"], location=(-0.12, 0.0, 0.0))


def apply(parts):
    navy = m.ceramic(color=(0.12, 0.18, 0.35), roughness=0.2, glaze=0.8, name="navy")
    white = m.ceramic(color=(0.95, 0.94, 0.92), roughness=0.2, glaze=0.8, name="white")
    green = m.ceramic(color=(0.30, 0.45, 0.36), roughness=0.3, glaze=0.6, name="green")
    for name, outside in (("mug", navy), ("bowl", green), ("vase", white)):
        for obj in parts[name]:
            m.assign_slot(obj, "outside", outside)
            m.assign_slot(obj, "inside", white if name != "vase" else green)
