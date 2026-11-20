"""Clips from a free animation library onto a character's game skeleton. Runs inside Blender only.

A library (a GLB or FBX with an armature and its actions) is imported beside the character. Its
bones are matched to the character's by name (`match`: Quaternius, Mixamo and Unreal-style names
all read as the same body parts), and every clip is transferred bone by bone in world space:

    target_world = source_delta * rest_fix * target_rest_world

where source_delta is how far the source bone has turned from its own rest this frame and rest_fix
turns the target bone's rest direction onto the source bone's rest direction (a library made in a
T-pose drives a character built in an A-pose without the arms ending up in the body). The hips'
travel is scaled by the ratio of the two hip heights, so a short character takes shorter steps.
The result is one action per clip on the character's skeleton, named after the clip.
"""

import math
import re

import bpy
from mathutils import Matrix, Quaternion, Vector

# the game skeleton's bones and the words a library's names use for each
PARTS = {
    "pelvis": ["pelvis", "hips", "hip"], "spine_01": ["spine", "spine_01", "spine1", "spine01"],
    "spine_02": ["spine_02", "spine2", "spine02", "chest"], "spine_03": ["spine_03", "spine3", "spine03", "upperchest",
                                                                         "upper_chest"],
    "neck_01": ["neck", "neck_01", "neck1"], "head": ["head"],
    "clavicle": ["clavicle", "shoulder", "collar"], "upperarm": ["upperarm", "upper_arm", "arm", "uparm"],
    "lowerarm": ["lowerarm", "lower_arm", "forearm", "elbow"], "hand": ["hand", "wrist"],
    "thigh": ["thigh", "upperleg", "upper_leg", "upleg"], "calf": ["calf", "lowerleg", "lower_leg", "shin", "leg", "knee"],
    "foot": ["foot", "ankle"], "ball": ["ball", "toe", "toes", "toebase"],
    "thumb": ["thumb"], "index": ["index"], "middle": ["middle"], "ring": ["ring"], "pinky": ["pinky", "little"],
}
SIDED = ("clavicle", "upperarm", "lowerarm", "hand", "thigh", "calf", "foot", "ball", "thumb", "index", "middle",
         "ring", "pinky")
FINGERS = ("thumb", "index", "middle", "ring", "pinky")
REST_FIXED = ("clavicle", "upperarm", "lowerarm", "hand") + FINGERS  # bones whose rest directions are matched


def _clean(name):
    name = re.sub(r"^(mixamorig\d*[:_]|def[-_]|bip\d*[ _]|armature[|:])", "", name.lower())
    return re.sub(r"[\s.\-]+", "_", name)


def _side(name):
    n = _clean(name)
    if re.search(r"(^|_)(l|left)(_|$)|_l$|left|\.l$", n) or n.endswith("l") and n[-2:-1] in ("_",):
        return "l"
    if re.search(r"(^|_)(r|right)(_|$)|_r$|right|\.r$", n):
        return "r"
    return ""


def _part(name):
    """(part, side, finger joint number) a library bone name reads as, or None."""
    n = _clean(name)
    side = _side(name)
    words = re.sub(r"(^|_)(l|r|left|right)(_|$)", "_", n).strip("_")
    words = words.replace("left", "").replace("right", "")
    digits = re.findall(r"\d+", words)
    for part in FINGERS:
        if any(w in words for w in PARTS[part]):
            num = int(digits[-1]) if digits else 1
            return part, side, num
    order = ["upperchest", "upper_chest", "spine_03", "spine3", "spine03", "spine_02", "spine2", "spine02", "chest",
             "spine_01", "spine1", "spine01", "spine"]
    for w in order:
        if w in words:
            for part in ("spine_03", "spine_02", "spine_01"):
                if w in PARTS[part]:
                    return part, "", 0
    for part in ("lowerarm", "upperarm", "clavicle", "hand", "thigh", "calf", "foot", "ball", "neck_01", "head",
                 "pelvis"):
        if any(re.search(rf"(^|_){re.escape(w)}(_|$|\d)", words) or words.endswith(w) for w in PARTS[part]):
            return part, side, 0
    return None


def target_name(part, side, num=0):
    if part in FINGERS:
        return f"{part}_0{max(1, min(3, num))}_{side}"
    if part in SIDED:
        return f"{part}_{side}"
    return part


def match(source_names, target_names):
    """{target bone: source bone} for every target bone a source bone reads as."""
    out = {}
    fingers = {}
    for s in source_names:
        p = _part(s)
        if p is None:
            continue
        part, side, num = p
        if part in SIDED and not side:
            continue
        if part in FINGERS:
            fingers.setdefault((part, side), []).append((num, s))
            continue
        t = target_name(part, side)
        if t in target_names and t not in out:
            out[t] = s
    for (part, side), joints in fingers.items():  # a finger's joints in order, whatever they are numbered from
        for i, (_, s) in enumerate(sorted(joints)[:3]):
            t = target_name(part, side, i + 1)
            if t in target_names:
                out[t] = s
    return out


