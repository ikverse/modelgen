"""The animation clips a character can be given: the free libraries in assets_cc0/animations (the
Quaternius Universal Animation Library, CC0), read without Blender from the GLB's own index.

A character sheet's `clips` is "default" (an everyday set), "all", or a list of clip names; the names
are the library's, matched case-blind. bpy_lib.retarget moves the chosen clips onto the character's
skeleton during the build.
"""

import functools
import json
import struct

from .. import config

LIBRARIES = {"quaternius_ual": "assets_cc0/animations/quaternius_ual/Unreal-Godot/UAL1_Standard.glb"}
DEFAULT = ("Idle_Loop", "Walk_Loop", "Jog_Fwd_Loop", "Sprint_Loop", "Jump_Start", "Jump_Loop", "Jump_Land",
           "Punch_Jab", "Idle_Talking_Loop", "Dance_Loop", "Sitting_Idle_Loop", "Crouch_Idle_Loop")
SKIP = ("A_TPose",)  # the library's rest pose, not a motion


class ClipError(ValueError):
    pass


def library_path(name="quaternius_ual"):
    return config.ROOT / LIBRARIES[name]


@functools.lru_cache(maxsize=4)
def _names(path):
    data = path.read_bytes()
    size = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20:20 + size])
    return tuple(a["name"] for a in gltf.get("animations", []) if a["name"] not in SKIP)


def available():
    """The clip names of the library on this PC ([] when it is not there)."""
    path = library_path()
    return list(_names(path)) if path.is_file() else []


def clean(raw):
    """The clips a sheet asks for, as the library's own names. "default" or nothing: the everyday
    set; "all": every clip; a list: those names (case-blind); an empty list: none. Raises ClipError
    naming the clip."""
    names = available()
    if not names:
        return []
    if raw in (None, "", "default"):
        return [n for n in DEFAULT if n in names]
    if raw == "all":
        return names
    if not isinstance(raw, list):
        raise ClipError('character.clips must be "default", "all" or a list of clip names')
    if not raw:
        return []  # no library clips: the character gets only the short rig_test loop
    by_key = {n.lower(): n for n in names}
    out = []
    for item in raw:
        key = str(item).strip().lower()
        if key not in by_key:
            close = [n for n in names if key.split("_")[0] in n.lower()][:5]
            raise ClipError(f"character.clips has no clip {item!r}" + (f"; for example {', '.join(close)}" if close
                                                                      else f"; GET /v1/characters lists them"))
        if by_key[key] not in out:
            out.append(by_key[key])
    return out


def nice(name):
    """The name a clip gets on the character (bpy_lib.retarget._nice): lower case, underscores."""
    import re
    return re.sub(r"[^a-z0-9]+", "_", name.split("|")[-1].lower()).strip("_") or "clip"
