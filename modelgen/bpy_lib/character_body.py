"""A character's body and skeleton in the scene. Runs inside Blender only.

The body is the MakeHuman base mesh (CC0, as packaged by the Anny body model, assets_cc0/anny_body)
shaped by its macro targets the way Anny and MakeHuman blend them: age in years (newborn to 90),
sex, muscle, weight, height, proportions and ancestry, each target weighted by the product of its
sliders' interpolation coefficients. The skeleton is the 53-bone game-engine rig of that data (the
usual game skeleton: pelvis, spine_01..03, thigh_l, upperarm_l, fingers...), its joints placed from
the shaped mesh, its skin weights as MPFB2 painted them. numpy does the maths (Blender ships it).
It becomes what a game engine expects:

    <armature "rig">          the skeleton, tagged mg_rig, at the origin, feet on the floor
      body (mesh)             parented to it with an Armature modifier, one vertex group per bone,
                              tagged mg_part = "body"
      eyes (mesh)             the eyeballs, skinned to the head the same way, mg_part = "eyes"

Z is up, the character faces -Y (the studio's front), lengths in metres. Only the four strongest
weights per vertex are kept (what game engines blend), renormalised to sum to one.
"""

import gzip
import hashlib
import json
import math
import os

import bpy
import numpy as np
from mathutils import Euler, Vector

from . import helpers

RIG_KEY = "mg_rig"
RIG_NAME = "rig"
MAX_INFLUENCES = 4
RIG_FILE = "rigs/standard/rig.game_engine.json"
WEIGHTS_FILE = "rigs/standard/weights.game_engine.json"

# --- the body model ---------------------------------------------------------------------

# the macro sliders and their anchors: a target named by an anchor gets that anchor's coefficient
AGES = (("newborn", 0.0), ("baby", 1.0), ("child", 11.0), ("young", 25.0), ("old", 90.0))  # in years
LEVELS = {"muscle": ("minmuscle", "averagemuscle", "maxmuscle"),
          "weight": ("minweight", "averageweight", "maxweight"),
          "height": ("minheight", "maxheight"),
          "proportions": ("idealproportions", "uncommonproportions"),
          "cupsize": ("mincup", "averagecup", "maxcup"),
          "firmness": ("minfirmness", "averagefirmness", "maxfirmness")}
RACES = ("african", "asian", "caucasian")
# a newborn is the baby targets scaled down (Anny's empirical numbers, in its Z-up axes)
NEWBORN_SCALE = np.array([0.922, 0.922, 0.75])
NEWBORN_BLOCKS = 3.0  # universal, race and height targets each carry a full share of the newborn's scaling
# faces Anny removes from the base mesh (nudity edits), and the quads that close the gap
_DISCARD = set(range(1778, 1794)) | set(range(8450, 8466))
_CAPS = [[8437, 8438, 8439, 8440], [8436, 8437, 8440, 8441], [8435, 8436, 8441, 8442], [8434, 8435, 8442, 8443],
         [8449, 8434, 8443, 8444], [8448, 8449, 8444, 8445], [8447, 8448, 8445, 8446],
         [1762, 1771, 1770, 1763], [1763, 1770, 1769, 1764], [1764, 1769, 1768, 1765], [1765, 1768, 1767, 1766],
         [1762, 1777, 1772, 1771], [1777, 1776, 1773, 1772], [1776, 1775, 1774, 1773]]
EYE_GROUPS = ("helper-l-eye", "helper-r-eye")
HELPER_GROUPS = ("helper-tights", "helper-skirt", "helper-hair")


def _to_z_up(a):
    """MakeHuman's decimetres, Y up and facing +Z, to metres, Z up and facing -Y."""
    a = np.asarray(a, dtype=np.float64) * 0.1
    return np.stack([a[..., 0], -a[..., 2], a[..., 1]], axis=-1)


def interpolate(value, anchors):
    """Linear interpolation coefficients of value between increasing anchors (clamped)."""
    value = min(max(float(value), anchors[0]), anchors[-1])
    out = [0.0] * len(anchors)
    for i in range(1, len(anchors)):
        if value <= anchors[i] or i == len(anchors) - 1:
            lo, hi = anchors[i - 1], anchors[i]
            a = (value - lo) / (hi - lo) if hi > lo else 0.0
            out[i - 1], out[i] = 1.0 - a, a
            return out
    return out


