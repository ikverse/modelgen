"""A character's hair, clothes, shoes and accessories. Runs inside Blender only.

Every piece is cut from geometry that already fits and already follows the skeleton: the MakeHuman
fitted shells (helper-tights for tops and trousers, helper-skirt for skirts, dresses and coat tails,
helper-hair for long hair) and the body itself (shoes, the scalp, eyebrows). A piece is chosen by
region, not by hand: each vertex's main bone says where on the body it is (torso, arm, leg, head),
and a cut line says how far a sleeve, a hem or a trouser leg goes (along the arm or leg, or by
height on the torso). The piece is then pushed out by its layer's thickness, given a real thickness
of its own, and keeps the skin weights it was cut from, so it moves with the body. Body faces fully
hidden under clothes are removed (fewer triangles, nothing pokes through).

A look is {"hair": {...}, "outfit": [{...}, ...], "accessories": [...], "brows": {...}}; the host
checks it (tools.character_look) before it reaches Blender.
"""

import math

import bmesh
import bpy
import numpy as np
from mathutils import Vector

from . import character_body as cb
from . import helpers

TORSO = {"pelvis", "spine_01", "spine_02", "spine_03", "clavicle_l", "clavicle_r", "neck_01"}
ARM_L, ARM_R = ("upperarm_l", "lowerarm_l", "hand_l"), ("upperarm_r", "lowerarm_r", "hand_r")
LEG_L, LEG_R = ("thigh_l", "calf_l", "foot_l", "ball_l"), ("thigh_r", "calf_r", "foot_r", "ball_r")
FINGERS = ("thumb", "index", "middle", "ring", "pinky")

# how far each layer sits off the skin (metres) and how thick the cloth is
LAYERS = {"under": (0.003, 0.0015), "shirt": (0.005, 0.002), "knit": (0.012, 0.003), "pants": (0.006, 0.002),
          "jacket": (0.018, 0.003), "coat": (0.024, 0.004), "shoes": (0.006, 0.004), "skirt": (0.012, 0.002)}

# garment kinds: what they cover and how they are made
GARMENTS = {
    "tshirt": {"kind": "top", "layer": "shirt", "sleeves": 0.38, "hem": "hip", "neck": "crew"},
    "tank_top": {"kind": "top", "layer": "shirt", "sleeves": 0.0, "hem": "hip", "neck": "scoop"},
    "shirt": {"kind": "top", "layer": "shirt", "sleeves": 0.97, "hem": "hip", "neck": "collar"},
    "blouse": {"kind": "top", "layer": "shirt", "sleeves": 0.9, "hem": "hip", "neck": "crew"},
    "sweater": {"kind": "top", "layer": "knit", "sleeves": 0.98, "hem": "hip", "neck": "crew"},
    "hoodie": {"kind": "top", "layer": "knit", "sleeves": 0.98, "hem": "hip", "neck": "crew"},
    "cardigan": {"kind": "top", "layer": "knit", "sleeves": 0.98, "hem": "hip", "neck": "v", "open": 0.035},
    "turtleneck": {"kind": "top", "layer": "knit", "sleeves": 0.98, "hem": "hip", "neck": "turtle"},
    "jacket": {"kind": "top", "layer": "jacket", "sleeves": 0.99, "hem": "hip", "neck": "collar", "open": 0.05},
    "leather_jacket": {"kind": "top", "layer": "jacket", "sleeves": 0.99, "hem": "waist", "neck": "collar",
                       "open": 0.06, "material": "leather"},
    # a coat's body ends at the waist; below it the tails (the skirt shell, on spring chains) hang free
    "coat": {"kind": "top", "layer": "coat", "sleeves": 0.99, "hem": "waist", "neck": "collar", "tails": "knee"},
    "pants": {"kind": "bottom", "layer": "pants", "length": 0.98},
    "jeans": {"kind": "bottom", "layer": "pants", "length": 0.98, "material": "denim"},
    "chinos": {"kind": "bottom", "layer": "pants", "length": 0.98},
    "shorts": {"kind": "bottom", "layer": "pants", "length": 0.42},
    "leggings": {"kind": "bottom", "layer": "under", "length": 0.99},
    "skirt": {"kind": "skirt", "layer": "skirt", "length": "knee"},
    "dress": {"kind": "dress", "layer": "shirt", "sleeves": 0.3, "neck": "scoop", "length": "knee"},
    "onesie": {"kind": "onesie", "layer": "knit", "sleeves": 0.98, "length": 0.99, "neck": "crew"},
    "sneakers": {"kind": "shoes", "layer": "shoes", "height": 0.06},
    "shoes": {"kind": "shoes", "layer": "shoes", "height": 0.04},
    "flats": {"kind": "shoes", "layer": "shoes", "height": 0.025},
    "boots": {"kind": "shoes", "layer": "shoes", "height": 0.2},
}
ROUGHNESS = {"cotton": 0.8, "denim": 0.85, "leather": 0.35, "knit": 0.95, "silk": 0.3, "wool": 0.9, "rubber": 0.6}


# --- where every vertex is ------------------------------------------------------------

