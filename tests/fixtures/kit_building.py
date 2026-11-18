"""A two-storey house and a glass tower built with the building kit (runs inside Blender): windows
and a door on all four walls from one layout, a gable roof, and a curtain-wall tower beside it."""

from bpy_lib import helpers as h
from bpy_lib import kits as k
from bpy_lib import materials as m

W, D = 8.0, 6.0
WALL_H = 6.1
REVEAL = 0.12
TOWER_X = 26.0


def layout():
    return k.openings(W, D, floors=2, floor_height=2.8, bays_front=3, bays_side=2, window=(1.0, 1.4), sill=0.9,
                      ground_floor=0.3, door={"bay": 1, "width": 1.0, "height": 2.2})


def build_walls():
    return k.walls(W, D, WALL_H, layout(), reveal=REVEAL, plinth=(0.3, 0.05))


def build_windows():
    return k.windows(layout(), W, D, reveal=REVEAL, bars=(2, 2))


def build_door():
    return k.door(layout(), W, D, reveal=REVEAL)


def build_roof():
    return k.roof(W, D, kind="gable", pitch_deg=40, overhang=0.35)


def build_tower():
    return k.curtain_tower(16.0, 16.0, 48.0, floor_height=3.6, bay=1.6, lobby=6.0)


def assemble(parts):
    h.attach(parts["windows"], "walls")
    h.attach(parts["door"], "walls")
    h.place(parts["roof"], location=(0, 0, WALL_H))
    h.attach(parts["roof"], "walls")
    h.place(parts["tower"], location=(TOWER_X, 0, 0))


def apply(parts):
    brick = m.brick(color=(0.50, 0.22, 0.16))
    stone = m.stone(color=(0.78, 0.76, 0.72), mottle=0.1)
    white = m.painted(color=(0.93, 0.93, 0.91), roughness=0.4, wear=0.0, name="white")
    pane = m.window_glass()
    for obj in parts["walls"]:
        m.assign_slot(obj, "wall", brick)
        m.assign_slot(obj, "reveal", stone)
        m.assign_slot(obj, "plinth", stone)
    for obj in parts["windows"]:
        m.assign_slot(obj, "frame", white)
        m.assign_slot(obj, "glass", pane)
        m.assign_slot(obj, "sill", stone)
    for obj in parts["door"]:
        m.assign_slot(obj, "door", m.painted(color=(0.12, 0.30, 0.20), roughness=0.35, wear=0.0, name="door"))
        m.assign_slot(obj, "frame", white)
        m.assign_slot(obj, "metal", m.polished_metal(color=(0.75, 0.62, 0.35), name="brass"))
    for obj in parts["roof"]:
        m.assign_slot(obj, "roof", m.roof_tiles(color=(0.20, 0.21, 0.25), name="slate"))
        m.assign_slot(obj, "gable", brick)
    for obj in parts["tower"]:
        m.assign_slot(obj, "glass", m.window_glass(color=(0.12, 0.22, 0.32), name="tower_glass"))
        m.assign_slot(obj, "band", m.concrete(color=(0.82, 0.81, 0.78), name="band"))
        m.assign_slot(obj, "mullion", m.brushed_metal(color=(0.25, 0.26, 0.28), roughness=0.35, name="mullion"))
