"""Secondary motion for a character: spring chains for what swings (a ponytail, long hair, a skirt,
a dress's skirt, coat tails), simulated by ModelGen's own engine (modelgen/physics) and baked into
every clip. Runs inside Blender only.

For each swinging piece, chains of extra bones are added to the skeleton (named spring_<piece>_<k>
_<i>), the piece's skin weights move onto them, and each clip is played through twice in the
simulation: the second pass is keyed onto the chain bones, so the clip still loops. Capsule colliders
fitted to the body's bones keep hair and cloth out of it. Each chain's root bone carries its settings
as mg_spring (stiffness, drag, gravity, radius), exported as extras, so an engine (or the viewer) can
run the springs live instead of the baked keys.
"""

import json
import math

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

from physics import engine as ph

from . import character_body as cb

SPRING = "spring"
# per piece kind: chains, bones per chain, stiffness, drag, gravity (shares of the engine's), parent bone
KINDS = {
    "ponytail": {"chains": 1, "bones": 4, "stiffness": 0.18, "drag": 0.08, "gravity": 1.0, "parent": "head"},
    "hair_strands": {"chains": 6, "bones": 3, "stiffness": 0.25, "drag": 0.1, "gravity": 0.8, "parent": "head"},
    "skirt": {"chains": 12, "bones": 5, "stiffness": 0.12, "drag": 0.15, "gravity": 0.8, "parent": "pelvis"},
    "tails": {"chains": 12, "bones": 5, "stiffness": 0.12, "drag": 0.15, "gravity": 0.8, "parent": "pelvis"},
}
LEG_MARGIN = 1.25  # leg colliders a quarter fatter than the leg: a hem clears the knee as it comes up
COLLIDER_BONES = ("pelvis", "spine_01", "spine_02", "spine_03", "neck_01", "head", "thigh_l", "thigh_r", "calf_l",
                  "calf_r", "upperarm_l", "upperarm_r", "lowerarm_l", "lowerarm_r")


def _kind(obj):
    name = obj.name.split(".")[0]
    if name == "ponytail":
        return "ponytail"
    if name == "hair_strands":
        return "hair_strands"
    if name.endswith("_tails"):
        return "tails"
    if name == "skirt" or name.endswith("_skirt"):
        return "skirt"
    return None


def _coords(obj):
    me = obj.data
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


# --- chains ----------------------------------------------------------------------------------------