class Map:
    """Landmarks and, for every vertex of the body data, its main bone and how far along the arm or
    the leg it sits (0 at the shoulder or hip, 1 at the wrist or ankle)."""

    def __init__(self, made):
        self.v = np.asarray(made["vertices"])
        self.bones = {b["name"]: (np.array(b["head"]), np.array(b["tail"])) for b in made["bones"]}
        names = [b["name"] for b in made["bones"]]
        main = np.full(len(self.v), -1)
        for i, pairs in enumerate(made["weights"]):
            if pairs:
                main[i] = max(pairs, key=lambda p: p[1])[0]
        self.main = np.array([names[i] if i >= 0 else "" for i in main])
        self.along = np.full(len(self.v), -1.0)
        for chain in (ARM_L, ARM_R, LEG_L, LEG_R):
            self._chain(chain)
        b = self.bones
        z = lambda n, end=0: float(b[n][end][2])
        self.ankle, self.knee, self.hip_joint = z("foot_l"), z("calf_l"), z("thigh_l")
        self.waist = z("spine_02")
        self.belt = (z("spine_01") + z("pelvis")) / 2
        self.neck = z("neck_01")
        self.head = z("head")
        self.top = float(self.v[:, 2].max())
        self.crotch = self.hip_joint - 0.25 * (self.hip_joint - self.knee)
        self.hem = {"crop": self.waist + 0.3 * (self.neck - self.waist), "waist": self.belt + 0.02,
                    "hip": self.hip_joint - 0.02, "thigh": self.crotch - 0.05}

    def _chain(self, chain):
        segs = [(n, *self.bones[n]) for n in chain if n in self.bones]
        lengths = [float(np.linalg.norm(t - h)) for _, h, t in segs]
        total = sum(lengths[:2]) or 1.0  # shoulder to wrist, hip to ankle
        start = 0.0
        for (name, h, t), L in zip(segs, lengths):
            idx = np.where(self.main == name)[0]
            if len(idx):
                d = t - h
                s = np.clip(((self.v[idx] - h) @ d) / max(d @ d, 1e-9), 0.0, 1.0)
                self.along[idx] = (start + s * L) / total
            start += L

    def group(self, idx):
        """Which body region each vertex is in: torso, arm, leg, hand, foot, head."""
        m = self.main[idx]
        out = np.full(len(idx), "other", dtype=object)
        out[np.isin(m, list(TORSO))] = "torso"
        out[np.isin(m, ["upperarm_l", "lowerarm_l", "upperarm_r", "lowerarm_r"])] = "arm"
        finger = np.array([str(n).split("_")[0] in FINGERS for n in m], dtype=bool)
        out[finger | np.isin(m, ["hand_l", "hand_r"])] = "hand"
        out[np.isin(m, ["thigh_l", "calf_l", "thigh_r", "calf_r"])] = "leg"
        out[np.isin(m, ["foot_l", "foot_r", "ball_l", "ball_r"])] = "foot"
        out[np.isin(m, ["head"])] = "head"
        return out


# --- choosing faces -----------------------------------------------------------------------

def _neckline(m, spec, front):
    """The height of the neckline for a vertex (front or back)."""
    neck = spec.get("neck", "crew")
    base = m.neck
    if neck == "turtle":
        return m.head - 0.01
    drop = {"crew": 0.012, "collar": 0.02, "scoop": 0.07, "v": 0.11}.get(neck, 0.012)
    return base - (drop if front else min(drop, 0.02))


def top_mask(m, idx, spec):
    """Vertices a top covers: the torso between its hem and neckline, the arms out to its sleeve
    length (0: none, 1: to the wrist), the hips above the hem."""
    g = m.group(idx)
    v = m.v[idx]
    hem = m.hem.get(spec.get("hem", "hip"), m.hem["hip"])
    front = v[:, 1] < m.bones["spine_03"][0][1]
    neckline = np.where(front, [_neckline(m, spec, True)] * len(idx), [_neckline(m, spec, False)] * len(idx))
    torso = (g == "torso") & (v[:, 2] >= hem) & (v[:, 2] <= neckline)
    arm = (g == "arm") & (m.along[idx] <= float(spec.get("sleeves", 0.4)))
    if float(spec.get("sleeves", 0.4)) <= 0.02:  # sleeveless: the shoulder's arm-weighted skin stays bare
        arm[:] = False
    hips = (g == "leg") & (v[:, 2] >= hem)
    keep = torso | arm | hips
    open_w = float(spec.get("open", 0) or 0)
    if open_w > 0:  # an open front: a strip down the middle is left out
        keep &= ~(front & (np.abs(v[:, 0]) < open_w) & (v[:, 2] < m.neck - 0.02))
    return keep


def bottom_mask(m, idx, spec):
    """Vertices trousers or shorts cover: the hips below the belt, each leg down to its length."""
    g = m.group(idx)
    v = m.v[idx]
    hips = (g == "torso") & (v[:, 2] <= m.belt)
    leg = (g == "leg") & (m.along[idx] <= float(spec.get("length", 0.98)))
    return hips | leg


def shoe_mask(m, idx, spec):
    g = m.group(idx)
    v = m.v[idx]
    return (g == "foot") | ((g == "leg") & (v[:, 2] <= m.ankle + float(spec.get("height", 0.05))))


def _faces_with(faces, verts_ok):
    return [i for i, f in enumerate(faces) if all(verts_ok.get(v, False) for v in f)]


def select(m, faces, mask_fn, spec):
    """The faces whose every vertex the mask keeps."""
    idx = np.array(sorted({v for f in faces for v in f}))
    keep = mask_fn(m, idx, spec)
    ok = dict(zip(idx.tolist(), keep.tolist()))
    return _faces_with(faces, ok)


# --- making a piece --------------------------------------------------------------------------

def _push(obj, distance, thickness, smooth=0, top_bias=None):
    """Push every vertex out along its normal by distance, then give the piece a thickness (a
    Solidify applied now; the skin weights carry over to the new vertices)."""
    me = obj.data
    me.update()
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    no = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("normal", no)
    no = no.reshape(-1, 3)
    if smooth:  # smoothed before it is pushed out: a smoothed outer layer would shrink into the one under it
        mod = obj.modifiers.new("smooth", "SMOOTH")
        mod.factor, mod.iterations = 0.5, smooth
        _apply_first(obj, mod)
        me.vertices.foreach_get("co", co.ravel())
        me.vertices.foreach_get("normal", no.ravel())
    d = np.full(len(co), distance)
    if top_bias is not None:
        d = d * top_bias(co)
    co = co + no * d[:, None]
    me.vertices.foreach_set("co", co.ravel())
    me.update()
    if thickness > 0:
        mod = obj.modifiers.new("cloth", "SOLIDIFY")
        mod.thickness = thickness
        mod.offset = 1.0
        mod.use_even_offset = False  # even thickness blows up at the sharp corners of toes and hairlines
        mod.use_quality_normals = True
        _apply_first(obj, mod)