def coefficients(body):
    """{anchor name: coefficient} for a body's sliders: age (years), female (0 male .. 1 female),
    muscle, weight, height, proportions, cupsize, firmness (0..1, 0.5 average) and ancestry
    (african, asian, caucasian: shares, normalised; equal when left out)."""
    c = dict(zip([a for a, _ in AGES], interpolate(body.get("age", 25.0), [y for _, y in AGES])))
    female = min(max(float(body.get("female", 0.5)), 0.0), 1.0)
    c["female"], c["male"] = female, 1.0 - female
    for name, keys in LEVELS.items():
        anchors = [i / (len(keys) - 1) for i in range(len(keys))]
        c.update(zip(keys, interpolate(body.get(name, 0.5), anchors)))
    shares = [max(0.0, float(body.get(r, 1.0))) for r in RACES]
    total = sum(shares) or 3.0
    c.update({r: (s / total if sum(shares) else 1 / 3) for r, s in zip(RACES, shares)})
    return c


def target_list(body):
    """[(file relative to the data root, weight, newborn)] of every macro target the body uses,
    those with a weight under 1e-6 left out."""
    c = coefficients(body)
    out = []

    def add(rel, keys, newborn=False):
        w = 1.0
        for k in keys:
            w *= c[k]
        if w > 1e-6:
            out.append((rel, w, newborn))

    ages = [a for a, _ in AGES]
    for g in ("male", "female"):
        for age in ages:
            load = "baby" if age == "newborn" else age
            nb = age == "newborn"
            for mu in LEVELS["muscle"]:
                for we in LEVELS["weight"]:
                    add(f"targets/macrodetails/universal-{g}-{load}-{mu}-{we}.target.gz", (g, age, mu, we), nb)
                    for he in LEVELS["height"]:
                        add(f"targets/macrodetails/height/{g}-{load}-{mu}-{we}-{he}.target.gz",
                            (g, age, mu, we, he), nb)
                    if age not in ("newborn", "baby"):
                        for pr in LEVELS["proportions"]:
                            add(f"targets/macrodetails/proportions/{g}-{age}-{mu}-{we}-{pr}.target.gz",
                                (g, age, mu, we, pr))
                        if g == "female":
                            for cu in LEVELS["cupsize"]:
                                for fi in LEVELS["firmness"]:
                                    add(f"targets/breast/female-{age}-{mu}-{we}-{cu}-{fi}.target.gz",
                                        (g, age, mu, we, cu, fi))
            for race in RACES:
                add(f"targets/macrodetails/{race}-{g}-{load}.target.gz", (race, g, age), nb)
    return out


