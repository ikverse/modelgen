"""A ceramic mug built from fixed code with the helper library (runs inside Blender)."""

from bpy_lib import helpers as h
from bpy_lib import materials as m


def build_body():
    # outer wall up to the rim, inner wall down, inner floor back to the axis
    profile = [(0.0, 0.0), (0.036, 0.0), (0.039, 0.002), (0.040, 0.006), (0.042, 0.095),
               (0.0385, 0.095), (0.0365, 0.014), (0.032, 0.009), (0.0, 0.009)]
    return h.revolve(profile, segments=96, name="body")


def build_handle():
    path = [(0.040, 0.0, 0.078), (0.060, 0.0, 0.080), (0.073, 0.0, 0.062), (0.071, 0.0, 0.038),
            (0.057, 0.0, 0.024), (0.040, 0.0, 0.024)]
    return h.sweep(path, radius=0.0055, profile_segments=24, name="handle")


def assemble(parts):
    h.attach(parts["handle"], "body")


def apply(parts):
    glaze = m.ceramic(color=(0.86, 0.84, 0.78), glaze=0.8, speckle=0.3)
    for name in ("body", "handle"):
        for obj in parts[name]:
            m.assign(obj, glaze)
