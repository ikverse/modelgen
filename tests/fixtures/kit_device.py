"""A hand-held game device built with the device kit (runs inside Blender): one shell with a seam,
a screen in a bezel, a direction pad and buttons in wells, speaker slots and a battery door."""

from bpy_lib import helpers as h
from bpy_lib import kits as k
from bpy_lib import materials as m

W, H, D = 0.090, 0.150, 0.030
FRONT = -D / 2
WELL = 0.001


def build_shell():
    shell = k.device_shell(W, H, D, corner_radius=0.006, corners={"br": 0.018}, seam=(0.002, 0.0008, 0.0006))
    k.pocket(shell, (0.0, 0.105, 0.074, 0.064, 0.004), 0.0012)                     # the bezel's seat
    k.pocket(shell, (-0.024, 0.046, 0.028, 0.028, 0.014), WELL, slot="pocket")     # direction pad well
    for x, z in ((0.020, 0.040), (0.034, 0.050)):
        k.pocket(shell, (x, z, 0.0125, 0.0125, 0.00625), WELL, slot="pocket")     # button wells
    k.slots(shell, (0.030, 0.016), 5, (0.0016, 0.014), 0.0036, angle_deg=60, depth=0.0012)
    k.pocket(shell, (0.0, 0.040, 0.060, 0.050, 0.003), 0.0008, face="back")                # battery door
    k.pocket(shell, (0.0, 0.0, 0.012, 0.005, 0.002), 0.004, face="top", slot="slot")        # a port on top
    return shell


def build_bezel():
    bezel = k.plate((0.0, 0.105, 0.074, 0.064, 0.004), 0.0012, shell_depth=D, sink=0.0012, slot="trim", name="bezel")
    return k.pocket(bezel, (0.0, 0.107, 0.052, 0.044, 0.002), 0.0006)


def build_screen():
    return k.plate((0.0, 0.107, 0.052, 0.044, 0.002), 0.0004, shell_depth=D, sink=0.0006, slot="screen", name="screen")


def build_dpad():
    return k.dpad(0.024, 0.008, 0.003)


def build_button():
    return k.round_button(0.0055, 0.003, dome=0.35)


def assemble(parts):
    h.attach(parts["bezel"], "shell")
    h.attach(parts["screen"], "bezel")
    h.place(parts["dpad"], location=(-0.024, FRONT + WELL, 0.046))
    h.attach(parts["dpad"], "shell")
    button = parts["button"]
    for i, (x, z) in enumerate(((0.020, 0.040), (0.034, 0.050))):
        piece = button if i == 0 else h.instance(button)
        h.place(piece, location=(x, FRONT + WELL, z))
        h.attach(piece, "shell")


def apply(parts):
    grey = m.plastic(color=(0.74, 0.73, 0.70), roughness=0.5, name="case")
    dark = m.matte(color=(0.07, 0.07, 0.08), roughness=0.5, name="dark")
    well = m.matte(color=(0.55, 0.54, 0.52), roughness=0.6, name="well")
    for obj in parts["shell"]:
        m.assign_slot(obj, "shell", grey)
        m.assign_slot(obj, "seam", dark)
        m.assign_slot(obj, "slot", dark)
        m.assign_slot(obj, "pocket", well)
    for obj in parts["bezel"]:
        m.assign_slot(obj, "trim", m.plastic(color=(0.30, 0.31, 0.35), roughness=0.4, name="bezel"))
    for obj in parts["screen"]:
        m.assign_slot(obj, "screen", m.screen(color=(0.56, 0.62, 0.30), glow=0.4, name="lcd"))
    for obj in parts["dpad"]:
        m.assign_slot(obj, "button", m.plastic(color=(0.10, 0.10, 0.11), roughness=0.45, name="pad"))
    for obj in parts["button"]:
        m.assign_slot(obj, "button", m.plastic(color=(0.55, 0.10, 0.25), roughness=0.35, name="ab"))