class BodyData:
    """The base mesh, its groups, the rig and the weights, read once from the data folder."""

    def __init__(self, root, cache_dir=None):
        self.root = root
        self.cache_dir = cache_dir
        self._read_obj(os.path.join(root, "3dobjs", "base.obj"))
        self.rig = json.load(open(os.path.join(root, RIG_FILE), encoding="utf-8"))
        self.rig = self.rig.get("bones", self.rig)
        self.bone_weights = json.load(open(os.path.join(root, WEIGHTS_FILE), encoding="utf-8"))["weights"]

    def _read_obj(self, path):
        verts, uvs, groups, current = [], [], {}, None
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("v "):
                    verts.append([float(x) for x in line.split()[1:4]])
                elif line.startswith("vt "):
                    uvs.append([float(x) for x in line.split()[1:3]])
                elif line.startswith("g "):
                    current = groups.setdefault(line.split(None, 1)[1].strip(), {"faces": [], "uvs": []})
                elif line.startswith("f ") and current is not None:
                    corners = [c.split("/") for c in line.split()[1:]]
                    current["faces"].append([int(c[0]) - 1 for c in corners])
                    current["uvs"].append([int(c[1]) - 1 if len(c) > 1 and c[1] else 0 for c in corners])
        self.template = _to_z_up(verts)
        self.uvs = uvs
        self.groups = groups
        for g in groups.values():
            g["verts"] = sorted({v for face in g["faces"] for v in face})

    def target(self, rel):
        """One target's offsets for every vertex (metres, Z up), cached as .npy when a cache folder is set."""
        cached = None
        if self.cache_dir:
            key = hashlib.sha1(rel.encode()).hexdigest()[:16]
            cached = os.path.join(self.cache_dir, key + ".npy")
            if os.path.isfile(cached):
                return np.load(cached)
        out = np.zeros_like(self.template)
        with gzip.open(os.path.join(self.root, rel), "rt") as f:
            rows = [line.split() for line in f if line.strip()]
        if rows:
            idx = np.array([int(r[0]) for r in rows])
            out[idx] = _to_z_up([[float(x) for x in r[1:4]] for r in rows])
        if cached:
            os.makedirs(self.cache_dir, exist_ok=True)
            np.save(cached, out)
        return out

    def local_targets(self):
        """{slider name: (negative target, positive target)} of the local sliders (target.json):
        nose, eyes, mouth, chin, head shape, torso, legs... Each slider runs from -1 to 1."""
        if getattr(self, "_local", None) is None:
            meta = json.load(open(os.path.join(self.root, "targets", "target.json"), encoding="utf-8"))
            out = {}
            for key, group in meta.items():
                if key == "genitals":
                    continue
                for cat in group.get("categories", []):
                    opp = cat.get("opposites") or {}
                    for side in ("unsided", "left", "right"):
                        neg, pos = opp.get(f"negative-{side}"), opp.get(f"positive-{side}")
                        if neg and pos:
                            name = cat["name"] if side == "unsided" else f"{cat['name']}-{side[0]}"
                            out[name] = (f"targets/{key}/{neg}.target.gz", f"targets/{key}/{pos}.target.gz")
            self._local = out
        return self._local

    def shape(self, body):
        """The base mesh's vertices shaped to the body's sliders, then its local sliders
        (body["local"]: {slider: -1..1}; a sided slider without its -l/-r suffix moves both sides)."""
        verts = self.template.copy()
        table = self.local_targets()
        for name, value in (body.get("local") or {}).items():
            value = max(-1.0, min(1.0, float(value)))
            names = [name] if name in table else [n for n in (f"{name}-l", f"{name}-r") if n in table]
            for n in names:
                rel = table[n][1] if value > 0 else table[n][0]
                if abs(value) > 1e-4 and os.path.isfile(os.path.join(self.root, rel)):
                    verts += abs(value) * self.target(rel)
        for rel, w, newborn in target_list(body):
            if rel.startswith("targets/breast/") and not os.path.isfile(os.path.join(self.root, rel)):
                continue  # the average breast is the base shape itself: MakeHuman ships no file for it
            t = self.target(rel)
            if newborn:
                t = NEWBORN_SCALE[None, :] * t + ((NEWBORN_SCALE[None, :] - 1) / NEWBORN_BLOCKS) * self.template
            verts += w * t
        return verts

    def region(self, folder, words=("flap", "lobe", "wing")):
        """The vertices a folder of local targets really moves (targets/ears: the ears): those a
        target named with one of `words` moves by at least 30% of its largest move (a target also
        nudges the skin round it a little; that is not the ear)."""
        key = "_region_" + folder
        if not hasattr(self, key):
            idx = set()
            base = os.path.join(self.root, "targets", folder)
            for name in os.listdir(base) if os.path.isdir(base) else []:
                if name.endswith(".target.gz") and any(w in name for w in words):
                    d = np.linalg.norm(self.target(f"targets/{folder}/{name}"), axis=1)
                    if d.max() > 0:
                        idx.update(np.where(d >= 0.5 * d.max())[0].tolist())
            setattr(self, key, np.array(sorted(idx), dtype=np.int64))
        return getattr(self, key)

    def _point(self, verts, spec):
        if spec["strategy"] == "VERTEX":
            idx = [spec["vertex_index"]]
        elif spec["strategy"] == "CUBE":
            idx = self.groups[spec["cube_name"]]["verts"]
        else:
            idx = spec["vertex_indices"]
        return verts[np.asarray(idx, dtype=np.int64)].mean(axis=0)

    def bones(self, verts):
        """The rig's bones, parents first: [{"name", "parent", "head", "tail", "roll"}]."""
        order, seen = [], set()

        def visit(name):
            if name in seen:
                return
            parent = self.rig[name].get("parent") or ""
            if parent:
                visit(parent)
            seen.add(name)
            order.append(name)

        for name in self.rig:
            visit(name)
        return [{"name": n, "parent": self.rig[n].get("parent") or "",
                 "head": self._point(verts, self.rig[n]["head"]).tolist(),
                 "tail": self._point(verts, self.rig[n]["tail"]).tolist(),
                 "roll": float(self.rig[n].get("roll", 0.0))} for n in order]

    def faces(self, groups):
        """The faces (and their texture corners) of the named groups, with the nudity edits applied."""
        faces, uvf = [], []
        for name in groups:
            g = self.groups[name]
            for f, u in zip(g["faces"], g["uvs"]):
                if name == "body" and any(v in _DISCARD for v in f):
                    continue
                faces.append(f)
                uvf.append(u)
        if "body" in groups:
            corner_uv = {}
            g = self.groups["body"]
            for f, u in zip(g["faces"], g["uvs"]):
                for v, t in zip(f, u):
                    corner_uv.setdefault(v, t)
            for cap in _CAPS:
                faces.append(list(cap))
                uvf.append([corner_uv.get(v, 0) for v in cap])
        return faces, uvf


_DATA = {}


def body_data(root, cache_dir=None):
    key = (root, cache_dir)
    if key not in _DATA:
        _DATA[key] = BodyData(root, cache_dir)
    return _DATA[key]


# heights in cm by age (years) for (male, female): typical medians, used when the sheet gives none
HEIGHTS = [(0, 50, 49), (1, 76, 74), (2, 87, 86), (4, 103, 102), (6, 116, 115), (8, 128, 127), (10, 138, 138),
           (12, 149, 151), (14, 164, 160), (16, 174, 163), (18, 176, 164), (30, 177, 164), (50, 176, 163),
           (70, 173, 160), (90, 169, 156)]


