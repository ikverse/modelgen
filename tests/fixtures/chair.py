"""A simple dining chair built from fixed code with the helper library (runs inside Blender)."""

from bpy_lib import helpers as h
from bpy_lib import materials as m

SEAT_Z = 0.43


def build_seat():
    seat = h.box((0.44, 0.42, 0.03), anchor="bottom", name="seat")
    return h.bevel(seat, 0.006, segments=3)


def build_leg():
    # tapered: thinner at the foot; the top sinks 5 mm into the seat
    return h.cylinder(0.016, 0.435, segments=48, anchor="bottom", radius_top=0.019, name="leg")


def build_back():
    pieces = [h.box((0.03, 0.03, 0.45), location=(x, 0, 0), anchor="bottom", name="post") for x in (-0.19, 0.19)]
    for z in (0.22, 0.31, 0.40):
        pieces.append(h.box((0.38, 0.02, 0.05), location=(0, 0, z), anchor="bottom", name="slat"))
    return pieces


def assemble(parts):
    h.place(parts["seat"], location=(0, 0, SEAT_Z))
    leg = parts["leg"]
    for i, (x, y) in enumerate([(-0.19, -0.17), (0.19, -0.17), (-0.19, 0.17), (0.19, 0.17)]):
        piece = leg if i == 0 else h.instance(leg)
        h.place(piece, location=(x, y, 0.0))
        h.attach(piece, "seat")
    h.place(parts["back"], location=(0, 0.18, SEAT_Z + 0.025))
    h.attach(parts["back"], "seat")


def apply(parts):
    oak = m.wood(color=(0.45, 0.29, 0.16), grain_scale=10.0)
    for name in ("seat", "leg", "back"):
        for obj in parts[name]:
            m.assign(obj, oak)