def _plan_chains(obj, kind, rig):
    """Where a piece's chains run: [[joint positions, root first], ...] in the rig's space, and for
    every vertex (chain position around, position along) to weight it."""
    co = _coords(obj)
    spec = KINDS[kind]
    if kind == "ponytail":  # one chain down the tube's middle, from its root ring to its tip
        order = np.argsort(-co[:, 2])
        top, bottom = co[order[: max(4, len(co) // 12)]].mean(axis=0), co[order[-max(4, len(co) // 12):]].mean(axis=0)
        n = spec["bones"]
        joints = [tuple(top + (bottom - top) * i / n) for i in range(n + 1)]
        along = np.clip((top[2] - co[:, 2]) / max(top[2] - bottom[2], 1e-6), 0, 1)
        return [joints], np.zeros(len(co)), along
    # hanging pieces: chains spread round the piece's axis, from its top ring to its bottom
    centre = np.array([co[:, 0].mean(), co[:, 1].mean()])
    top_z, low_z = float(co[:, 2].max()), float(co[:, 2].min())
    ang = np.arctan2(co[:, 1] - centre[1], co[:, 0] - centre[0])
    n_chain, n_bone = spec["chains"], spec["bones"]
    if kind == "hair_strands":  # only behind and beside the head
        span = (-math.pi * 0.05, math.pi * 1.05)
    elif kind == "tails" and co[:, 1].mean() > centre[1]:
        span = (0.0, math.pi)
    else:
        span = (-math.pi, math.pi)
    full = span == (-math.pi, math.pi)
    chains = []
    for k in range(n_chain):
        a = span[0] + (span[1] - span[0]) * (k + (0.5 if full else 0.0)) / (n_chain if full else max(n_chain - 1, 1))
        sel = np.abs(np.angle(np.exp(1j * (ang - a)))) < math.pi / n_chain * 1.2
        pts = co[sel] if sel.sum() >= 3 else co
        joints = []
        for i in range(n_bone + 1):
            z = top_z - (top_z - low_z) * i / n_bone
            ring = pts[np.abs(pts[:, 2] - z) < (top_z - low_z) / (n_bone * 1.5)]
            if len(ring):
                r = float(np.median(np.hypot(ring[:, 0] - centre[0], ring[:, 1] - centre[1])))
            else:
                r = float(np.median(np.hypot(pts[:, 0] - centre[0], pts[:, 1] - centre[1])))
            joints.append((centre[0] + r * math.cos(a), centre[1] + r * math.sin(a), z))
        chains.append((a, joints))
    along = np.clip((top_z - co[:, 2]) / max(top_z - low_z, 1e-6), 0, 1)
    return [j for _, j in chains], (ang, [a for a, _ in chains], span, full), along


def _add_bones(rig, parent, piece, chains):
    """Edit-mode bones for the chains, each under the one before, the first under `parent`."""
    bpy.context.view_layer.objects.active = rig
    for o in bpy.context.scene.objects:
        o.select_set(o is rig)
    bpy.ops.object.mode_set(mode="EDIT")
    arm = rig.data
    names = []
    for k, joints in enumerate(chains):
        prev = arm.edit_bones.get(parent)
        row = []
        for i in range(len(joints) - 1):
            eb = arm.edit_bones.new(f"{SPRING}_{piece}_{k}_{i}")
            eb.head, eb.tail = Vector(joints[i]), Vector(joints[i + 1])
            if (eb.tail - eb.head).length < 1e-4:
                eb.tail = eb.head + Vector((0, 0, -0.01))
            eb.parent = prev
            eb.use_connect = i > 0
            eb.use_deform = True
            prev = eb
            row.append(eb.name)
        names.append(row)
    bpy.ops.object.mode_set(mode="OBJECT")
    return names


def _reweight(obj, kind, names, layout, along, keep_top=0.15):
    """Move the piece's weights onto its chain bones by where each vertex sits round the chains and
    along them; the top keeps its own weights, blending into the chains below it."""
    groups = {g.name: g for g in obj.vertex_groups}
    for row in names:
        for n in row:
            groups[n] = obj.vertex_groups.new(name=n)
    n_bone = len(names[0])
    for v in obj.data.vertices:
        s = float(along[v.index])
        if kind == "ponytail":
            chain_w = [(0, 1.0)]
        else:
            ang, angles, span, full = layout
            a = float(ang[v.index])
            dists = [abs(math.atan2(math.sin(a - c), math.cos(a - c))) for c in angles]
            order = np.argsort(dists)[:2]
            d0, d1 = dists[order[0]], dists[order[1]]
            w0 = 1.0 if d0 + d1 < 1e-9 else d1 / (d0 + d1)
            chain_w = [(int(order[0]), w0), (int(order[1]), 1 - w0)]
        b = min(s * n_bone, n_bone - 1e-6)
        b0 = int(b)
        f = b - b0
        # the last bone takes the end of the chain whole (its weight would fade to nothing otherwise)
        bone_w = [(b0, 1 - f), (b0 + 1, f)] if b0 + 1 < n_bone else [(b0, 1.0)]
        blend = 1.0 if kind == "ponytail" else min(1.0, max(0.0, (s - keep_top * 0.5) / keep_top))
        old = [(obj.vertex_groups[g.group].name, g.weight) for g in v.groups]
        new = {}
        for name, w in old:
            new[name] = new.get(name, 0.0) + w * (1 - blend)
        for ci, cw in chain_w:
            for bi, bw in bone_w:
                name = names[ci][bi]
                new[name] = new.get(name, 0.0) + blend * cw * bw
        top = sorted(new.items(), key=lambda kv: -kv[1])[: cb.MAX_INFLUENCES]
        total = sum(w for _, w in top) or 1.0
        for name, _ in old:
            groups[name].remove([v.index])
        for name, w in top:
            if w > 1e-4:
                groups[name].add([v.index], w / total, "REPLACE")


def add_springs(rig, pieces):
    """Chains for every swinging piece: bones added, weights moved. Returns the chains as
    [{"piece", "kind", "bones": [[names root first], ...], "settings"}]."""
    made = []
    # a coat's tails first: a skirt under them hangs from the same chains, so the two never cross
    pieces = sorted(pieces, key=lambda o: 0 if _kind(o) == "tails" else 1)
    outer = None
    for obj in pieces:
        kind = _kind(obj)
        if kind is None:
            continue
        if kind == "ponytail" and not obj.vertex_groups.get("head"):
            continue
        chains, layout, along = _plan_chains(obj, kind, rig)
        spec = KINDS[kind]
        piece = obj.name.split(".")[0]
        if kind == "skirt" and outer is not None:
            _, (o_angles, o_span, o_full) = outer["names"], outer["layout"][1:]
            _reweight(obj, kind, outer["names"], (layout[0], o_angles, o_span, o_full), along)
            continue
        names = _add_bones(rig, spec["parent"], piece, chains)
        _reweight(obj, kind, names, layout, along)
        if kind == "tails":
            outer = {"names": names, "layout": layout}
        settings = {k: spec[k] for k in ("stiffness", "drag", "gravity")}
        # a joint's collision radius: the cloth's own distance from the skin, so the hem between joints clears it
        settings["radius"] = 0.02 if kind == "ponytail" else 0.03
        for row in names:
            rig.data.bones[row[0]]["mg_spring"] = json.dumps({**settings, "chain": row})
        made.append({"piece": piece, "kind": kind, "bones": names, "settings": settings})
    return made


# --- colliders ---------------------------------------------------------------------------------------

def fit_colliders(rig, body):
    """A capsule per main body bone, its radius the typical distance of the bone's own skin from it."""
    out = {}
    co = _coords(body)
    names = [g.name for g in body.vertex_groups]
    main = np.full(len(co), -1)
    best = np.zeros(len(co))
    for v in body.data.vertices:
        for g in v.groups:
            if g.weight > best[v.index]:
                best[v.index], main[v.index] = g.weight, g.group
    for bone in COLLIDER_BONES:
        if bone not in names or bone not in rig.data.bones:
            continue
        idx = np.where(main == names.index(bone))[0]
        if len(idx) < 8:
            continue
        b = rig.data.bones[bone]
        a, t = np.array(b.head_local), np.array(b.tail_local)
        ab = t - a
        s = np.clip(((co[idx] - a) @ ab) / max(ab @ ab, 1e-9), 0, 1)
        d = np.linalg.norm(co[idx] - (a + s[:, None] * ab), axis=1)
        out[bone] = float(np.percentile(d, 60)) * (LEG_MARGIN if bone.startswith(("thigh", "calf")) else 0.95)
    return out


def _capsules(rig, radii):
    caps = []
    for bone, r in radii.items():
        pb = rig.pose.bones[bone]
        caps.append(ph.Capsule(tuple(rig.matrix_world @ pb.head), tuple(rig.matrix_world @ pb.tail), r))
    return caps


# --- baking --------------------------------------------------------------------------------------------

def _targets(rig, row):
    """Where the animation puts a chain's joints this frame: its bones at rest under their posed
    parents, joint by joint."""
    pts = []
    for name in row:
        pb = rig.pose.bones[name]
        pts.append(tuple(rig.matrix_world @ pb.head))
    pts.append(tuple(rig.matrix_world @ rig.pose.bones[row[-1]].tail))
    return pts


def _aim(rig, row, positions):
    """Turn each chain bone so it points at the next simulated joint (the bones before it already
    turned), and key it."""
    inv = rig.matrix_world.inverted()
    for i, name in enumerate(row):
        pb = rig.pose.bones[name]
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = Quaternion()
        bpy.context.view_layer.update()
        head, tail = pb.head, pb.tail
        want = (inv @ Vector(positions[i + 1])) - (inv @ Vector(positions[i]))
        have = tail - head
        if want.length < 1e-9 or have.length < 1e-9:
            continue
        turn = have.rotation_difference(want)  # in armature space
        m = pb.matrix.copy()
        r = (turn.to_matrix().to_4x4() @ Matrix.Translation(-head))
        m = Matrix.Translation(head) @ r @ m
        pb.matrix = m
        bpy.context.view_layer.update()


def bake(rig, chains, radii, fps=24):
    """Simulate every chain through every clip (each clip twice; the second pass is keyed) and key
    the chain bones into the clip. Returns {clip: frames}."""
    if not chains:
        return {}
    scene = bpy.context.scene
    actions = [a for a in bpy.data.actions if rig.animation_data and a.name and not a.name.startswith("_")]
    done = {}
    for action in actions:
        rig.animation_data.action = action
        start, end = int(action.frame_range[0]), int(action.frame_range[1])
        scene.frame_set(start)
        world = ph.World(substeps=4, iterations=4)
        sims = []
        for c in chains:
            for row in c["bones"]:
                for name in row:
                    rig.pose.bones[name].rotation_mode = "QUATERNION"
                    rig.pose.bones[name].rotation_quaternion = Quaternion()
                bpy.context.view_layer.update()
                s = c["settings"]
                chain = ph.Chain(row[0], _targets(rig, row), s["stiffness"], s["drag"], s["gravity"], s["radius"])
                world.add_chain(chain)
                sims.append((row, chain))
        for pass_no in range(2):
            for f in range(start, end + 1):
                scene.frame_set(f)
                world.colliders = _capsules(rig, radii)
                for row, chain in sims:
                    for name in row:
                        rig.pose.bones[name].rotation_quaternion = Quaternion()
                bpy.context.view_layer.update()
                for row, chain in sims:
                    chain.set_targets(_targets(rig, row))
                world.step(1.0 / fps)
                if pass_no == 1:
                    for row, chain in sims:
                        _aim(rig, row, chain.positions())
                        for name in row:
                            rig.pose.bones[name].keyframe_insert("rotation_quaternion", frame=f)
        done[action.name] = end - start + 1
    return done


def run(rig, pieces, body, fps=24):
    """Springs for a dressed character: chains added, colliders fitted, every clip baked."""
    chains = add_springs(rig, pieces)
    radii = fit_colliders(rig, body)
    for bone, r in radii.items():  # exported as extras: an engine (or the viewer) runs the springs live
        rig.data.bones[bone]["mg_collider"] = round(r, 4)
    baked = bake(rig, chains, radii, fps) if chains else {}
    return {"chains": [{"piece": c["piece"], "kind": c["kind"], "count": len(c["bones"]),
                        "bones": sum(len(r) for r in c["bones"])} for c in chains],
            "colliders": {k: round(v, 4) for k, v in radii.items()}, "baked": baked}
