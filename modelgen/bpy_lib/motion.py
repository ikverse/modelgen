"""Motion for a finished model: wheels that turn, a body that bounces, and markers where smoke or
steam rises. Runs inside Blender only, after the assembly and materials scripts, as `m` in the
animator's module.

The motion loops in place: the vehicle stays where the assembler put it and the page slides the
ground under it, so the downloaded model is still usable in a game, which moves the vehicle itself.
Every helper keys its change on top of the assembled pose, so frame 1 is exactly the model the
checks measured and the renders show, and the pose one frame past the end equals frame 1, so the
loop never jumps. Spinning is worked out in world space, round each wheel's own centre: a copy the
assembler turned round (the left-hand wheels get a 180 degree Z rotation) still rolls the way the
vehicle travels, and a wheel anchored at its bottom still turns round its hub.

spin and bounce only collect what to do; the keys are written when the animator's script has
finished (report), once every wheel is known: a wheel must make whole turns in one loop or the loop
jumps, so the loop length (2 to 4 seconds) is chosen to bring every wheel's whole turns as close as
possible to the travel speed.

Mechanisms: the driver gear is spun at its speed (spin with rpm) and mesh_drive turns every other
part from it at the exact ratio, the right way round. The loop is then the shortest stretch after
which every part looks exactly as it started (a plain gear after one tooth, a gear with a keyway
after a whole turn), at least half a second; a rack swings the whole mechanism back and forth.
"""

import json
import math
from fractions import Fraction

import bpy
from mathutils import Matrix, Vector

from . import helpers

FX_KEY = "mg_fx"            # on a marker empty: "smoke" or "steam"
FX_SIZE_KEY = "mg_fx_size"
FX_RATE_KEY = "mg_fx_rate"
MOTION_KEY = "mg_motion"    # on the motion empty (speed, direction, loop) and on spin pivots
SPIN_KEY = "mg_spin"        # on a spun object: its true radius in metres (the frame strip looks at it)
MOTION_NAME = "_mg_motion"
KINDS = ("smoke", "steam")
UP = Vector((0.0, 0.0, 1.0))
LOOP_RANGE_S = (2.0, 4.0)   # the loop lengths tried to make every wheel roll at the travel speed
GEAR_KEY = "mg_gear"        # a kit gear's numbers (JSON): teeth, pitch radius, type, picture period
MIN_LOOP_FRAMES = 12        # a mechanism's loop is at least this long (half a second at 24 fps)
MAX_LOOP_FRAMES = 480       # and at most this (a keyway on a gear with an odd ratio needs many turns)
SWING_MIN_FRAMES = 24

_S = {}


def _fresh(seconds=2.0, fps=24):
    _S.clear()
    _S.update(seconds=float(seconds), fps=int(fps), speed=0.0, forward=Vector((0.0, -1.0, 0.0)), fixed=False,
              spins=[], bounces=[], animated={}, markers=[], drives=[], mechanism=None, written=False)


_fresh()


def configure(settings):
    """Called by the job before the animator's script runs: the loop length and frame rate from
    config.yaml, and a clean slate for the report."""
    s = settings or {}
    _fresh(s.get("seconds", 2.0), s.get("fps", 24))
    try:  # new keys interpolate linearly: a wheel turns at one speed, not easing in and out
        bpy.context.preferences.edit.keyframe_new_interpolation_type = "LINEAR"
    except (AttributeError, TypeError):
        pass
    _apply_loop()


def _last_frame():
    return max(2, round(_S["seconds"] * _S["fps"]))


def _apply_loop():
    scene = bpy.context.scene
    scene.render.fps = _S["fps"]
    scene.render.fps_base = 1.0
    scene.frame_start = 1
    scene.frame_end = _last_frame()
    scene.frame_set(1)


def _flat(objs):
    if objs is None:
        return []
    if isinstance(objs, dict):
        objs = list(objs.values())
    if not isinstance(objs, (list, tuple)):
        return [objs]
    out = []
    for o in objs:
        out += _flat(o)
    return out


def _model():
    return [o for o in bpy.context.scene.objects if o.type == "MESH" and o.get(helpers.PART_KEY)]


def _motion_empty():
    obj = bpy.data.objects.get(MOTION_NAME)
    if obj is None:
        obj = bpy.data.objects.new(MOTION_NAME, None)
        obj.empty_display_size = 0.05
        bpy.context.scene.collection.objects.link(obj)
    obj[MOTION_KEY] = "motion"
    obj["mg_speed_mps"] = float(_S["speed"])
    obj["mg_forward"] = [float(v) for v in _S["forward"]]
    obj["mg_seconds"] = float(_S["seconds"])
    obj["mg_fps"] = int(_S["fps"])
    return obj


def loop(seconds=None, fps=None):
    """Set the loop length in seconds and the frame rate yourself. Left alone, the loop is 2 to 4
    seconds, chosen so every wheel rolls as close as possible to the travel speed; a loop you set
    is kept as it is, and the wheels are rounded to whole turns in it."""
    if seconds is not None:
        _S["seconds"] = max(0.25, float(seconds))
        _S["fixed"] = True
    if fps is not None:
        _S["fps"] = max(6, int(fps))
    _apply_loop()
    return {"seconds": _S["seconds"], "fps": _S["fps"], "frames": _last_frame()}


