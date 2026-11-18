"""The car body built from a reference (bpy_lib.shells.car_v2), for the Blender tests."""

from bpy_lib.shells import car_v2


def build_body_family_sedan():
    return car_v2.body("family_sedan")