def _apply_first(obj, mod):
    """Apply one modifier now, before the skin (the Armature modifier stays last in the stack)."""
    bpy.context.view_layer.objects.active = obj
    for o in bpy.context.scene.objects:
        o.select_set(o is obj)
    arm = [m for m in obj.modifiers if m.type == "ARMATURE"]
    for m in arm:
        m.show_viewport = False
    while obj.modifiers.find(mod.name) > 0:
        bpy.ops.object.modifier_move_up(modifier=mod.name)
    bpy.ops.object.modifier_apply(modifier=mod.name)
    for m in arm:
        m.show_viewport = True


def _material(name, colour, kind="cotton"):
    return cb._material(name, colour, roughness=ROUGHNESS.get(kind, 0.8), specular=0.3 if kind != "leather" else 0.5)


def smooth_edges(obj, iterations=6):
    """Make every cut edge a clean line: each open-edge vertex moves towards the middle of its two
    open-edge neighbours, a few times over (faces chosen whole leave a zig-zag otherwise)."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    rim = {}
    for e in bm.edges:
        if e.is_boundary:
            a, b = e.verts
            rim.setdefault(a.index, []).append(b.index)
            rim.setdefault(b.index, []).append(a.index)
    bm.verts.ensure_lookup_table()
    bm.normal_update()
    normals = {i: bm.verts[i].normal.copy() for i in rim}
    for _ in range(iterations):
        new = {}
        for i, nb in rim.items():
            if len(nb) == 2:
                mid = (bm.verts[nb[0]].co + bm.verts[nb[1]].co) / 2
                move = (mid - bm.verts[i].co) * 0.5
                nrm = normals[i]
                move -= nrm * move.dot(nrm)  # along the surface only: a round cuff must not tighten into the arm
                new[i] = bm.verts[i].co + move
        for i, co in new.items():
            bm.verts[i].co = co
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def smooth_weights(obj, repeat=6, factor=0.5):
    """Blend a garment's skin weights over its surface: cloth that spans two bones (a crotch, an
    armpit) then stretches evenly across the gap instead of tearing at the line between them. At
    most four bones per vertex are kept, renormalised."""
    me = obj.data
    n, groups = len(me.vertices), list(obj.vertex_groups)
    if not n or not groups:
        return
    w = np.zeros((n, len(groups)))
    for v in me.vertices:
        for g in v.groups:
            w[v.index, g.group] = g.weight
    ed = np.empty(len(me.edges) * 2, dtype=np.int64)
    me.edges.foreach_get("vertices", ed)
    ed = ed.reshape(-1, 2)
    deg = np.bincount(ed.ravel(), minlength=n).astype(float)
    deg[deg == 0] = 1.0
    # an edge (a hem, a cuff, a neckline) keeps the skin's own weights, so it never slides into the
    # skin under it; the blending fades in over the next few rings of vertices
    bm = bmesh.new()
    bm.from_mesh(me)
    ring = np.full(n, 99)
    front = [v.index for v in bm.verts if v.is_boundary]
    bm.free()
    ring[front] = 0
    for depth in range(1, 5):
        nxt = set()
        for a, b in ed:
            if ring[a] == depth - 1 and ring[b] > depth:
                nxt.add(b)
            if ring[b] == depth - 1 and ring[a] > depth:
                nxt.add(a)
        for i in nxt:
            ring[i] = depth
    step = factor * np.clip(ring / 4.0, 0.0, 1.0)[:, None]
    for _ in range(repeat):
        acc = np.zeros_like(w)
        np.add.at(acc, ed[:, 0], w[ed[:, 1]])
        np.add.at(acc, ed[:, 1], w[ed[:, 0]])
        w = (1 - step) * w + step * acc / deg[:, None]
    keep = np.argsort(-w, axis=1)[:, :cb.MAX_INFLUENCES]
    for vi in range(n):
        cols = keep[vi]
        vals = w[vi, cols]
        total = vals.sum() or 1.0
        for gi in range(len(groups)):
            groups[gi].remove([vi])
        for gi, val in zip(cols, vals):
            if val > 1e-4:
                groups[gi].add([vi], float(val / total), "REPLACE")


def _crotch_to_pelvis(obj, m, width=0.08):
    """Trousers hang from the hips: cloth near the middle seam between the legs follows the pelvis
    as much as either thigh, so lifting one leg stretches it evenly instead of tearing the seam."""
    pelvis = obj.vertex_groups.get("pelvis")
    if pelvis is None:
        pelvis = obj.vertex_groups.new(name="pelvis")
    lo, hi = m.crotch - 0.1 * (m.hip_joint - m.knee), m.hip_joint + 0.02
    for v in obj.data.vertices:
        x, z = v.co.x, v.co.z
        if lo <= z <= hi and abs(x) < width:
            share = 0.75 * (1 - abs(x) / width)
            for g in v.groups:
                g.weight *= 1 - share
            pelvis.add([v.index], share, "ADD")


def _weld(obj, distance=2e-5):
    """Join vertices that sit on one spot (a shell's seam), so the piece cannot open along it when
    the two sides move differently. The texture corners stay; the skin weights of the kept vertex
    stay."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=distance)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def _piece(name, made, rig, faces, face_uvs, layer, colour, kind, smooth=0, bias=None, before=None):
    obj, _ = cb.skinned_part(name, made, faces, face_uvs, rig)
    _weld(obj)
    smooth_edges(obj)
    if before:
        before(obj)
    if layer != "shoes":
        smooth_weights(obj, repeat=10 if layer in ("pants", "under") else 6)
    gap, thick = LAYERS[layer]
    _push(obj, gap, thick, smooth, bias)
    obj.data.materials.append(_material(name, colour, kind))
    for p in obj.data.polygons:
        p.use_smooth = True
    return obj


def _subset(faces, uvs, chosen):
    return [faces[i] for i in chosen], [uvs[i] for i in chosen]


def _stretch_skirt(obj, m, length):
    """Lengthen or shorten a skirt to its hem: below the hips the shell is stretched down (and
    flared a little) so its lowest ring sits at the hem."""
    hem = {"mini": m.crotch - 0.12 * (m.crotch - m.knee), "knee": m.knee - 0.02,
           "midi": m.knee - 0.45 * (m.knee - m.ankle), "long": m.ankle + 0.05}.get(length, m.knee)
    me = obj.data
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    top = m.hip_joint
    low = co[:, 2].min()
    if low >= top - 1e-3:
        return
    t = np.clip((top - co[:, 2]) / (top - low), 0.0, 1.0)
    z = top - t * (top - hem)
    flare = 1.0 + 0.18 * t * max(0.0, (top - hem) / max(top - low, 1e-6) - 1.0)
    co[:, 0] *= np.where(co[:, 2] < top, flare, 1.0)
    co[:, 1] = np.where(co[:, 2] < top, (co[:, 1] - m.bones["pelvis"][0][1]) * flare + m.bones["pelvis"][0][1],
                        co[:, 1])
    co[:, 2] = np.where(co[:, 2] < top, z, co[:, 2])
    me.vertices.foreach_set("co", co.ravel())
    me.update()
    # the stretched part follows the thighs less: weight it to the pelvis as it hangs down
    pelvis = obj.vertex_groups.get("pelvis")
    if pelvis is not None:
        for i, ti in enumerate(t):
            if ti > 0.3:
                pelvis.add([i], float(min(1.0, ti)), "ADD")
        _normalise(obj)


def _normalise(obj):
    for v in obj.data.vertices:
        total = sum(g.weight for g in v.groups)
        if total > 0:
            for g in v.groups:
                g.weight /= total


def garment(spec, made, m, rig, colour):
    """One garment as one or two skinned pieces; returns the objects."""
    kind = spec["kind"]
    name = spec.get("name") or spec["type"]
    tights = made["helpers"].get("helper-tights")
    skirt = made["helpers"].get("helper-skirt")
    material = spec.get("material") or ("knit" if spec.get("layer") == "knit" else "cotton")
    out = []
    if kind in ("top", "dress", "onesie"):
        top_spec = {**spec, "hem": "waist"} if kind == "dress" else spec
        mask = top_mask if kind != "onesie" else (lambda mm, i, s: top_mask(mm, i, {**s, "hem": "hip"})
                                                 | bottom_mask(mm, i, s))
        chosen = select(m, tights["faces"], mask, top_spec)
        f, u = _subset(tights["faces"], tights["face_uvs"], chosen)
        out.append(_piece(name, made, rig, f, u, spec["layer"], colour, material, smooth=1))
    if kind == "bottom":
        chosen = select(m, tights["faces"], bottom_mask, spec)
        f, u = _subset(tights["faces"], tights["face_uvs"], chosen)
        out.append(_piece(name, made, rig, f, u, spec["layer"], colour, material, smooth=1,
                          before=lambda o: _crotch_to_pelvis(o, m)))
    if kind in ("skirt", "dress") or spec.get("tails"):
        length = spec.get("tails") or spec.get("length", "knee")
        obj = _piece(name + ("_tails" if spec.get("tails") else "_skirt" if kind == "dress" else ""), made, rig,
                     skirt["faces"], skirt["face_uvs"], "coat" if spec.get("tails") else "skirt", colour, material)
        _stretch_skirt(obj, m, length)
        out.append(obj)
    if kind == "shoes":
        body = made["parts"]["body"]
        chosen = select(m, body["faces"], shoe_mask, spec)
        f, u = _subset(body["faces"], body["face_uvs"], chosen)
        obj = _piece(name, made, rig, f, u, "shoes", colour, spec.get("material") or "leather")
        _flat_soles(obj)
        out.append(obj)
    return out


def _flat_soles(obj, sole=0.012):
    """A shoe's bottom is flat on the floor: everything within the sole height goes down to it."""
    me = obj.data
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    low = co[:, 2] < sole
    co[low, 2] = np.maximum(0.0, co[low, 2] - 0.004)
    me.vertices.foreach_set("co", co.ravel())
    me.update()


# --- hair, brows, glasses ------------------------------------------------------------------

HAIR_STYLES = ("bald", "buzz", "short", "crew", "undercut", "medium", "bob", "long", "ponytail", "bun", "spiky",
               "curly", "afro")


def _eye_centres(made):
    eyes = made["parts"].get("eyes")
    if not eyes:
        return None
    vs = np.asarray(made["vertices"])
    idx = np.array(sorted({v for f in eyes["faces"] for v in f}))
    pts = vs[idx]
    left, right = pts[pts[:, 0] > 0], pts[pts[:, 0] <= 0]
    return left.mean(axis=0), right.mean(axis=0)


LAST_SCALP = {}  # how many head vertices each test of the last scalp_mask kept (for checking it)


def scalp_mask(m, made, style):
    """Body vertices the hair covers: the head above a hairline, the face and the ears left bare,
    the nape covered down to the neck."""
    eyes = _eye_centres(made)
    eye_z = float((eyes[0][2] + eyes[1][2]) / 2) if eyes else m.head + 0.6 * (m.top - m.head)
    eye_y = float((eyes[0][1] + eyes[1][1]) / 2) if eyes else m.bones["head"][0][1] - 0.08
    skin = np.zeros(len(m.v), dtype=bool)  # the body's own vertices: not the hair, tights or eye shells
    skin[[i for f in made["parts"]["body"]["faces"] for i in f]] = True
    head_idx = np.where((m.main == "head") & skin)[0]
    v = m.v[head_idx]
    centre = np.array([0.0, float(v[:, 1].min() + v[:, 1].max()) / 2, eye_z])  # the middle of the skull
    hairline = eye_z + (0.55 if style not in ("undercut", "buzz") else 0.62) * (m.top - eye_z)
    half_w = float(np.abs(v[:, 0]).max())
    d = v - centre
    d /= np.maximum(np.linalg.norm(d, axis=1), 1e-9)[:, None]
    front = d[:, 1] < -0.64  # within 50 degrees of straight ahead: the face, not the temples
    back = d[:, 1] > 0.2
    above = v[:, 2] >= hairline
    # the sides come down to the top of the ear, a little lower behind it; never over the ear
    side_line = eye_z + np.where(v[:, 1] > centre[1], 0.0, 0.1) * (m.top - eye_z)
    sides = (~front) & (v[:, 2] >= side_line)
    ears = np.zeros(len(v), dtype=bool)
    nape = back & (v[:, 2] >= m.neck + 0.45 * (eye_z - m.neck))
    ear_idx = np.asarray(made.get("ears", []), dtype=np.int64)
    if len(ear_idx):  # the ears themselves: the vertices the ear targets move
        ears = np.isin(head_idx, ear_idx)
    keep = above | ((sides | nape) & ~ears)
    keep &= ~ears
    LAST_SCALP.update(above=int(above.sum()), sides=int(sides.sum()), nape=int(nape.sum()), ears=int(ears.sum()),
                      front=int(front.sum()), kept=int(keep.sum()))
    if style in ("undercut",):
        keep = above | (back & (v[:, 2] >= eye_z + 0.4 * (m.top - eye_z)))
    full = np.zeros(len(m.v), dtype=bool)
    full[head_idx[keep]] = True
    return full, eye_z, centre


def hair(spec, made, m, rig):
    """The hair as skinned pieces: a cap over the scalp (its volume by style) and, for longer
    styles, the fitted hair shell cut to length, a ponytail, a bun or spikes."""
    style = spec.get("style", "short")
    if style == "bald":
        return []
    colour = spec.get("colour", "#3b2a1e")
    body = made["parts"]["body"]
    full, eye_z, centre = scalp_mask(m, made, style)
    ok = {i: bool(full[i]) for f in body["faces"] for i in f}
    chosen = _faces_with(body["faces"], ok)
    f, u = _subset(body["faces"], body["face_uvs"], chosen)
    volume = {"buzz": 0.004, "crew": 0.01, "undercut": 0.016, "short": 0.018, "spiky": 0.014, "medium": 0.022,
              "bob": 0.024, "long": 0.016, "ponytail": 0.01, "bun": 0.01, "curly": 0.032, "afro": 0.06}[style]
    volume *= float(spec.get("volume", 1.0))
    top = m.top

    cap = cb.skinned_part("hair", made, f, u, rig)[0]
    smooth_edges(cap, 8)
    edge = _rings_from_edge(cap, 4)

    def bias(co):  # more hair on the crown than at the hairline, thinning out to nothing at its edge
        crown = 0.35 + 0.65 * np.clip((co[:, 2] - eye_z) / max(top - eye_z, 1e-6), 0.0, 1.0)
        return crown * (0.15 + 0.85 * np.clip(edge / 4.0, 0.0, 1.0))

    _push(cap, volume, 0.002, smooth=2, top_bias=bias)
    mat = _hair_material(colour)
    cap.data.materials.append(mat)
    out = [cap]
    shell = made["helpers"].get("helper-hair")
    if style in ("medium", "bob", "long") and shell:
        cut = {"bob": eye_z - 0.6 * (eye_z - m.neck), "medium": m.neck - 0.02,
               "long": m.bones["spine_03"][0][2] - float(spec.get("length", 0.0) or 0.0)}[style]
        idx = np.array(sorted({v for fc in shell["faces"] for v in fc}))
        okh = dict(zip(idx.tolist(), (m.v[idx][:, 2] >= cut).tolist()))
        chosen = _faces_with(shell["faces"], okh)
        if chosen:
            f2, u2 = _subset(shell["faces"], shell["face_uvs"], chosen)
            strands = cb.skinned_part("hair_strands", made, f2, u2, rig)[0]
            _push(strands, 0.004, 0.006, smooth=1)
            strands.data.materials.append(mat)
            out.append(strands)
    if style == "ponytail":
        out.append(_tube_hair("ponytail", rig, centre, eye_z, top, mat, kind="ponytail"))
    if style == "bun":
        out.append(_tube_hair("bun", rig, centre, eye_z, top, mat, kind="bun"))
    if style == "spiky":
        out.append(_spikes(cap, rig, eye_z, top, mat, scale=float(spec.get("volume", 1.0))))
    return out


def _rings_from_edge(obj, most):
    """For every vertex, how many edges away the mesh's open edge is (capped at `most`)."""
    me = obj.data
    n = len(me.vertices)
    bm = bmesh.new()
    bm.from_mesh(me)
    ring = np.full(n, float(most))
    frontier = {v.index for v in bm.verts if v.is_boundary}
    seen = set(frontier)
    depth = 0
    while frontier and depth < most:
        for i in frontier:
            ring[i] = depth
        nxt = set()
        bm.verts.ensure_lookup_table()
        for i in frontier:
            for e in bm.verts[i].link_edges:
                j = e.other_vert(bm.verts[i]).index
                if j not in seen:
                    seen.add(j)
                    nxt.add(j)
        frontier, depth = nxt, depth + 1
    bm.free()
    return ring


def _hair_material(colour):
    """Hair: the colour with fine lengthwise streaks of lighter and darker strands and a soft sheen
    (Blender's renders); a game file gets the plain colour."""
    mat = cb._material("hair", colour, roughness=0.42, specular=0.55)
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    coord = nt.nodes.new("ShaderNodeTexCoord")
    wave = nt.nodes.new("ShaderNodeTexWave")
    wave.wave_type = "BANDS"
    wave.bands_direction = "X"
    wave.inputs["Scale"].default_value = 90.0
    wave.inputs["Distortion"].default_value = 6.0
    wave.inputs["Detail"].default_value = 3.0
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    base = bsdf.inputs["Base Color"].default_value
    ramp.color_ramp.elements[0].color = (base[0] * 0.7, base[1] * 0.7, base[2] * 0.7, 1)
    ramp.color_ramp.elements[1].color = (min(1, base[0] * 1.25), min(1, base[1] * 1.25), min(1, base[2] * 1.25), 1)
    nt.links.new(coord.outputs["Object"], wave.inputs["Vector"])
    nt.links.new(wave.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    # the plain values a game file gets (rig._plain_materials reads all three)
    mat["mg_base_color"] = list(base)
    mat["mg_metallic"] = 0.0
    mat["mg_roughness"] = 0.42
    return mat


def _rigid(obj, rig, bone="head"):
    """Skin a separate piece wholly to one bone."""
    obj.parent = rig
    g = obj.vertex_groups.new(name=bone)
    g.add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")
    mod = obj.modifiers.new("rig", "ARMATURE")
    mod.object = rig
    obj[helpers.PART_KEY] = obj.name
    obj[helpers.NO_BEVEL_KEY] = True
    return obj


def _tube_hair(name, rig, centre, eye_z, top, mat, kind):
    bm = bmesh.new()
    h = top - eye_z
    back_y = centre[1] + 0.85 * h
    if kind == "bun":
        bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=10, radius=0.42 * h)
        bmesh.ops.translate(bm, verts=bm.verts[:], vec=Vector((0, back_y - 0.1 * h, top - 0.05 * h)))
    else:
        rings, seg = 9, 10
        root = Vector((0, back_y - 0.15 * h, eye_z + 0.55 * h))
        prev = None
        for i in range(rings):
            t = i / (rings - 1)
            r = 0.22 * h * (1 - 0.65 * t) + 0.02 * h
            c = root + Vector((0, 0.35 * h * math.sin(t * 1.2), -1.9 * h * t))
            ring = [bm.verts.new(c + Vector((r * math.cos(a), r * math.sin(a), 0)))
                    for a in np.linspace(0, 2 * math.pi, seg, endpoint=False)]
            if prev:
                for j in range(seg):
                    bm.faces.new((prev[j], prev[(j + 1) % seg], ring[(j + 1) % seg], ring[j]))
            prev = ring
        bm.faces.new(list(reversed(prev)))
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(mat)
    return _rigid(obj, rig)


def _spikes(cap, rig, eye_z, top, mat, count=26, scale=1.0):
    """Anime-style spikes: cones from the upper scalp, pointing out and swept back."""
    me = cap.data
    co = np.array([v.co[:] for v in me.vertices])
    no = np.array([v.normal[:] for v in me.vertices])
    upper = np.where(co[:, 2] > eye_z + 0.35 * (top - eye_z))[0]
    rng = np.random.default_rng(7)
    picks = rng.choice(upper, size=min(count, len(upper)), replace=False)
    bm = bmesh.new()
    h = top - eye_z
    for i in picks:
        base, n = Vector(co[i]), Vector(no[i]).normalized()
        direction = (n + Vector((0, 0.6, 0.25))).normalized()
        length = (0.5 + 0.4 * rng.random()) * h * scale
        r = 0.16 * h * min(scale, 1.3)
        tip = bm.verts.new(base + direction * length)
        side = direction.orthogonal().normalized()
        other = direction.cross(side)
        ring = [bm.verts.new(base - direction * 0.01 + (side * math.cos(a) + other * math.sin(a)) * r)
                for a in np.linspace(0, 2 * math.pi, 6, endpoint=False)]
        for j in range(6):
            bm.faces.new((ring[j], ring[(j + 1) % 6], tip))
    me2 = bpy.data.meshes.new("hair_spikes")
    bm.to_mesh(me2)
    bm.free()
    obj = bpy.data.objects.new("hair_spikes", me2)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(mat)
    return _rigid(obj, rig)


def _eye_radius(made):
    eyes = made["parts"].get("eyes")
    vs = np.asarray(made["vertices"])
    idx = np.array(sorted({v for f in eyes["faces"] for v in f}))
    pts = vs[idx]
    left = pts[pts[:, 0] > 0]
    return float((left[:, 0].max() - left[:, 0].min()) / 2)


def brows(spec, made, m, rig, body_obj):
    """Eyebrows: a smooth arched strip above each eye, laid on the skin and sized from the eye
    (so a baby's are a baby's), thick at the inner end and tapering outwards."""
    from mathutils.bvhtree import BVHTree
    eyes = _eye_centres(made)
    if not eyes or body_obj is None:
        return []
    re = _eye_radius(made)
    thick = 0.5 + float(spec.get("thickness", 0.5))
    arch = float(spec.get("arch", 0.5))
    dg = bpy.context.evaluated_depsgraph_get()
    tree = BVHTree.FromObject(body_obj, dg)
    to_world = body_obj.matrix_world
    bm = bmesh.new()
    n = 12
    for e in eyes:
        side = 1.0 if e[0] > 0 else -1.0
        upper, lower = [], []
        for i in range(n):
            t = i / (n - 1)  # 0 inner end, 1 outer end
            x = e[0] + side * (-1.15 + 2.55 * t) * re
            z = e[2] + (1.35 + 0.45 * arch * math.sin(math.pi * min(1.0, t * 1.25)) - 0.35 * t * t) * re
            origin = Vector((x, e[1] - 0.2, z))
            hit, normal, _, _ = tree.ray_cast(origin, Vector((0, 1, 0)), 0.4)
            if hit is None:
                continue
            h = (0.42 * (1 - 0.6 * t) + 0.12) * (0.25 + thick - 0.5) * re  # thickness 0..1 -> fine to bold
            p = hit + normal * 0.0012
            up = Vector((0, 0, 1)) - normal * normal.z
            up.normalize()
            upper.append(bm.verts.new(p + up * h / 2))
            lower.append(bm.verts.new(p - up * h / 2))
        for i in range(len(upper) - 1):
            face = (lower[i], lower[i + 1], upper[i + 1], upper[i])
            bm.faces.new(face if side > 0 else tuple(reversed(face)))
    me = bpy.data.meshes.new("brows")
    bm.to_mesh(me)
    bm.free()
    if not me.polygons:
        return []
    obj = bpy.data.objects.new("brows", me)
    bpy.context.scene.collection.objects.link(obj)  # in the body's own space, like every other piece
    obj.data.materials.append(cb._material("brows", spec.get("colour", "#2a1e16"), roughness=0.75))
    for p in me.polygons:
        p.use_smooth = True
    return [_rigid(obj, rig)]


def glasses(spec, made, rig):
    """Glasses: a round or square frame in front of each eye, a bridge and arms back to the ears,
    all on the head bone."""
    eyes = _eye_centres(made)
    if not eyes:
        return []
    bm = bmesh.new()
    r = 0.021 if spec.get("shape", "round") == "round" else 0.022
    sq = spec.get("shape") == "square"
    wire = 0.0013 if spec.get("frame", "thin") == "thin" else 0.0028
    fronts = []
    for e in eyes:
        c = Vector((float(e[0]), float(e[1]) - 0.02, float(e[2])))
        fronts.append(c)
        pts = []
        for a in np.linspace(0, 2 * math.pi, 24, endpoint=False):
            x, z = math.cos(a), math.sin(a)
            if sq:
                k = max(abs(x), abs(z) * 1.25)
                x, z = x / k, z / k * 0.8
            pts.append(c + Vector((x * r, 0, z * r)))
        _ring_tube(bm, pts, wire, closed=True)
    a, b = sorted(fronts, key=lambda p: p.x)
    _ring_tube(bm, [a + Vector((r, 0, 0.004)), b + Vector((-r, 0, 0.004))], wire, closed=False)
    for c in fronts:
        side = 1 if c.x > 0 else -1
        start = c + Vector((side * r, 0, 0.004))
        _ring_tube(bm, [start, start + Vector((side * 0.012, 0.03, 0)), start + Vector((side * 0.014, 0.095, -0.012))],
                   wire, closed=False)
    me = bpy.data.meshes.new("glasses")
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new("glasses", me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(cb._material("glasses", spec.get("colour", "#1b1b1b"), roughness=0.3))
    for p in me.polygons:
        p.use_smooth = True
    return [_rigid(obj, rig)]


def _ring_tube(bm, pts, radius, closed, seg=6):
    rings = []
    n = len(pts)
    for i, p in enumerate(pts):
        nxt = pts[(i + 1) % n] if (closed or i < n - 1) else p + (p - pts[i - 1])
        prv = pts[i - 1] if (closed or i > 0) else p - (pts[1] - p)
        d = (nxt - prv).normalized()
        s = d.orthogonal().normalized()
        o = d.cross(s)
        rings.append([bm.verts.new(p + (s * math.cos(a) + o * math.sin(a)) * radius)
                      for a in np.linspace(0, 2 * math.pi, seg, endpoint=False)])
    count = n if closed else n - 1
    for i in range(count):
        r1, r2 = rings[i], rings[(i + 1) % n]
        for j in range(seg):
            bm.faces.new((r1[j], r1[(j + 1) % seg], r2[(j + 1) % seg], r2[j]))


# --- hiding the body under clothes ---------------------------------------------------------------

def hide_covered(body_obj, made, m, garments):
    """Remove the body faces every garment hides: a face goes when all its vertices are covered and
    so are all their neighbours (a ring of skin stays under every edge, so nothing opens up)."""
    body = made["parts"]["body"]
    idx = np.array(sorted({v for f in body["faces"] for v in f}))
    covered = np.zeros(len(idx), dtype=bool)
    for spec in garments:
        kind = spec["kind"]
        if kind in ("top", "dress", "onesie"):
            s = {**spec, "hem": "waist"} if kind == "dress" else spec
            c = top_mask(m, idx, s)
            if kind == "onesie":
                c |= bottom_mask(m, idx, spec)
            covered |= c
        elif kind == "bottom":
            covered |= bottom_mask(m, idx, spec)
        elif kind == "shoes":
            covered |= shoe_mask(m, idx, {**spec, "height": max(0.0, float(spec.get("height", 0.04)) - 0.015)})
    ok = dict(zip(idx.tolist(), covered.tolist()))
    neighbours = {}
    for f in body["faces"]:
        for v in f:
            neighbours.setdefault(v, set()).update(f)
    inner = {v: ok[v] and all(ok.get(w, False) for w in neighbours[v]) for v in ok}
    # the body object's own vertex order: the sorted used vertices of its faces
    gone = [i for i, f in enumerate(body["faces"]) if all(inner.get(v, False) for v in f)]
    if not gone:
        return 0
    me = body_obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[bm.faces[i] for i in gone if i < len(bm.faces)], context="FACES_ONLY")
    loose = [v for v in bm.verts if not v.link_faces]
    bmesh.ops.delete(bm, geom=loose, context="VERTS")
    bm.to_mesh(me)
    bm.free()
    me.update()
    return len(gone)


# --- drawn styles -------------------------------------------------------------------------------------

OUTLINE_KEY = "mg_outline"


def _outline_material(width):
    """Ink for an outline shell: black where the shell's inside faces the camera (its silhouette),
    see-through elsewhere. Cycles shows back faces, so the see-through part is the shader's job."""
    mat = bpy.data.materials.get("mg_outline_ink")
    if mat is not None:
        return mat
    mat = bpy.data.materials.new("mg_outline_ink")
    mat.use_nodes = True
    mat.use_backface_culling = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    ink = nt.nodes.new("ShaderNodeEmission")
    ink.inputs["Color"].default_value = (0.02, 0.018, 0.02, 1)
    clear = nt.nodes.new("ShaderNodeBsdfTransparent")
    mix = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(geo.outputs["Backfacing"], mix.inputs["Fac"])
    nt.links.new(ink.outputs[0], mix.inputs[1])
    nt.links.new(clear.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    mat.diffuse_color = (0.02, 0.018, 0.02, 1)
    return mat


TOON_LIGHT = (0.35, -0.45, 0.82)  # the cel light's direction (Blender axes), as the viewer's default
# (where a band starts, its share of the colour): flat emission reads darker than lit cloth, so the
# lit band is brighter than the colour itself
TOON_BANDS = ((0.0, 0.8), (0.42, 1.15), (0.62, 1.45))


def _toon(mat):
    """Turn a material into cel bands: its colour in three flat tones by how much each surface faces
    the cel light, as an Emission (so Cycles draws it flat). rig._flatten_emission_materials keeps the
    bands as mg_toon_colors / mg_toon_stops / mg_toon_light in the GLB, which the viewer draws again."""
    bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.use_nodes else None
    base = list(bsdf.inputs["Base Color"].default_value) if bsdf else list(mat.diffuse_color)
    if "mg_base_color" in mat.keys():  # a stored plain colour (the hair's) would make the exporter skip the bands
        base = list(mat["mg_base_color"])
    for key in ("mg_base_color", "mg_metallic", "mg_roughness"):
        if key in mat.keys():
            del mat[key]
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    dot = nt.nodes.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    dot.inputs[1].default_value = TOON_LIGHT
    span = nt.nodes.new("ShaderNodeMapRange")
    span.inputs["From Min"].default_value = -1.0
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.interpolation = "CONSTANT"
    els = ramp.color_ramp.elements
    while len(els) < len(TOON_BANDS):
        els.new(0.5)
    for el, (pos, share) in zip(els, TOON_BANDS):
        el.position = pos
        el.color = (base[0] * share, base[1] * share, base[2] * share, 1.0)
    glow = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(geo.outputs["Normal"], dot.inputs[0])
    nt.links.new(dot.outputs["Value"], span.inputs["Value"])
    nt.links.new(span.outputs["Result"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], glow.inputs["Color"])
    nt.links.new(glow.outputs["Emission"], out.inputs["Surface"])
    mat.diffuse_color = base[:3] + [1.0]


def toonify(objs, rig, outline=0.004):
    """A drawn look: every material in flat cel bands (_toon), marked with its outline width for the
    viewer; and for Blender's renders an ink outline: a slightly bigger copy of each piece, inside
    out, skinned like it, kept out of the exported files (the viewer and game engines draw outlines
    themselves, without doubling the triangles)."""
    done = set()
    for obj in objs:
        for mat in obj.data.materials:
            if mat is None or mat.name in done:
                continue
            done.add(mat.name)
            _toon(mat)
            mat["mg_outline"] = float(outline)
    if outline <= 0:
        return []
    ink = _outline_material(outline)
    shells = []
    for obj in objs:
        if obj.name.startswith(("eyes", "eyelashes", "brows", "glasses")):
            continue
        shell = obj.copy()
        shell.data = obj.data.copy()
        bpy.context.scene.collection.objects.link(shell)
        for key in (helpers.PART_KEY,):
            if key in shell:
                del shell[key]
        shell[OUTLINE_KEY] = obj.name
        shell.name = obj.name + "_outline"
        # seen by the camera only: as a shadow or a reflection it would darken the character
        for attr in ("visible_shadow", "visible_diffuse", "visible_glossy", "visible_transmission",
                     "visible_volume_scatter"):
            if hasattr(shell, attr):
                setattr(shell, attr, False)
        shell.data.materials.clear()
        shell.data.materials.append(ink)
        me = shell.data
        co = np.empty(len(me.vertices) * 3)
        me.vertices.foreach_get("co", co)
        no = np.empty(len(me.vertices) * 3)
        me.vertices.foreach_get("normal", no)
        me.vertices.foreach_set("co", co + no * outline)
        me.flip_normals()
        me.update()
        shells.append(shell)
    return shells


# --- the whole look ----------------------------------------------------------------------------------

def dress_up(look, made, rig, parts):
    """Put the look on the character built by character_body.build: returns {name: object} of
    every piece made, and how many body faces were hidden."""
    m = Map(made)
    made_parts = {}
    outfit = [{**GARMENTS[g["type"]], **g} for g in look.get("outfit") or [] if g.get("type") in GARMENTS]
    for g in outfit:
        for obj in garment(g, made, m, rig, g.get("colour", "#777777")):
            made_parts[obj.name] = obj
    hidden = hide_covered(parts["body"], made, m, outfit) if outfit else 0
    if look.get("hair"):
        for obj in hair(look["hair"], made, m, rig):
            made_parts[obj.name] = obj
    if look.get("brows", {"colour": (look.get("hair") or {}).get("colour", "#2a1e16")}) is not None:
        brow_spec = look.get("brows") or {"colour": (look.get("hair") or {}).get("colour", "#2a1e16")}
        for obj in brows(brow_spec, made, m, rig, parts.get("body")):
            made_parts[obj.name] = obj
    for acc in look.get("accessories") or []:
        if acc.get("type") == "glasses":
            for obj in glasses(acc, made, rig):
                made_parts[obj.name] = obj
    bpy.context.view_layer.update()
    return {"pieces": made_parts, "hidden_faces": hidden}