def typical_height_cm(age, female):
    """The median height for this age (years) and sex (0 male .. 1 female)."""
    age = min(max(float(age), 0.0), 90.0)
    for (a0, m0, f0), (a1, m1, f1) in zip(HEIGHTS, HEIGHTS[1:]):
        if age <= a1:
            t = (age - a0) / (a1 - a0)
            male, fem = m0 + t * (m1 - m0), f0 + t * (f1 - f0)
            return male + float(female) * (fem - male)
    return HEIGHTS[-1][1] + float(female) * (HEIGHTS[-1][2] - HEIGHTS[-1][1])


def _bone_weight(data, n, names):
    w = np.zeros(n)
    for name in names:
        for vi, wt in data.bone_weights.get(name, []):
            w[int(vi)] += float(wt)
    return np.clip(w, 0.0, 1.0)


def restyle(data, verts, prop):
    """A drawing style's proportions, before the body is scaled to its height: the head (and what
    rides on it) and the hands grow about their joints by how much each vertex follows them, the
    legs lengthen below the hip joints. Every vertex array (body, eyes, shells) moves together, and
    the skeleton is placed from the result, so it fits."""
    head, hands, legs, eyes = (float(prop.get(k, 1.0)) for k in ("head", "hands", "legs", "eyes"))
    if all(abs(v - 1) < 1e-4 for v in (head, hands, legs, eyes)):
        return verts
    verts = verts.copy()
    bones = {b["name"]: b for b in data.bones(verts)}
    n = len(verts)
    if abs(eyes - 1) > 1e-4:  # drawn eyes: each eyeball and the skin round it grow about the eye's centre
        for group in EYE_GROUPS:
            if group not in data.groups:
                continue
            ball = np.asarray(data.groups[group]["verts"])
            c = verts[ball].mean(axis=0)
            r = float(np.linalg.norm(verts[ball] - c, axis=1).max())
            d = np.linalg.norm(verts - c, axis=1)
            fall = np.clip(1.0 - (d - 1.2 * r) / (1.6 * r), 0.0, 1.0)  # whole within 1.2 eye radii, none past 2.8
            fall[ball] = 1.0
            verts += (eyes - 1) * fall[:, None] * (verts - c)
    if abs(head - 1) > 1e-4:
        w = _bone_weight(data, n, ["head"])
        c = np.array(bones["head"]["head"])
        verts += (head - 1) * w[:, None] * (verts - c)
    if abs(hands - 1) > 1e-4:
        for side in ("l", "r"):
            names = [b for b in bones if b.endswith("_" + side) and (b.startswith(("hand", "thumb", "index", "middle",
                                                                                   "ring", "pinky")))]
            w = _bone_weight(data, n, names)
            c = np.array(bones["hand_" + side]["head"])
            verts += (hands - 1) * w[:, None] * (verts - c)
    if abs(legs - 1) > 1e-4:
        hip = float(bones["thigh_l"]["head"][2])
        floor = float(verts[:, 2].min())
        z = verts[:, 2]
        below = z < hip
        verts[:, 2] = np.where(below, hip - (hip - z) * legs, z) + (hip - floor) * (legs - 1)
    return verts


def make_body(body, root, cache_dir=None):
    """The body as data: {"vertices", "bones", "parts": {name: {"faces", "face_uvs", "verts"}},
    "uvs", "weights" (per vertex [[bone index, weight]]), "height"}. The mesh is scaled to the
    body's height_cm (else the median for its age and sex), feet on the floor, centred on X and Y."""
    data = body_data(root, cache_dir)
    verts = data.shape(body)
    verts = restyle(data, verts, body.get("style_proportions") or {})
    body_faces, body_uvs = data.faces(["body"])
    used = sorted({v for f in body_faces for v in f})
    lo, hi = verts[used].min(axis=0), verts[used].max(axis=0)
    want = float(body.get("height_cm") or typical_height_cm(body.get("age", 25), body.get("female", 0.5))) / 100.0
    scale = want / max(hi[2] - lo[2], 1e-6)
    centre = np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]])
    verts = (verts - centre) * scale
    bones = data.bones(verts)
    feet = [b["head"] for b in bones if b["name"] in ("foot_l", "foot_r")]
    if len(feet) == 2:  # stand between the ankles, not the middle of the box the arms make
        mid = np.array([(feet[0][0] + feet[1][0]) / 2, (feet[0][1] + feet[1][1]) / 2, 0.0])
        verts = verts - mid
        bones = data.bones(verts)
    names = [b["name"] for b in bones]
    weights = [[] for _ in range(len(verts))]
    for bi, name in enumerate(names):
        for vi, w in data.bone_weights.get(name, []):
            weights[int(vi)].append([bi, float(w)])
    parts = {"body": {"faces": body_faces, "face_uvs": body_uvs}}
    eye_faces, eye_uvs = data.faces([g for g in EYE_GROUPS if g in data.groups])
    if eye_faces:
        parts["eyes"] = {"faces": eye_faces, "face_uvs": eye_uvs}
    lashes = [g for g in data.groups if "eyelashes" in g]
    if lashes:
        f, u = data.faces(lashes)
        parts["eyelashes"] = {"faces": f, "face_uvs": u}
    # the fitted shells clothes and hair are made from: shaped and skinned with the body
    helpers = {}
    for name in HELPER_GROUPS:
        if name in data.groups:
            f, u = data.faces([name])
            helpers[name] = {"faces": f, "face_uvs": u}
    return {"vertices": verts, "bones": bones, "parts": parts, "uvs": data.uvs, "weights": weights,
            "height": want, "head_top": float((hi[2] - lo[2]) * scale), "helpers": helpers,
            "ears": data.region("ears")}