def travel(speed_kmh=None, speed_mps=None, forward=(0.0, -1.0, 0.0)):
    """How fast and which way the vehicle travels. The model stays in place (the page moves the
    ground under it); wheels spun without their own turns or rpm roll at this speed. forward is
    the direction the front faces, -Y by default as in every plan. Returns the speed in m/s."""
    if speed_kmh is not None:
        speed_mps = float(speed_kmh) / 3.6
    _S["speed"] = max(0.0, float(speed_mps or 0.0))
    f = Vector(forward)
    if f.length < 1e-9:
        raise ValueError("forward must be a direction, e.g. (0, -1, 0)")
    _S["forward"] = f.normalized()
    _motion_empty()
    return _S["speed"]


def _rotation_matrix(obj):
    mode = obj.rotation_mode
    if mode == "QUATERNION":
        return obj.rotation_quaternion.to_matrix()
    if mode == "AXIS_ANGLE":
        a = obj.rotation_axis_angle
        return Matrix.Rotation(a[0], 3, Vector(a[1:]))
    return obj.rotation_euler.to_matrix()


def _thinnest_axis(obj):
    s = helpers.size(obj)
    i = min(range(3), key=lambda k: s[k])
    v = Vector((0.0, 0.0, 0.0))
    v[i] = 1.0
    return v


def _hub(obj, axis):
    """The wheel's centre (the middle of its world bounding box) and its true radius: the farthest
    any point of its mesh lies from the axle through that centre. (A bounding box corner would
    overstate a round wheel's radius by about 40 percent.)"""
    bpy.context.view_layer.update()
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    m = ev.matrix_world
    if obj.get(GEAR_KEY):  # a kit part is built round its own axis: its origin is on it (an odd blade count
        centre = m.translation.copy()  # would put the box's middle off the axis and make it wobble)
    else:
        corners = [m @ Vector(c) for c in ev.bound_box]
        centre = sum(corners, Vector()) / len(corners)
    best = 0.0
    for v in ev.data.vertices:
        d = m @ v.co - centre
        best = max(best, (d - axis * d.dot(axis)).length_squared)
    return centre, max(math.sqrt(best), 1e-4)


def _fcurves(action):
    """Every F-curve of an action: the flat list of Blender 4 and earlier, or the curves of each
    channel bag of a layered action (Blender 4.4 and later, the only kind in 5)."""
    found = getattr(action, "fcurves", None)
    if found is not None:
        return list(found)
    out = []
    for layer in getattr(action, "layers", []):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", []):
                out += list(bag.fcurves)
    return out


def _set_linear(obj, path, index):
    anim = obj.animation_data
    if not anim or not anim.action:
        return
    for fc in _fcurves(anim.action):
        if fc.data_path == path and fc.array_index == index:
            for kp in fc.keyframe_points:
                kp.interpolation = "LINEAR"


def spin(objs, axis=None, turns=None, rpm=None, reverse=False):
    """Turn each object round its own axle for the whole loop, as a rolling wheel does. axis is the
    axle direction in world space, e.g. (1, 0, 0) for a wheel on a side-to-side axle; left out, it
    is the object's thinnest side. Left without turns or rpm, a wheel rolls at the travel speed for
    its own radius (measured from its mesh), in whole turns per loop; turns sets the whole turns
    per loop and rpm the revolutions per minute instead, for what does not roll (a fan, a
    propeller). Each copy rolls the way the vehicle travels, even one the assembler turned round;
    reverse flips it. Returns each object's direction by name (1 forward, -1 backward); the turns
    and each wheel's speed error are in the report once the loop length is chosen."""
    out = {}
    for obj in _flat(objs):
        gear = _gear(obj)
        a = Vector(axis).normalized() if axis is not None else (_gear_axis(obj) if gear else _thinnest_axis(obj))
        centre, r = _hub(obj, a)
        if turns is None and rpm is None and _S["speed"] <= 0:
            raise ValueError("call travel(speed_kmh=...) first, or give turns or rpm")
        roll = _S["forward"].dot(a.cross(UP))
        sign = -1 if roll < -1e-6 and not gear else 1  # a gear turns about its own axis as it is
        if reverse:
            sign = -sign
        _S["spins"].append({"obj": obj, "axis": a, "centre": centre, "r": r, "sign": sign,
                            "turns": turns, "rpm": rpm})
        out[obj.name] = sign
    return out


def bounce(objs, amplitude_m=0.004, cycles=2, axis="Z"):
    """Move the objects along an axis by a small sine wave, cycles times per loop (a whole number):
    a body riding on its springs. Give every part that rides on the springs together (the boiler
    and everything fixed to it, the cab, the tender body and its load), never wheels, frames or the
    track, or parts separate. They are back where the assembler put them at the start and the end,
    so the checks and the renders see the assembled pose. Returns how many objects bounce."""
    items = _flat(objs)
    _S["bounces"].append({"objs": items, "amp": float(amplitude_m), "cycles": max(1, int(round(cycles))),
                          "i": "XYZ".index(str(axis).upper())})
    return len(items)


