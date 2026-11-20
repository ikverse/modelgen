"""ModelGen's own physics engine (modelgen/physics): spring chains keep their lengths, hang under
gravity, spring back to the animated pose and stay out of the body's colliders; a ragdoll falls,
keeps its bones' lengths and its joints' limits, and comes to rest on the floor."""

import math

from modelgen.physics import engine as ph

DT = 1 / 30


def _chain(stiffness=0.0, n=5, seg=0.1):
    pts = [(0.0, 0.0, 1.0 - 0.0)] + [(seg * i, 0.0, 1.0) for i in range(1, n)]  # sticking out sideways
    return ph.Chain("tail", pts, stiffness=stiffness, drag=0.05, radius=0.0), pts


def test_a_loose_chain_swings_down_and_keeps_its_lengths():
    w = ph.World()
    ch, pts = _chain()
    w.add_chain(ch)
    for _ in range(90):
        ch.set_targets(pts)
        w.step(DT)
    pos = ch.positions()
    for a, b in zip(pos, pos[1:]):
        assert abs(ph.length(ph.sub(b, a)) - 0.1) < 0.002
    assert pos[-1][2] < 0.7  # it hangs: the tip is well below the root
    assert pos[0] == pts[0]  # the root rides on its bone


def test_a_stiff_chain_holds_close_to_its_animated_pose():
    w = ph.World()
    stiff, pts = _chain(stiffness=0.6)
    w.add_chain(stiff)
    for _ in range(60):
        stiff.set_targets(pts)
        w.step(DT)
    assert abs(stiff.positions()[-1][2] - 1.0) < 0.15  # sags a little under gravity, no more


def test_a_chain_follows_its_root_and_lags_behind_it():
    w = ph.World(gravity=(0, 0, 0))
    ch, pts = _chain(stiffness=0.1)
    w.add_chain(ch)
    lags = []
    for f in range(30):
        moved = [ph.add(p, (0.0, 0.03 * f, 0.0)) for p in pts]
        ch.set_targets(moved)
        w.step(DT)
        lags.append(ch.positions()[0][1] - ch.positions()[-1][1])
    assert ch.positions()[0][1] == moved[0][1]  # the root rides on its bone
    assert max(lags[:10]) > 0.005 and ch.positions()[-1][1] > 0.5  # it trails behind at first, then follows


def test_colliders_keep_hair_out_of_the_body():
    w = ph.World()
    ch = ph.Chain("hair", [(0.0, 0.0, 1.0), (0.0, 0.0, 0.9), (0.0, 0.0, 0.8), (0.0, 0.0, 0.7)],
                  stiffness=0.0, radius=0.01)
    w.add_chain(ch)
    body = ph.Capsule((0.0, 0.05, 0.4), (0.0, 0.05, 0.95), 0.06)  # a back just behind the hair
    w.colliders.append(body)
    pts = ch.positions()
    for _ in range(60):
        ch.set_targets(pts)
        w.step(DT)
    for p in ch.particles[1:]:
        c = ph.closest_on_segment(p.pos, body.a, body.b)
        assert ph.length(ph.sub(p.pos, c)) >= body.radius + p.radius - 0.001  # within a millimetre


def test_the_same_motion_whatever_the_substeps():
    tips = []
    for sub in (2, 8):
        w = ph.World(substeps=sub)
        ch, pts = _chain(stiffness=0.3)
        w.add_chain(ch)
        for _ in range(45):
            ch.set_targets(pts)
            w.step(DT)
        tips.append(ch.positions()[-1])
    assert ph.length(ph.sub(tips[0], tips[1])) < 0.08


def _standing():
    return {"pelvis": (0, 0, 0.95), "spine_02": (0, 0, 1.2), "neck_01": (0, 0, 1.45), "head_top": (0, 0, 1.72),
            "upperarm_l": (0.18, 0, 1.42), "lowerarm_l": (0.45, 0, 1.4), "hand_l": (0.7, 0, 1.38),
            "upperarm_r": (-0.18, 0, 1.42), "lowerarm_r": (-0.45, 0, 1.4), "hand_r": (-0.7, 0, 1.38),
            "thigh_l": (0.1, 0, 0.92), "calf_l": (0.1, 0, 0.5), "foot_l": (0.1, 0, 0.08),
            "thigh_r": (-0.1, 0, 0.92), "calf_r": (-0.1, 0, 0.5), "foot_r": (-0.1, 0, 0.08)}


def test_a_ragdoll_falls_keeps_its_bones_and_rests_on_the_floor():
    joints = _standing()
    w, names = ph.ragdoll_world(joints, radius=0.05, velocity=(0.0, 0.03, 0.0))  # a push backwards
    for _ in range(240):
        w.step(DT)
    pos = {n: w.particles[i].pos for i, n in enumerate(names)}
    for a, b in ph.RAGDOLL_BONES:
        rest = ph.length(ph.sub(joints[b], joints[a]))
        assert abs(ph.length(ph.sub(pos[b], pos[a])) - rest) < 0.02 * max(rest, 0.1), (a, b)
    assert all(p[2] >= 0.05 - 1e-6 for p in pos.values())  # never through the floor
    assert max(p[2] for p in pos.values()) < 0.5  # lying down, not standing
    before = [p.pos for p in w.particles]
    w.step(DT)
    assert max(ph.length(ph.sub(p.pos, q)) for p, q in zip(w.particles, before)) < 0.01  # at rest


def test_a_ragdoll_knee_never_bends_past_its_limit():
    joints = _standing()
    w, names = ph.ragdoll_world(joints, velocity=(0.0, -0.04, 0.0))
    idx = {n: i for i, n in enumerate(names)}
    for _ in range(150):
        w.step(DT)
        a, b, c = (w.particles[idx[n]].pos for n in ("thigh_l", "calf_l", "foot_l"))
        axis, bone = ph.norm(ph.sub(b, a)), ph.norm(ph.sub(c, b))
        assert math.degrees(math.acos(max(-1, min(1, ph.dot(axis, bone))))) <= 141