# --- into the scene ---------------------------------------------------------------------

def rig_object():
    """The character's armature in the scene, or None."""
    return next((o for o in bpy.context.scene.objects if o.type == "ARMATURE" and o.get(RIG_KEY)), None)


def _link(obj):
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _armature(bones):
    """The armature from the bones: heads, tails and rolls as given, each bone under its parent."""
    arm = bpy.data.armatures.new(RIG_NAME)
    arm.display_type = "STICK"
    obj = _link(bpy.data.objects.new(RIG_NAME, arm))
    obj[RIG_KEY] = True
    obj.show_in_front = True
    bpy.context.view_layer.objects.active = obj
    for o in bpy.context.scene.objects:
        o.select_set(o is obj)
    bpy.ops.object.mode_set(mode="EDIT")
    made = {}
    for b in bones:
        eb = arm.edit_bones.new(b["name"])
        eb.head = Vector(b["head"])
        tail = Vector(b["tail"])
        if (tail - eb.head).length < 1e-4:
            tail = eb.head + Vector((0.0, 0.0, 0.01))
        eb.tail = tail
        eb.roll = float(b.get("roll", 0.0))
        eb.use_deform = b["name"] != "Root"
        made[b["name"]] = eb
    for b in bones:
        parent = made.get(b.get("parent") or "")
        if parent is not None:
            made[b["name"]].parent = parent
            made[b["name"]].use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")
    return obj


def _part_mesh(name, verts, faces, uvs, face_uvs):
    """A mesh of only the vertices its faces use; returns (mesh, the original index of each vertex)."""
    used = sorted({v for f in faces for v in f})
    remap = {v: i for i, v in enumerate(used)}
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(verts[v]) for v in used], [], [[remap[v] for v in f] for f in faces])
    me.validate(clean_customdata=False)
    if uvs and len(face_uvs) == len(me.polygons):
        layer = me.uv_layers.new(name="UVMap")
        for poly, fuv in zip(me.polygons, face_uvs):
            for li, ui in zip(poly.loop_indices, fuv):
                layer.data[li].uv = uvs[ui] if ui < len(uvs) else (0.0, 0.0)
    for poly in me.polygons:
        poly.use_smooth = True
    me.update()
    return me, used


def _weights(obj, bone_names, weights, used):
    """Vertex groups from per-vertex [[bone index, weight]], the strongest four kept, renormalised."""
    groups = [obj.vertex_groups.new(name=n) for n in bone_names]
    for new_i, old_i in enumerate(used):
        pairs = sorted(((b, w) for b, w in weights[old_i] if w > 1e-4), key=lambda p: -p[1])[:MAX_INFLUENCES]
        total = sum(w for _, w in pairs) or 1.0
        for b, w in pairs:
            groups[b].add([new_i], w / total, "REPLACE")


def _smooth_subdivide(obj, cuts):
    """Subdivide a mesh smoothly in place; its vertex groups (skin weights) are interpolated."""
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=cuts, use_grid_fill=True, smooth=1.0)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def build(body, root, cache_dir=None):
    """Put a rigged body for these sliders in the scene; returns {"rig", "parts": {name: object},
    "height", "bones"}."""
    made = make_body(body, root, cache_dir)
    rig = _armature(made["bones"])
    names = [b["name"] for b in made["bones"]]
    parts = {}
    for name, part in made["parts"].items():
        me, used = _part_mesh(name, made["vertices"], part["faces"], made["uvs"], part["face_uvs"])
        obj = _link(bpy.data.objects.new(name, me))
        obj[helpers.PART_KEY] = name
        obj[helpers.NO_BEVEL_KEY] = True
        _weights(obj, names, made["weights"], used)
        if name == "eyes":
            _smooth_subdivide(obj, 2)  # the base mesh's eyeballs are too coarse for a round iris
        obj.parent = rig
        mod = obj.modifiers.new("rig", "ARMATURE")
        mod.object = rig
        mod.use_vertex_groups = True
        parts[name] = obj
    bpy.context.view_layer.update()
    return {"rig": rig, "parts": parts, "height": made["height"], "bones": len(names), "data": made}


