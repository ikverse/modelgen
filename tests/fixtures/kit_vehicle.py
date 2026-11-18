"""A simple bus built with the vehicle kit (runs inside Blender): one body with its windows, door
and lamps sunk into it and wheel arches cut into its sides, standing on kit wheels, the rear ones
doubled."""

from bpy_lib import helpers as h
from bpy_lib import kits as k
from bpy_lib import materials as m

W, L, BODY_H, FLOOR = 2.4, 8.0, 2.65, 0.28
RADIUS, TYRE = 0.48, 0.28
AXLES = (-2.6, 2.3)


def build_body():
    top = FLOOR + BODY_H
    body = k.vehicle_body(W, L, top, floor=FLOOR, roof_radius=0.22,
                          windows=(8, -L / 2 + 1.5, L / 2 - 0.4, FLOOR + 1.35, FLOOR + 2.15),
                          windshield=(FLOOR + 1.1, FLOOR + 2.2), rear_window=(FLOOR + 1.35, FLOOR + 2.15),
                          doors=[("right", -L / 2 + 0.75, 0.9, FLOOR + 0.05, FLOOR + 2.25)],
                          lamps=[("front", -0.85, FLOOR + 0.6, 0.22, "lamp_clear"),
                                 ("front", 0.85, FLOOR + 0.6, 0.22, "lamp_clear"),
                                 ("back", -0.9, FLOOR + 0.7, (0.2, 0.3), "lamp_red"),
                                 ("back", 0.9, FLOOR + 0.7, (0.2, 0.3), "lamp_red"),
                                 ("back", -0.9, FLOOR + 1.05, 0.16, "lamp_amber"),
                                 ("back", 0.9, FLOOR + 1.05, 0.16, "lamp_amber")])
    k.pocket(body, (0.0, FLOOR + 0.45, 1.2, 0.35, 0.05), 0.03, face="front", slot="trim")
    k.wheel_arches(body, AXLES, RADIUS + 0.09, z=RADIUS, depth=0.75)
    return body


def build_wheel_front():
    return k.wheel(RADIUS, TYRE, style="steel", hub_reach=0.5, name="wheel_front")


def build_wheel_rear():
    return k.wheel(RADIUS, TYRE, style="steel", dual=True, hub_reach=0.2, name="wheel_rear")


def assemble(parts):
    track = W - TYRE - 0.04
    for name, y in (("wheel_front", AXLES[0]), ("wheel_rear", AXLES[1])):
        wheel = parts[name]
        for i, spot in enumerate(k.axle_positions([y], track, RADIUS)):
            piece = wheel if i == 0 else h.instance(wheel)
            h.place(piece, location=spot["location"], rotation_deg=spot["rotation_deg"])
            h.attach(piece, "body")


def apply(parts):
    paint = m.car_paint(color=(0.90, 0.62, 0.05), metallic=0.0, flake=0.0, name="paint")
    for obj in parts["body"]:
        m.assign_slot(obj, "body", paint)
        m.assign_slot(obj, "glass", m.window_glass())
        m.assign_slot(obj, "trim", m.matte(color=(0.05, 0.05, 0.055), roughness=0.5, name="grille"))
        m.assign_slot(obj, "door", m.car_paint(color=(0.82, 0.55, 0.04), metallic=0.0, flake=0.0, name="door"))
        m.assign_slot(obj, "lamp_clear", m.lamp(color=(0.96, 0.96, 0.90), name="headlamp"))
        m.assign_slot(obj, "lamp_red", m.lamp(color=(0.80, 0.05, 0.05), name="tail"))
        m.assign_slot(obj, "lamp_amber", m.lamp(color=(1.0, 0.62, 0.05), name="indicator"))
    for name in ("wheel_front", "wheel_rear"):
        for obj in parts[name]:
            m.assign_slot(obj, "tyre", m.rubber(name="tyre"))
            m.assign_slot(obj, "rim", m.painted(color=(0.75, 0.76, 0.78), roughness=0.4, wear=0.0, name="rim"))


def build_hood():
    """A sloping engine hood with grille, headlights and fenders, as a bus or truck nose."""
    hood = k.hood(2.1, 1.5, 1.4, floor=0.5, grille=(0.9, 0.45, 0.95),
                  lamps=[(-0.8, 0.95, 0.2, "lamp_clear"), (0.8, 0.95, 0.2, "lamp_clear")], fenders=(-0.1, RADIUS))
    k.wheel_arches(hood, [-0.1], RADIUS + 0.08, z=RADIUS, depth=0.6)
    return hood

