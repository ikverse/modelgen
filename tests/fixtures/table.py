"""A dining table built from fixed code with the helper library (runs inside Blender)."""

from bpy_lib import helpers as h
from bpy_lib import materials as m

TOP_Z = 0.72


def build_top():
    return h.box((1.2, 0.7, 0.03), anchor="bottom", name="top")


def build_leg():
    # the top of the leg sinks 5 mm into the table top
    return h.box((0.05, 0.05, TOP_Z + 0.005), anchor="bottom", name="leg")


def build_apron():
    # four rails under the top, their tops at the origin
    rails = [h.box((1.1, 0.02, 0.08), location=(0, y, -0.04), name="rail") for y in (-0.3, 0.3)]
    rails += [h.box((0.02, 0.6, 0.08), location=(x, 0, -0.04), name="rail") for x in (-0.55, 0.55)]
    return rails


def assemble(parts):
    h.place(parts["top"], location=(0, 0, TOP_Z))
    leg = parts["leg"]
    for i, (x, y) in enumerate([(-0.55, -0.3), (0.55, -0.3), (-0.55, 0.3), (0.55, 0.3)]):
        piece = leg if i == 0 else h.instance(leg)
        h.place(piece, location=(x, y, 0.0))
        h.attach(piece, "top")
        h.attach(piece, "apron")
    h.place(parts["apron"], location=(0, 0, TOP_Z))
    h.attach(parts["apron"], "top")
    h.attach(parts["apron"], "leg")


def apply(parts):
    walnut = m.wood(color=(0.3, 0.18, 0.1), grain_scale=6.0)
    black = m.painted(color=(0.05, 0.05, 0.05), wear=0.2)
    for obj in parts["top"]:
        m.assign(obj, walnut)
    for name in ("leg", "apron"):
        for obj in parts[name]:
            m.assign(obj, black)