def skinned_part(name, made, faces, face_uvs, rig, tag=True):
    """A mesh of the body data's vertices for these faces, skinned to the rig like the body."""
    me, used = _part_mesh(name, made["vertices"], faces, made["uvs"], face_uvs)
    obj = _link(bpy.data.objects.new(name, me))
    if tag:
        obj[helpers.PART_KEY] = name
        obj[helpers.NO_BEVEL_KEY] = True
    _weights(obj, [b["name"] for b in made["bones"]], made["weights"], used)
    obj.parent = rig
    mod = obj.modifiers.new("rig", "ARMATURE")
    mod.object = rig
    mod.use_vertex_groups = True
    return obj, used


def skinned(obj):
    """Whether a mesh object is driven by the character's skeleton."""
    return any(m.type == "ARMATURE" and m.object is not None for m in obj.modifiers)


# --- materials ------------------------------------------------------------------------

def _srgb(hex_colour):
    h = hex_colour.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c] + [1.0]


def _material(name, colour, roughness=0.5, subsurface=0.0, specular=0.5):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = _srgb(colour)
    bsdf.inputs["Roughness"].default_value = roughness
    if subsurface and "Subsurface Weight" in bsdf.inputs:
        bsdf.inputs["Subsurface Weight"].default_value = subsurface
        bsdf.inputs["Subsurface Radius"].default_value = (1.0, 0.35, 0.2)
        bsdf.inputs["Subsurface Scale"].default_value = 0.01
    if "Specular IOR Level" in bsdf.inputs:
        bsdf.inputs["Specular IOR Level"].default_value = specular
    mat.diffuse_color = _srgb(colour)
    return mat


IRIS_DEG, PUPIL_DEG = 30.0, 12.0  # angles from the eye's forward axis: an iris about 12 mm across


def dress(parts, sheet):
    """Skin on the body; the eyes white, with the iris and the pupil on the faces that look forward."""
    skin = _material("skin", sheet.get("skin") or "#e3b9a0", roughness=0.5, subsurface=0.25, specular=0.35)
    body = parts.get("body")
    if body is not None:
        body.data.materials.append(skin)
    lashes = parts.get("eyelashes")
    if lashes is not None:
        lashes.data.materials.append(_material("eyelashes", "#1a1410", 0.6))
    eyes = parts.get("eyes")
    if eyes is None:
        return
    for m in (_material("sclera", "#ece8e2", 0.25, specular=0.6), _material("iris", sheet.get("eyes") or "#4a3322", 0.3),
              _material("pupil", "#050505", 0.15), _material("eye_glint", "#ffffff", 0.2)):
        eyes.data.materials.append(m)
    iris_deg = float(sheet.get("iris_deg") or IRIS_DEG)
    pupil_deg = PUPIL_DEG * iris_deg / IRIS_DEG
    glint = sheet.get("glint")  # a drawn eye's white highlight, up and to one side of the pupil
    glint_dir = np.array([-0.35, -1.0, 0.4]) / np.linalg.norm([-0.35, -1.0, 0.4])
    me = eyes.data
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    left = co[:, 0] > 0
    centres = {True: co[left].mean(axis=0), False: co[~left].mean(axis=0)}
    forward = np.array([0.0, -1.0, 0.0])
    for poly in me.polygons:
        c = np.array(poly.center)
        d = c - centres[bool(c[0] > 0)]
        d /= max(np.linalg.norm(d), 1e-9)
        angle = math.degrees(math.acos(max(-1.0, min(1.0, float(d @ forward)))))
        poly.material_index = 2 if angle < pupil_deg else 1 if angle < iris_deg else 0
        if glint and math.degrees(math.acos(max(-1.0, min(1.0, float(d @ glint_dir))))) < 0.28 * iris_deg:
            poly.material_index = 3


# --- clips ----------------------------------------------------------------------------

def _key(rig, frames, values):
    """Key pose-bone rotations: values {bone: [(x, y, z) degrees per key]} over the frames."""
    for bone, keys in values.items():
        pb = rig.pose.bones.get(bone)
        if pb is None:
            continue
        pb.rotation_mode = "XYZ"
        for f, rot in zip(frames, keys):
            pb.rotation_euler = Euler([math.radians(a) for a in rot])
            pb.keyframe_insert("rotation_euler", frame=f)