def _gear(obj):
    raw = obj.get(GEAR_KEY) if obj is not None else None
    try:
        return json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _gear_axis(obj):
    """A kit part's own axis in the world: its local Z."""
    bpy.context.view_layer.update()
    return (obj.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()


def _perp(v, axis):
    return v - axis * v.dot(axis)


def mesh_drive(driver, driven, ratio=None, same_direction=None):
    """Turn `driven` from `driver` as meshing gears do: ratio is driven's turns per turn of the
    driver (left out: the driver's teeth over the driven's, read from the kit parts). Meshing
    external gears turn opposite ways, a gear inside a ring gear and its ring the same way, bevel
    gears as their cones meet: all worked out from where the parts stand; same_direction=True or
    False says it outright (True for a belt, or a gear and the shaft it is fixed to, with ratio 1).
    A rack meshing with the driver slides along its length instead. Spin the first driver with
    spin(..., rpm=...); the loop length is then chosen so every part ends where it began. Returns
    the ratio used."""
    gd, ge = _gear(driver) or {}, _gear(driven) or {}
    if ratio is None:
        if ge.get("type") == "rack":
            ratio = 1.0
        elif gd.get("teeth") and ge.get("teeth"):
            ratio = gd["teeth"] / ge["teeth"]
        else:
            raise ValueError(f"give the ratio: {driven.name} or {driver.name} is not a kit gear with a tooth count")
    _S["drives"].append({"driver": driver, "driven": driven, "ratio": float(ratio), "same": same_direction})
    return float(ratio)


def crank_slider(crank, rod, piston, pin, length, followers=()):
    """An engine's slider-crank: as `crank` turns (spin it with spin(..., rpm=...)), the rod's big end
    rides its crankpin (pin: the pin's centre in the crank's own frame, metres) and its small end
    stays on the piston's gudgeon pin, `length` away; the piston slides up and down its cylinder's
    axis (the line it stands on, through the crank's axis), worked out exactly every frame. Followers
    (the piston's rings) move with the piston. Place the rod and piston at the crank's first pose
    before calling. Returns the stroke in metres."""
    _S.setdefault("sliders", []).append({"crank": crank, "rod": rod, "piston": piston, "pin": Vector(pin),
                                         "length": float(length), "followers": list(followers)})
    return 2 * Vector(pin).xy.length


def _write_sliders(last):
    """Key every frame of each slider-crank from where its crank stands at that frame."""
    scene = bpy.context.scene
    for s in _S.get("sliders") or []:
        crank, rod, piston = s["crank"], s["rod"], s["piston"]
        scene.frame_set(1)
        bpy.context.view_layer.update()
        up = (piston.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
        rest = piston.matrix_world.translation.copy()
        foot = rest  # any point of the line the piston rides along
        rot_p = piston.matrix_world.to_3x3()
        offsets = [(f, f.matrix_world.translation - rest) for f in s["followers"]]
        rod.rotation_mode = "XYZ"
        prev = None
        lo = hi = None
        for frame in range(1, last + 2):
            scene.frame_set(frame)
            p = crank.matrix_world @ s["pin"]
            a = (crank.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
            d = p - foot
            along = d.dot(up)
            off = d - up * along
            h = along + math.sqrt(max(s["length"] ** 2 - off.length_squared, 0.0))
            q = foot + up * h
            y = (q - p).normalized()
            x = y.cross(a).normalized()
            m3 = Matrix((x, y, x.cross(y))).transposed()
            e = m3.to_euler("XYZ", prev) if prev is not None else m3.to_euler("XYZ")
            prev = e
            rod.location, rod.rotation_euler = p, e
            rod.keyframe_insert("location", frame=frame)
            rod.keyframe_insert("rotation_euler", frame=frame)
            piston.matrix_world = Matrix.Translation(q) @ rot_p.to_4x4()
            piston.keyframe_insert("location", frame=frame)
            for f, o in offsets:
                f.location = q + o
                f.keyframe_insert("location", frame=frame)
            lo, hi = (h, h) if lo is None else (min(lo, h), max(hi, h))
        for obj in [rod, piston] + list(s["followers"]):
            for i in range(3):
                _set_linear(obj, "location", i)
                _set_linear(obj, "rotation_euler", i)
        _S["animated"][piston.name] = {"slide_m": round(hi - lo, 5)}
        _S["animated"][rod.name] = {"crank_slider": crank.name}
    scene.frame_set(1)


def _own_loop(frames, info):
    """A motion that sets its own loop (a screw, a squeeze, a planetary set): the loop is fixed now."""
    frames = int(max(MIN_LOOP_FRAMES, min(MAX_LOOP_FRAMES, frames)))
    _S["seconds"] = frames / _S["fps"]
    _S["fixed"] = True
    _apply_loop()
    info.update(frames=frames, seconds=round(frames / _S["fps"], 4), fps=_S["fps"])
    _S["mechanism"] = info
    return frames


def _key_matrix(obj, matrix, frame, prev):
    """Key an object's location and rotation at a frame to a world matrix (no parent); returns the
    Euler angles, kept continuous with the frame before."""
    obj.rotation_mode = "XYZ"
    loc, rot, _ = matrix.decompose()
    e = rot.to_euler("XYZ", prev) if prev is not None else rot.to_euler("XYZ")
    obj.location, obj.rotation_euler = loc, e
    obj.keyframe_insert("location", frame=frame)
    obj.keyframe_insert("rotation_euler", frame=frame)
    return e


def _linear(obj):
    for i in range(3):
        _set_linear(obj, "location", i)
        _set_linear(obj, "rotation_euler", i)
        _set_linear(obj, "scale", i)


def screw(nut, pitch, travel, rpm):
    """A nut screwing off its bolt by `travel` metres and back on, turning one pitch's worth for each
    pitch it moves (right-handed: it backs off down its own -Z), at `rpm` at the fastest."""
    turns = travel / pitch
    rps = float(rpm) / 60.0
    frames = round(math.pi * turns / rps * _S["fps"])  # turns (1 - cos)/2: its peak speed is pi turns per loop
    _S.setdefault("screws", []).append({"obj": nut, "pitch": float(pitch), "travel": float(travel)})
    return _own_loop(frames, {"mode": "screw", "driver": nut.name, "turns_each_way": round(turns, 4),
                              "travel_m": round(travel, 6)})


def _write_screws(last):
    for s in _S.get("screws") or []:
        obj = s["obj"]
        m0 = obj.matrix_world.copy()
        axis = (m0.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
        prev = None
        for f in range(1, last + 2):
            t = (f - 1) / last
            down = s["travel"] * (1 - math.cos(2 * math.pi * t)) / 2
            turn = -2 * math.pi * down / s["pitch"]  # its thread stays in the bolt's
            m = Matrix.Translation(-axis * down) @ Matrix.Translation(m0.translation) @ \
                Matrix.Rotation(turn, 4, axis) @ Matrix.Translation(-m0.translation) @ m0
            prev = _key_matrix(obj, m, f, prev)
        _linear(obj)
        _S["animated"][obj.name] = {"screw_m": round(s["travel"], 6)}


def squash(spring, share, rpm, stretch=False):
    """A spring squeezed by `share` of its length (pulled out by it when stretch) and let go, `rpm`
    times a minute, its foot (the origin) still."""
    frames = round(60.0 / float(rpm) * _S["fps"])
    _S.setdefault("squashes", []).append({"obj": spring, "share": float(share) * (-1 if stretch else 1)})
    return _own_loop(frames, {"mode": "squeeze", "driver": spring.name, "share": float(share)})


def _write_squashes(last):
    for s in _S.get("squashes") or []:
        obj = s["obj"]
        base = obj.scale.copy()
        for f in range(1, last + 2):
            t = (f - 1) / last
            obj.scale = Vector((base.x, base.y, base.z * (1 - s["share"] * (1 - math.cos(2 * math.pi * t)) / 2)))
            obj.keyframe_insert("scale", frame=f)
        obj.scale = base
        _linear(obj)
        _S["animated"][obj.name] = {"squeeze": abs(s["share"])}


def _turned(obj, m0, axis):
    """How far an object has turned about `axis` (world) since the matrix m0, radians in (-pi, pi]."""
    ref = m0.to_3x3() @ Vector((1.0, 0.0, 0.0))
    now = obj.matrix_world.to_3x3() @ Vector((1.0, 0.0, 0.0))
    ref, now = (ref - axis * ref.dot(axis)).normalized(), (now - axis * now.dot(axis)).normalized()
    return math.atan2(axis.dot(ref.cross(now)), ref.dot(now))


def _unwrapped_turns(obj, last):
    """Each frame's total turn of a part about its own axis since frame 1 (radians, counterclockwise
    about its local Z positive), unwrapped."""
    scene = bpy.context.scene
    scene.frame_set(1)
    m0 = obj.matrix_world.copy()
    axis = (m0.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
    out, total, prev = [], 0.0, 0.0
    for f in range(1, last + 2):
        scene.frame_set(f)
        a = _turned(obj, m0, axis)
        d = (a - prev + math.pi) % (2 * math.pi) - math.pi
        total += d
        prev = a
        out.append(total)
    scene.frame_set(1)
    return out


def chain_drive(chain, sprocket, teeth):
    """A roller chain's links running round with `sprocket` (its first sprocket, `teeth` teeth): one
    pitch for each tooth the sprocket turns. The chain's shape keys (kits.chain) are blended frame by
    frame."""
    _S.setdefault("chains", []).append({"chain": chain, "sprocket": sprocket, "teeth": int(teeth)})


def _write_chains(last):
    for c in _S.get("chains") or []:
        obj = c["chain"]
        keys = obj.data.shape_keys
        if keys is None:
            continue
        names = [k.name for k in keys.key_blocks][1:]  # phase1..phaseN
        n = len(names)
        turns = _unwrapped_turns(c["sprocket"], last)
        for f in range(1, last + 2):
            phase = (-turns[f - 1] / (2 * math.pi) * c["teeth"]) % 1.0  # it runs on as its sprocket turns clockwise
            x = phase * n
            j, t = int(math.floor(x)), x - math.floor(x)
            weights = [0.0] * n
            if j == 0:
                weights[0] = t
            else:
                weights[j - 1] = 1 - t
                if j < n:
                    weights[j] = t
            for name, w in zip(names, weights):
                block = keys.key_blocks[name]
                block.value = w
                block.keyframe_insert("value", frame=f)
        if keys.animation_data and keys.animation_data.action:
            for fc in _fcurves(keys.animation_data.action):
                for kp in fc.keyframe_points:
                    kp.interpolation = "CONSTANT"
        _S["animated"][obj.name] = {"chain_runs_with": c["sprocket"].name}


def cam_drive(cam, follower, table, spring=None, spring_length=None):
    """A follower (or valve) lifted by its cam: `table` is the radius from the cam's axis to the
    follower's contact at even turns of the cam (counterclockwise about its local Z from frame 1); the
    follower moves along its own local Z (away from the cam) by the radius less the first. A spring
    on a valve is squeezed by as much (its foot, the origin, still)."""
    _S.setdefault("cams", []).append({"cam": cam, "obj": follower, "table": list(table), "spring": spring,
                                      "spring_length": spring_length})


def _write_cams(last):
    for c in _S.get("cams") or []:
        obj, table = c["obj"], c["table"]
        n = len(table)
        turns = _unwrapped_turns(c["cam"], last)
        rest = obj.matrix_world.translation.copy()
        d = (obj.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
        spring = c["spring"]
        sbase = spring.scale.copy() if spring is not None else None
        top = 0.0
        for f in range(1, last + 2):
            x = (turns[f - 1] / (2 * math.pi)) % 1.0 * n
            i = int(math.floor(x))
            r = table[i % n] + (table[(i + 1) % n] - table[i % n]) * (x - i)
            lift = r - table[0]
            top = max(top, lift)
            obj.location = rest + d * lift
            obj.keyframe_insert("location", frame=f)
            if spring is not None:
                spring.scale = Vector((sbase.x, sbase.y, sbase.z * (1 - lift / c["spring_length"])))
                spring.keyframe_insert("scale", frame=f)
        obj.location = rest
        _linear(obj)
        if spring is not None:
            spring.scale = sbase
            _linear(spring)
        _S["animated"][obj.name] = {"lift_m": round(top, 6)}


def planetary(sun, ring, carrier, planets, rates, rpm, reverse=False, turns=1.0):
    """A planetary set: each member turning at `rates` (turns per turn of the input, from Willis's
    equation; "planet" each planet's own turn in the world) with the input at `rpm`, the planets
    carried round by the carrier. The loop is `turns` turns of the input (every member back where it
    began)."""
    rps = float(rpm) / 60.0
    frames = round(float(turns) / rps * _S["fps"])
    _S.setdefault("planetaries", []).append({"sun": sun, "ring": ring, "carrier": carrier, "planets": list(planets),
                                             "rates": dict(rates), "sign": -1.0 if reverse else 1.0,
                                             "turns": float(turns)})
    return _own_loop(frames, {"mode": "planetary", "driver": sun.name, "input_turns": float(turns),
                              "rates": {k: round(v, 6) for k, v in rates.items()}})


def _write_planetaries(last):
    for s in _S.get("planetaries") or []:
        c0 = s["carrier"].matrix_world.copy()
        axis = (c0.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
        centre = c0.translation.copy()
        members = [(s["sun"], s["rates"]["sun"], False), (s["ring"], s["rates"]["ring"], False),
                   (s["carrier"], s["rates"]["carrier"], False)] + [(p, s["rates"]["planet"], True) for p in s["planets"]]
        rest = {o.name: o.matrix_world.copy() for o, _, _ in members}
        prev = {}
        for f in range(1, last + 2):
            t = (f - 1) / last
            input_angle = s["sign"] * 2 * math.pi * s["turns"] * t
            carry = Matrix.Translation(centre) @ Matrix.Rotation(input_angle * s["rates"]["carrier"], 4, axis) @ \
                Matrix.Translation(-centre)
            for obj, rate, rides in members:
                m0 = rest[obj.name]
                spin = input_angle * rate
                own = Matrix.Translation(m0.translation) @ Matrix.Rotation(spin, 4, axis) @ \
                    Matrix.Translation(-m0.translation)
                if rides:  # carried round the sun's axis; its own turn in the world on top, about its own centre
                    moved = carry @ m0
                    own = Matrix.Translation(moved.translation) @ Matrix.Rotation(
                        spin - input_angle * s["rates"]["carrier"], 4, axis) @ Matrix.Translation(-moved.translation)
                    m = own @ moved
                else:
                    m = own @ m0
                prev[obj.name] = _key_matrix(obj, m, f, prev.get(obj.name))
        for obj, _, _ in members:
            _linear(obj)
            _S["animated"][obj.name] = {"planetary": True}


def _pitch_point(driver, driven, gd, ge):
    """Where two meshing gears' pitch circles touch."""
    a = _gear_axis(driver)
    cd, ce = driver.matrix_world.translation, driven.matrix_world.translation
    if ge.get("type") == "rack":  # straight across to the rack's pitch line, wherever along it its middle is
        up = (driven.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
        return cd - _perp(up, a).normalized() * gd["pitch_radius"]
    if ge.get("type") == "ring_gear":
        return cd + _perp(cd - ce, a).normalized() * gd["pitch_radius"]
    if gd.get("type") == "ring_gear":
        return ce + _perp(ce - cd, a).normalized() * ge["pitch_radius"]
    return cd + _perp(ce - cd, a).normalized() * gd["pitch_radius"]


def _drive_sign(d):
    """+1 when the driven part turns the same way about its own axis as the driver, -1 if not."""
    if d["same"] is not None:
        return 1 if d["same"] else -1
    gd, ge = _gear(d["driver"]), _gear(d["driven"])
    if not gd or not ge or not gd.get("pitch_radius"):
        return -1
    p = _pitch_point(d["driver"], d["driven"], gd, ge)
    va = _gear_axis(d["driver"]).cross(p - d["driver"].matrix_world.translation)
    vb = _gear_axis(d["driven"]).cross(p - d["driven"].matrix_world.translation)
    return 1 if va.dot(vb) > 0 else -1


def _rack_rate(d):
    """How far a rack slides (metres, along its own X) per radian the gear driving it turns."""
    gd = _gear(d["driver"])
    p = _pitch_point(d["driver"], d["driven"], gd, _gear(d["driven"]) or {"type": "rack"})
    v = _gear_axis(d["driver"]).cross(p - d["driver"].matrix_world.translation)
    along = (d["driven"].matrix_world.to_3x3() @ Vector((1.0, 0.0, 0.0))).normalized()
    return v.dot(along)


def _period(obj):
    g = _gear(obj)
    if g and g.get("period"):
        return Fraction(int(g["period"][0]), int(g["period"][1]))
    return Fraction(1)


def _lcm(a, b):
    return Fraction(a.numerator * b.numerator // math.gcd(a.numerator, b.numerator),
                    math.gcd(a.denominator, b.denominator))


def _resolve_drives():
    """Each driven part's turns per turn of the driver it hangs from; then the loop: spin mode (every
    part ends where it began, the shortest such stretch of at least half a second) or swing mode (a
    rack in the train: the driver turns back and forth so the rack stays in mesh)."""
    root = next((s for s in _S["spins"] if any(d["driver"] is s["obj"] for d in _S["drives"])
                 or any(sl["crank"] is s["obj"] for sl in _S.get("sliders") or [])
                 or any(c["cam"] is s["obj"] for c in _S.get("cams") or [])), None)
    if root is None:
        raise ValueError("spin the first driver with spin(obj, rpm=...) before mesh_drive")
    rates = {root["obj"].name: Fraction(1)}
    objs = {root["obj"].name: root["obj"]}
    racks = []
    pending = list(_S["drives"])
    while pending:
        progressed = False
        for d in list(pending):
            name = d["driver"].name
            if name not in rates:
                continue
            pending.remove(d)
            progressed = True
            if (_gear(d["driven"]) or {}).get("type") == "rack":
                racks.append((d, rates[name]))
                continue
            step = Fraction(d["ratio"]).limit_denominator(100000) * _drive_sign(d)
            rates[d["driven"].name] = rates[name] * step
            objs[d["driven"].name] = d["driven"]
        if not progressed:
            names = ", ".join(sorted({d["driver"].name for d in pending}))
            raise ValueError(f"mesh_drive from {names}, which nothing drives: drive it first")
    fps = _S["fps"]
    asked = root.get("rpm")
    rps = float(asked) / 60.0 if asked is not None else None
    if rps is None or rps <= 0:
        raise ValueError("give the driver its speed: spin(driver, rpm=...)")
    sign_root = root["sign"]
    entries = {s["obj"].name: s for s in _S["spins"]}
    for name, obj in objs.items():
        if name not in entries:
            a = _gear_axis(obj) if _gear(obj) else _thinnest_axis(obj)
            centre, r = _hub(obj, a)
            entries[name] = {"obj": obj, "axis": a, "centre": centre, "r": r, "sign": 1, "turns": None, "rpm": None}
            _S["spins"].append(entries[name])
    info = {"driver": root["obj"].name, "fps": fps, "parts": {}}
    if racks:
        _swing(root, rates, objs, racks, rps, sign_root, info)
        return
    turns = Fraction(0)
    for name, rate in rates.items():
        if rate:
            need = _period(objs[name]) / abs(rate)
            turns = need if turns == 0 else _lcm(turns, need)
    exact = float(turns) / rps * fps
    k = max(1, math.ceil(MIN_LOOP_FRAMES / max(exact, 1e-9) - 1e-9))
    frames = max(1, round(exact * k))
    note = None
    if frames > MAX_LOOP_FRAMES:
        note = (f"a seamless loop needs {frames} frames; it was cut to {MAX_LOOP_FRAMES}, so the end does not "
                "join the start exactly")
        k, frames = 1, MAX_LOOP_FRAMES
        turns = Fraction(rps * MAX_LOOP_FRAMES / fps).limit_denominator(10000)
    loop_turns = turns * k
    _S["seconds"] = frames / fps
    _S["fixed"] = True
    _apply_loop()
    for name, rate in rates.items():
        s = entries[name]
        s["rpm"] = None
        if s is root:
            s["turns"] = float(loop_turns)
        else:
            s["sign"] = 1
            s["turns"] = float(loop_turns * rate) * sign_root
        info["parts"][name] = {"turns": round(float(loop_turns * rate) * sign_root, 6),
                               "period_turns": round(float(_period(objs[name])), 6)}
    info.update(mode="spin", frames=frames, seconds=round(frames / fps, 4), driver_turns=round(float(loop_turns), 6),
                driver_rpm=round(float(loop_turns) / (frames / fps) * 60, 4), driver_rpm_asked=float(asked))
    if note:
        info["note"] = note
    _S["mechanism"] = info


def _swing(root, rates, objs, racks, rps, sign_root, info):
    """A rack cannot slide one way for ever: the driver swings back and forth, A sin(2 pi t), as far
    as keeps every rack in mesh (two teeth from each end), at the driver's speed at the middle."""
    amp = 3 * math.pi
    for d, rate in racks:
        g = _gear(d["driven"])
        reach = max(g["pitch"], g["length"] / 2 - 2 * g["pitch"])
        per_root = abs(_rack_rate(d) * float(rate))
        if per_root > 0:
            amp = min(amp, reach / per_root)
    # amp sin(2 pi t / T) peaks at amp * 2 pi / T rad/s: the driver's own speed, 2 pi rps, when T = amp / rps
    frames = max(SWING_MIN_FRAMES, round(amp / rps * _S["fps"]))
    _S["seconds"] = frames / _S["fps"]
    _S["fixed"] = True
    _apply_loop()
    swings = []
    for name, rate in rates.items():
        obj = objs[name]
        entry = next(s for s in _S["spins"] if s["obj"] is obj)
        _S["spins"].remove(entry)
        swings.append({"obj": obj, "axis": entry["axis"], "centre": entry["centre"], "r": entry["r"],
                       "amp": amp * float(rate) * sign_root})
        info["parts"][name] = {"swing_deg": round(math.degrees(amp * float(rate)), 3)}
    slides = []
    for d, rate in racks:
        along = (d["driven"].matrix_world.to_3x3() @ Vector((1.0, 0.0, 0.0))).normalized()
        dist = _rack_rate(d) * float(rate) * amp * sign_root
        slides.append({"obj": d["driven"], "dir": along, "amp": dist})
        info["parts"][d["driven"].name] = {"slide_m": round(abs(dist), 5)}
    _S["swings"], _S["slides"] = swings, slides
    info.update(mode="swing", frames=frames, seconds=round(frames / _S["fps"], 4),
                driver_swing_deg=round(math.degrees(amp), 3), driver_rpm_peak=round(rps * 60, 4))
    _S["mechanism"] = info


def _write_swings(last):
    """Key every frame of a swing: each part turned by amp sin(2 pi t) about its own axis, each rack
    slid by its amp sin(2 pi t)."""
    for s in _S.get("swings") or []:
        obj, a = s["obj"], s["axis"]
        rot = _rotation_matrix(obj)
        local = rot.inverted() @ a
        i = max(range(3), key=lambda k: abs(local[k]))
        mode = ("XYZ", "YZX", "ZXY")[i]
        e = rot.to_euler(mode)
        obj.rotation_mode = mode
        sign = 1.0 if local[i] > 0 else -1.0
        for f in range(1, last + 2):
            t = (f - 1) / last
            e2 = e.copy()
            e2[i] += sign * s["amp"] * math.sin(2 * math.pi * t)
            obj.rotation_euler = e2
            obj.keyframe_insert("rotation_euler", index=i, frame=f)
        obj.rotation_euler = e
        _S["animated"][obj.name] = {"swing_deg": round(math.degrees(abs(s["amp"])), 3)}
    for s in _S.get("slides") or []:
        obj = s["obj"]
        base = obj.location.copy()
        for f in range(1, last + 2):
            t = (f - 1) / last
            obj.location = base + s["dir"] * (s["amp"] * math.sin(2 * math.pi * t))
            obj.keyframe_insert("location", frame=f)
        obj.location = base
        _S["animated"][obj.name] = {"slide_m": round(abs(s["amp"]), 5)}


def _choose_loop():
    """The loop length, in whole frames between 2 and 4 seconds, whose whole turns bring the wheels
    that roll at the travel speed closest to it (the worst wheel decides; the shorter loop wins a tie)."""
    rolling = [s for s in _S["spins"] if s["turns"] is None and s["rpm"] is None]
    if _S["fixed"] or not rolling or _S["speed"] <= 0:
        return
    fps, best = _S["fps"], None
    for frames in range(round(LOOP_RANGE_S[0] * fps), round(LOOP_RANGE_S[1] * fps) + 1):
        t = frames / fps
        worst = 0.0
        for s in rolling:
            exact = _S["speed"] * t / (2 * math.pi * s["r"])
            worst = max(worst, abs(max(1, round(exact)) / exact - 1))
        if best is None or worst < best[0] - 1e-9:
            best = (worst, frames)
    _S["seconds"] = best[1] / fps
    _apply_loop()


def _write_spin(s, last):
    obj, a, centre, r, sign = s["obj"], s["axis"], s["centre"], s["r"], s["sign"]
    error = None
    if s["rpm"] is not None:
        n = max(1, round(float(s["rpm"]) / 60.0 * _S["seconds"]))
    elif s["turns"] is not None:
        n = float(s["turns"])
    else:
        exact = _S["speed"] * _S["seconds"] / (2 * math.pi * r)
        n = max(1, round(exact))
        error = round((n / exact - 1) * 100, 1)
    total = sign * 2 * math.pi * n
    rot = _rotation_matrix(obj)
    local = rot.inverted() @ a
    i = max(range(3), key=lambda k: abs(local[k]))
    off = centre - obj.matrix_world.translation
    centred = (off - a * off.dot(a)).length <= 0.01 * r  # the origin is on the axle
    if abs(local[i]) > 0.999 and centred:
        # the axle is one of the object's own axes: make that axis the innermost Euler axis
        # and key it alone, so the rotation interpolates as a plain turn about the axle
        mode = ("XYZ", "YZX", "ZXY")[i]
        e = rot.to_euler(mode)
        obj.rotation_mode = mode
        obj.rotation_euler = e
        obj.keyframe_insert("rotation_euler", index=i, frame=1)
        e2 = e.copy()
        e2[i] += (1.0 if local[i] > 0 else -1.0) * total
        obj.rotation_euler = e2
        obj.keyframe_insert("rotation_euler", index=i, frame=last + 1)
        _set_linear(obj, "rotation_euler", i)
        obj.rotation_euler = e
    else:
        # an axle at an odd angle, or an origin off the axle (a wheel anchored at its bottom):
        # turn the object through a pivot empty at the wheel's centre whose X is the axle
        pivot = bpy.data.objects.new(obj.name + "_axle", None)
        pivot.empty_display_size = 0.05
        bpy.context.scene.collection.objects.link(pivot)
        pivot[MOTION_KEY] = "pivot"
        pivot.matrix_world = Matrix.Translation(centre) @ a.to_track_quat("X", "Z").to_matrix().to_4x4()
        bpy.context.view_layer.update()
        obj.parent = pivot
        obj.matrix_parent_inverse = pivot.matrix_world.inverted()
        pivot.rotation_mode = "XYZ"
        pivot.rotation_euler = pivot.matrix_world.to_euler("XYZ")
        pivot.keyframe_insert("rotation_euler", index=0, frame=1)
        e = pivot.rotation_euler.copy()
        pivot.rotation_euler[0] += total
        pivot.keyframe_insert("rotation_euler", index=0, frame=last + 1)
        _set_linear(pivot, "rotation_euler", 0)
        pivot.rotation_euler = e
    obj[SPIN_KEY] = float(r)
    entry = {"spin_turns": n * sign, "radius_m": round(r, 4)}
    if error is not None:
        entry["speed_error_pct"] = error
    _S["animated"][obj.name] = entry


def _write_bounce(b, last):
    i, amp, cycles = b["i"], b["amp"], b["cycles"]
    for obj in b["objs"]:
        base = obj.location[i]
        for f in range(1, last + 2):
            t = (f - 1) / last
            obj.location[i] = base + amp * math.sin(2 * math.pi * cycles * t)
            obj.keyframe_insert("location", index=i, frame=f)
        obj.location[i] = base
        _S["animated"].setdefault(obj.name, {})["bounce_m"] = amp


def _finalize():
    """Choose the loop length and write every key collected by spin and bounce."""
    if _S["written"]:
        return
    _S["written"] = True
    bpy.context.scene.frame_set(1)
    if _S["drives"] or _S.get("sliders") or _S.get("cams"):
        _resolve_drives()
    _choose_loop()
    last = _last_frame()
    for s in _S["spins"]:
        _write_spin(s, last)
    _write_swings(last)
    _write_sliders(last)
    _write_chains(last)
    _write_cams(last)
    _write_screws(last)
    _write_squashes(last)
    _write_planetaries(last)
    if _S.get("screws") or _S.get("squashes") or _S.get("planetaries"):
        _motion_empty()
    for b in _S["bounces"]:
        _write_bounce(b, last)
    if _S["spins"] or _S["bounces"] or _S["markers"] or _S.get("swings"):
        _motion_empty()


def top(obj, offset=(0.0, 0.0, 0.0)):
    """The centre of the top of an object's world bounding box, plus an offset in metres: where a
    chimney's smoke or a safety valve's steam starts."""
    lo, hi = helpers.bounds(obj)
    return Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, hi.z)) + Vector(offset)


def marker(kind, location, size_m=None, rate=None, name=None):
    """A point where the page draws rising smoke ("smoke", dark grey) or steam ("steam", white): an
    empty the exporters carry with the model. size_m is a puff's size at birth (default a twelfth of
    the model's height); rate is puffs per second (default 12). Returns the empty."""
    kind = str(kind).lower()
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    objs = _model()
    height = helpers.size(objs).z if objs else 1.0
    size_m = float(size_m or height / 12.0)
    obj = bpy.data.objects.new(name or f"_mg_fx_{kind}_{len(_S['markers']) + 1}", None)
    obj.empty_display_type = "SPHERE"
    obj.empty_display_size = size_m
    bpy.context.scene.collection.objects.link(obj)
    obj.location = Vector(location)
    obj[FX_KEY] = kind
    obj[FX_SIZE_KEY] = size_m
    obj[FX_RATE_KEY] = float(rate or 12.0)
    _S["markers"].append({"kind": kind, "name": obj.name, "location": [round(v, 4) for v in obj.location],
                          "size_m": round(size_m, 4)})
    _motion_empty()
    return obj


def motion_objects():
    """The empties the exporters must carry with the model: markers, pivots and the motion empty."""
    return [o for o in bpy.context.scene.objects if o.get(FX_KEY) or o.get(MOTION_KEY)]


def report():
    """Write the collected motion, then say what the animator's script did, for the job's result; it
    also puts the scene back on frame 1, so the checks, the exports and the renders see the
    assembled pose."""
    _finalize()
    bpy.context.scene.frame_set(1)
    out = {"animated": dict(_S["animated"]), "markers": list(_S["markers"]), "seconds": _S["seconds"],
           "fps": _S["fps"], "frames": _last_frame(), "speed_mps": _S["speed"],
           "forward": [round(v, 3) for v in _S["forward"]]}
    if _S.get("mechanism"):
        out["mechanism"] = _S["mechanism"]
        bpy.context.scene["mg_mechanism_loop"] = json.dumps(_S["mechanism"])
    return out
