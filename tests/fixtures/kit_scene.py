"""A small park built with the scene kit (runs inside Blender): a ground slab with two kinds of
tree, a bench, a fence round it and lamp posts."""

from bpy_lib import helpers as h
from bpy_lib import kits as k
from bpy_lib import materials as m

GROUND = 0.1


def build_ground():
    return h.box((12.0, 10.0, GROUND), anchor="bottom", name="ground")


def build_tree_round():
    return k.tree(4.2, kind="round", seed=3)


def build_tree_conifer():
    return k.tree(5.0, kind="conifer", seed=5)


def build_bench():
    return k.bench(1.6)


def build_fence():
    return k.fence([(-5.8, -4.8), (5.8, -4.8), (5.8, 4.8), (-5.8, 4.8), (-5.8, -4.8)], height=1.0, post_spacing=1.9)


def build_lamp_post():
    return k.lamp_post(3.6)


def assemble(parts):
    spots = {"tree_round": (-3.8, 2.6), "tree_conifer": (4.2, 2.8), "bench": (0.0, 3.4), "fence": (0.0, 0.0),
             "lamp_post": (-1.8, 3.7)}
    for name, (x, y) in spots.items():
        h.place(parts[name], location=(x, y, GROUND))
        h.attach(parts[name], "ground")
    second = h.instance(parts["lamp_post"], location=(1.8, 3.7, GROUND))
    h.attach(second, "ground")


def apply(parts):
    for obj in parts["ground"]:
        m.assign(obj, m.grass())
    bark = m.matte(color=(0.30, 0.21, 0.14), roughness=0.9, name="bark")
    for name, leaf in (("tree_round", (0.22, 0.46, 0.20)), ("tree_conifer", (0.12, 0.33, 0.20))):
        for obj in parts[name]:
            m.assign_slot(obj, "trunk", bark)
            m.assign_slot(obj, "leaves", m.foliage(color=leaf, name=f"{name}_leaves"))
    for obj in parts["bench"]:
        m.assign_slot(obj, "wood", m.wood(color=(0.50, 0.33, 0.18), grain_scale=12.0, name="slats"))
        m.assign_slot(obj, "metal", m.painted(color=(0.10, 0.11, 0.12), roughness=0.5, wear=0.0, name="iron"))
    white = m.painted(color=(0.92, 0.92, 0.90), roughness=0.5, wear=0.0, name="fence_white")
    for obj in parts["fence"]:
        m.assign_slot(obj, "post", white)
        m.assign_slot(obj, "rail", white)
    for obj in parts["lamp_post"]:
        m.assign_slot(obj, "metal", m.painted(color=(0.08, 0.09, 0.10), roughness=0.45, wear=0.0, name="lamp_iron"))
        m.assign_slot(obj, "lamp", m.lamp(color=(1.0, 0.93, 0.75), glow=2.0, name="lantern"))