def rig_test_clip(rig, fps=24, seconds=4.0):
    """A short loop that moves every main joint, so the skinning can be seen working before real
    clips exist: breathe, look round, raise the right arm and wave, bend a knee, back to rest."""
    action = bpy.data.actions.new("rig_test")
    rig.animation_data_create()
    rig.animation_data.action = action
    n = int(round(fps * seconds))
    frames = [1, n // 4 + 1, n // 2 + 1, 3 * n // 4 + 1, n + 1]
    z = (0, 0, 0)
    _key(rig, frames, {
        "spine_03": [z, (3, 0, 0), z, (3, 0, 0), z],
        "head": [z, (0, 35, 0), (-8, 0, 0), (0, -35, 0), z],
        "upperarm_r": [z, (0, 0, -75), (0, 0, -80), (0, 0, -75), z],
        "lowerarm_r": [z, (40, 0, 0), (75, 0, 0), (40, 0, 0), z],
        "hand_r": [z, (0, 0, 25), (0, 0, -25), (0, 0, 25), z],
        "upperarm_l": [z, (0, 0, 10), (25, 0, 10), (0, 0, 10), z],
        "thigh_l": [z, z, (-35, 0, 0), z, z],
        "calf_l": [z, z, (50, 0, 0), z, z],
    })
    for fc in getattr(action, "fcurves", []) or []:
        for kp in fc.keyframe_points:
            kp.interpolation = "BEZIER"
    scene = bpy.context.scene
    scene.render.fps = fps
    scene.frame_start, scene.frame_end = 1, n
    return {"name": "rig_test", "frames": n, "fps": fps, "seconds": seconds}


CLIPS = {"rig_test": rig_test_clip}


def build_character(step):
    """The job step: the body for the sheet, its skin and eyes, its clips. Returns what was made."""
    made = build(step["body"], step["root"], step.get("cache"))
    look = step.get("look") or {}
    dress(made["parts"], {**(step.get("sheet") or {}),
                          **{k: look[k] for k in ("skin", "eyes", "iris_deg", "glint") if look.get(k)}})
    pieces = {}
    if look:
        from . import character_parts
        dressed = character_parts.dress_up(look, made["data"], made["rig"], made["parts"])
        pieces = dressed["pieces"]
        if look.get("shading") == "toon":
            character_parts.toonify([o for o in bpy.context.scene.objects if o.type == "MESH" and o.get(helpers.PART_KEY)],
                                    made["rig"], look.get("outline", 0.004))
    clips = []
    for name in step.get("clips") or []:
        fn = CLIPS.get(name)
        if fn is not None:
            clips.append(fn(made["rig"]))
    springs = {}
    if pieces and step.get("physics", True):
        from . import physics_bake
        springs = physics_bake.run(made["rig"], list(pieces.values()), made["parts"]["body"],
                                   fps=bpy.context.scene.render.fps)
    for pb in made["rig"].pose.bones:
        pb.rotation_euler = Euler((0, 0, 0))
        pb.rotation_quaternion = (1, 0, 0, 0)
    bpy.context.scene.frame_set(bpy.context.scene.frame_start)
    every = {**made["parts"], **pieces}
    lo, hi = helpers.bounds(list(every.values()))
    return {"height": round(made["height"], 4), "bones": len(made["rig"].data.bones), "clips": clips,
            "springs": springs,
            "size": [round(v, 4) for v in (hi - lo)], "ground": round(lo.z, 5),
            "parts": {o.name: {"vertices": len(o.data.vertices), "faces": len(o.data.polygons)}
                      for o in every.values()}}


# --- checks ---------------------------------------------------------------------------

MIN_EDGE = 0.004  # edges shorter than this are left out of the stretch test
TEST_POSES = {
    "arms_up": {"upperarm_l": (0, 0, 60), "upperarm_r": (0, 0, -60)},
    "elbows_bent": {"lowerarm_l": (0, 0, 70), "lowerarm_r": (0, 0, -70)},
    "knee_up": {"thigh_l": (-70, 0, 0), "calf_l": (90, 0, 0)},
    "look_round": {"head": (0, 40, 0), "neck_01": (15, 0, 0)},
    "twist": {"spine_02": (0, 30, 0), "spine_03": (0, 20, 0)},
}


def _pose(rig, angles):
    """Rotate pose bones by {name: (x, y, z) degrees} from rest; names missing from the rig are
    skipped. A bone keeps its rotation mode (a spring bone's baked keys are quaternions, and a bone
    switched to Euler would ignore them)."""
    for pb in rig.pose.bones:
        pb.rotation_euler = Euler((0, 0, 0))
        pb.rotation_quaternion = (1, 0, 0, 0)
    for name, rot in angles.items():
        pb = rig.pose.bones.get(name)
        if pb is not None:
            e = Euler([math.radians(a) for a in rot])
            if pb.rotation_mode == "QUATERNION":
                pb.rotation_quaternion = e.to_quaternion()
            else:
                pb.rotation_euler = e
    bpy.context.view_layer.update()


def _edge_lengths(obj, evaluated=True):
    if evaluated:
        dg = bpy.context.evaluated_depsgraph_get()
        ev = obj.evaluated_get(dg)
        me = ev.to_mesh()
    else:
        me = obj.data
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    ed = np.empty(len(me.edges) * 2, dtype=np.int64)
    me.edges.foreach_get("vertices", ed)
    ed = ed.reshape(-1, 2)
    out = np.linalg.norm(co[ed[:, 0]] - co[ed[:, 1]], axis=1)
    if evaluated:
        ev.to_mesh_clear()
    return out


def rig_report(test_poses=None):
    """What the skeleton and the skinning are like: bones, any vertex with no weight, the largest
    weight count per vertex, and how far edges stretch in test poses (the 99.9th percentile ratio of
    posed to rest length; near 1 means the skin follows the bones cleanly, over 3 means a vertex is
    weighted to the wrong bone)."""
    rig = rig_object()
    if rig is None:
        return {"rig": False}
    test_poses = TEST_POSES if test_poses is None else test_poses
    bodies = [o for o in bpy.context.scene.objects if o.type == "MESH" and o.get(helpers.PART_KEY) and skinned(o)]
    names = {b.name for b in rig.data.bones}
    out = {"rig": True, "bones": len(rig.data.bones), "deform_bones": sum(1 for b in rig.data.bones if b.use_deform),
           "bone_names": sorted(names), "skinned": sorted(o.name for o in bodies), "unweighted": {},
           "max_influences": 0, "stretch": {}, "foreign_groups": {}}
    for obj in bodies:
        missing, most = 0, 0
        for v in obj.data.vertices:
            n = sum(1 for g in v.groups if g.weight > 1e-4)
            most = max(most, n)
            missing += n == 0
        out["unweighted"][obj.name] = missing
        out["max_influences"] = max(out["max_influences"], most)
        out["foreign_groups"][obj.name] = sorted(g.name for g in obj.vertex_groups if g.name not in names)
    rest = {o.name: _edge_lengths(o, evaluated=False) for o in bodies}
    out["stretch_by_part"] = {}
    for label, angles in test_poses.items():
        _pose(rig, angles)
        worst = 1.0
        for obj in bodies:
            posed = _edge_lengths(obj)
            r = rest[obj.name]
            ok = r > MIN_EDGE  # a cloth's thickness edges are a few mm: their ratio swings without meaning
            ratio = posed[ok] / r[ok]  # stretching tears; squeezing (cloth bunching in a bend) does not
            value = float(np.percentile(ratio, 99.9)) if ratio.size else 1.0
            worst = max(worst, value)
            part = out["stretch_by_part"].setdefault(obj.name, {})
            part[label] = round(value, 3)
        out["stretch"][label] = round(worst, 3)
        if label in POKE_POSES:
            out.setdefault("poke", {})[label] = poke_through(bodies)
    out.setdefault("poke", {})["rest"] = (_pose(rig, {}), poke_through(bodies))[1]
    _pose(rig, {})
    return out


POKE_POSES = ("arms_up", "knee_up", "twist")
SKIN_PARTS = ("body",)
NOT_CLOTH = ("body", "eyes", "eyelashes", "brows", "hair", "hair_strands", "hair_spikes", "ponytail", "bun", "glasses")


def poke_through(objs, near=0.03):
    """How much skin shows through the clothes as the body is posed now: of the body vertices with
    a garment within `near` metres outside them, the share whose garment surface is found inside
    the skin instead (cast inwards along the vertex normal, up to 2 cm). 0 is perfect."""
    from mathutils.bvhtree import BVHTree
    dg = bpy.context.evaluated_depsgraph_get()
    cloth = [o for o in objs if not o.name.split(".")[0].startswith(NOT_CLOTH)]
    body = next((o for o in objs if o.name.split(".")[0] == "body"), None)
    if not cloth or body is None:
        return {"checked": 0, "through": 0, "share": 0.0}
    trees = [BVHTree.FromObject(o, dg) for o in cloth]
    ev = body.evaluated_get(dg)
    me = ev.to_mesh()
    checked = through = 0
    for v in me.vertices:
        p, n = v.co, v.normal
        outside = any(t.ray_cast(p + n * 0.0005, n, near)[0] is not None for t in trees)
        inside = any(t.ray_cast(p - n * 0.0005, -n, 0.012)[0] is not None for t in trees)
        if outside or inside:
            checked += 1
            if inside and not outside:
                through += 1
    ev.to_mesh_clear()
    return {"checked": checked, "through": through, "share": round(through / checked, 4) if checked else 0.0}