def import_library(path):
    """Import a GLB or FBX animation library; returns its armature and its actions."""
    before = set(bpy.data.objects)
    acts_before = set(bpy.data.actions)
    if path.lower().endswith((".glb", ".gltf")):
        bpy.ops.import_scene.gltf(filepath=path)
    else:
        bpy.ops.import_scene.fbx(filepath=path, automatic_bone_orientation=True)
    new = [o for o in bpy.data.objects if o not in before]
    arm = next((o for o in new if o.type == "ARMATURE"), None)
    if arm is None:
        raise RuntimeError(f"no skeleton in {path}")
    for o in new:  # the library's own meshes are not wanted
        if o.type == "MESH":
            bpy.data.objects.remove(o, do_unlink=True)
    actions = [a for a in bpy.data.actions if a not in acts_before]
    return arm, actions


def _rest_world(arm, name):
    return (arm.matrix_world @ arm.data.bones[name].matrix_local).to_quaternion()


def _rest_dir(arm, name):
    b = arm.data.bones[name]
    return (arm.matrix_world.to_3x3() @ (b.tail_local - b.head_local)).normalized()


def transfer(source, target, clip, mapping, fps=30, name=None):
    """Bake one source action onto the target skeleton as a new action; returns its frame count."""
    scene = bpy.context.scene
    source.animation_data_create()
    source.animation_data.action = clip
    start, end = (int(round(v)) for v in clip.frame_range)
    target.animation_data_create()
    act = bpy.data.actions.new(name or clip.name)
    act.use_fake_user = True  # kept in the .blend and exported though only one clip is on the skeleton at a time
    target.animation_data.action = act
    order = [b.name for b in target.data.bones if b.name in mapping]  # parents before children
    fix, src_rest, tgt_rest = {}, {}, {}
    for t in order:
        s = mapping[t]
        src_rest[t] = _rest_world(source, s)
        tgt_rest[t] = _rest_world(target, t)
        # only the arms stand differently at rest (a T-pose library, an A-pose character); a body
        # bone takes the library's turns as they are, so what hangs from it (a skirt's spring chains
        # on the pelvis, a ponytail on the head) keeps its frame
        fix[t] = (_rest_dir(target, t).rotation_difference(_rest_dir(source, s)) if t.startswith(REST_FIXED)
                  else Quaternion())
    hip_t, hip_s = mapping.get("pelvis"), None
    scale = 1.0
    if hip_t:
        hip_s = hip_t
        ht = (target.matrix_world @ target.data.bones["pelvis"].head_local).z
        hs = (source.matrix_world @ source.data.bones[mapping["pelvis"]].head_local).z
        scale = ht / hs if hs > 1e-6 else 1.0
        src_hip0 = source.matrix_world @ source.data.bones[mapping["pelvis"]].head_local
        tgt_hip0 = target.matrix_world @ target.data.bones["pelvis"].head_local
    for pb in target.pose.bones:
        pb.rotation_mode = "QUATERNION"
    inv_t = target.matrix_world.inverted()
    for f in range(start, end + 1):
        scene.frame_set(f)
        for pb in target.pose.bones:
            pb.rotation_quaternion = Quaternion()
            pb.location = (0, 0, 0)
        bpy.context.view_layer.update()
        for t in order:
            s = mapping[t]
            spb = source.pose.bones[s]
            src_world = (source.matrix_world @ spb.matrix).to_quaternion()
            delta = src_world @ src_rest[t].inverted()
            want = delta @ fix[t] @ tgt_rest[t]
            tpb = target.pose.bones[t]
            m = Matrix.Translation(tpb.matrix.translation) @ (inv_t.to_quaternion() @ want).to_matrix().to_4x4()
            if t == "pelvis" and hip_s:
                src_hip = source.matrix_world @ spb.head
                move = (src_hip - src_hip0) * scale
                m = Matrix.Translation(inv_t @ (tgt_hip0 + move)) @ (inv_t.to_quaternion() @ want).to_matrix().to_4x4()
            tpb.matrix = m
            bpy.context.view_layer.update()
            tpb.keyframe_insert("rotation_quaternion", frame=f - start + 1)
            if t == "pelvis":
                tpb.keyframe_insert("location", frame=f - start + 1)
    return end - start + 1


def library_clips(path, target, wanted=None, fps=30):
    """Every clip of a library (or only those named in `wanted`, matched case-blind) transferred onto
    the target skeleton. Returns [{"name", "frames", "fps"}] and removes the library again."""
    source, actions = import_library(path)
    mapping = match([b.name for b in source.data.bones], {b.name for b in target.data.bones})
    if "pelvis" not in mapping or len(mapping) < 12:
        raise RuntimeError(f"could not match the library's skeleton ({len(mapping)} bones matched)")
    out = []
    pick = {w.lower(): w for w in (wanted or [])}
    for act in actions:
        key = act.name.lower()
        if wanted and key not in pick:
            continue
        frames = transfer(source, target, act, mapping, fps, name=_nice(act.name))
        out.append({"name": _nice(act.name), "frames": frames, "fps": fps, "source": act.name})
    bpy.data.objects.remove(source, do_unlink=True)
    for act in actions:
        bpy.data.actions.remove(act)
    bpy.context.scene.render.fps = fps
    return out, mapping


def _nice(name):
    """A clip name a person can read: the library's prefixes and armature names dropped."""
    name = name.split("|")[-1]
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "clip"
