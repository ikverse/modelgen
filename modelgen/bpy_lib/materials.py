"""ModelGen's procedural material recipes. Runs inside Blender only.

Every recipe returns a new material built from shader nodes; there are no texture images.
Colours are ordinary (sRGB) RGB tuples from 0 to 1, as a colour picker shows them; the recipes
convert them for Blender. Pattern scales are in features per metre, so a small object needs a
larger number for the same look. Put a material on a part with `assign`, or on some faces only
with `assign_where`.
"""

import math

import bpy

RING_DENSITY = 20.0 / (2 * math.pi)  # Blender's wave texture makes this many bands per unit

ALIASES = {
    "base_color": ["Base Color"],
    "metallic": ["Metallic"],
    "roughness": ["Roughness"],
    "ior": ["IOR"],
    "transmission": ["Transmission Weight", "Transmission"],
    "coat": ["Coat Weight", "Clearcoat"],
    "coat_roughness": ["Coat Roughness", "Clearcoat Roughness"],
    "sheen": ["Sheen Weight", "Sheen"],
    "sheen_roughness": ["Sheen Roughness"],
    "specular": ["Specular IOR Level", "Specular"],
    "subsurface": ["Subsurface Weight", "Subsurface"],
    "anisotropic": ["Anisotropic"],
    "normal": ["Normal"],
}


def _rgba(color):
    c = tuple(color)
    return c if len(c) == 4 else (*c[:3], 1.0)


def _linear(color):
    """An ordinary sRGB colour converted to the linear values Blender's shaders use."""
    return tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in tuple(color)[:3])


def _socket(bsdf, key):
    for name in ALIASES.get(key, [key]):
        if name in bsdf.inputs:
            return bsdf.inputs[name]
    return None


def _set(bsdf, key, value):
    s = _socket(bsdf, key)
    if s is not None:
        s.default_value = _rgba(value) if key == "base_color" else value
    return s


def _new(name, color, metallic=0.0, roughness=0.5):
    """A material with a Principled BSDF, its viewport colour and the plain values the GLB export
    and the browser viewer use (node patterns do not survive export)."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    _set(bsdf, "base_color", color)
    _set(bsdf, "metallic", metallic)
    _set(bsdf, "roughness", roughness)
    mat.diffuse_color = _rgba(color)
    mat.metallic = metallic
    mat.roughness = roughness
    mat["mg_base_color"] = list(_rgba(color))
    mat["mg_metallic"] = float(metallic)
    mat["mg_roughness"] = float(roughness)
    return mat, nt, bsdf


def _link(nt, out_socket, in_socket):
    if out_socket is not None and in_socket is not None:
        nt.links.new(out_socket, in_socket)


def _coords(nt, scale=(1.0, 1.0, 1.0)):
    """Object-space coordinates scaled per axis (scale is features per metre)."""
    tc = nt.nodes.new("ShaderNodeTexCoord")
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = scale
    _link(nt, tc.outputs["Object"], mp.inputs["Vector"])
    return mp.outputs["Vector"]


def _ramp(nt, fac_socket, color_a, color_b, pos_a=0.0, pos_b=1.0):
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = pos_a
    ramp.color_ramp.elements[0].color = _rgba(color_a)
    ramp.color_ramp.elements[1].position = pos_b
    ramp.color_ramp.elements[1].color = _rgba(color_b)
    _link(nt, fac_socket, ramp.inputs["Fac"])
    return ramp.outputs["Color"]


def _map_range(nt, value_socket, to_min, to_max, from_min=0.0, from_max=1.0):
    node = nt.nodes.new("ShaderNodeMapRange")
    node.inputs["From Min"].default_value = from_min
    node.inputs["From Max"].default_value = from_max
    node.inputs["To Min"].default_value = to_min
    node.inputs["To Max"].default_value = to_max
    _link(nt, value_socket, node.inputs["Value"])
    return node.outputs["Result"]


def _math(nt, op, a, b):
    node = nt.nodes.new("ShaderNodeMath")
    node.operation = op
    for i, v in enumerate((a, b)):
        if isinstance(v, (int, float)):
            node.inputs[i].default_value = v
        else:
            _link(nt, v, node.inputs[i])
    return node.outputs[0]


def _mix_color(nt, fac_socket, color_a, color_b):
    """Mix two colours (sockets or tuples) by a factor socket."""
    node = nt.nodes.new("ShaderNodeMix")
    node.data_type = "RGBA"
    colors = [s for s in node.inputs if s.type == "RGBA"]
    for sock, value in zip(colors, (color_a, color_b)):
        if isinstance(value, tuple):
            sock.default_value = _rgba(value)
        else:
            _link(nt, value, sock)
    _link(nt, fac_socket, node.inputs[0])
    return next(s for s in node.outputs if s.type == "RGBA")


def _bump(nt, bsdf, height_socket, strength=0.2, distance=0.001):
    node = nt.nodes.new("ShaderNodeBump")
    node.inputs["Strength"].default_value = strength
    node.inputs["Distance"].default_value = distance
    _link(nt, height_socket, node.inputs["Height"])
    _link(nt, node.outputs["Normal"], _socket(bsdf, "normal"))


def _noise(nt, vector, scale=1.0, detail=4.0, roughness=0.5):
    node = nt.nodes.new("ShaderNodeTexNoise")
    node.inputs["Scale"].default_value = scale
    node.inputs["Detail"].default_value = detail
    if "Roughness" in node.inputs:
        node.inputs["Roughness"].default_value = roughness
    _link(nt, vector, node.inputs["Vector"])
    return node


def _scaled(color, k):
    return tuple(max(0.0, min(1.0, c * k)) for c in color[:3])


# --- recipes ------------------------------------------------------------------------

def wood(color=(0.42, 0.26, 0.14), color_dark=None, grain_scale=8.0, ring_distortion=2.0,
         roughness=0.5, grain_axis="X", name="wood"):
    """Wood with growth rings and fine grain. grain_axis is the direction the grain runs ("X", "Y",
    "Z"): make one wood material per direction, e.g. along Z for legs and posts, along X for boards
    and rails. grain_scale is rings per metre (about 8 for furniture, 40 for a small spoon)."""
    color = _linear(color)
    dark = _linear(color_dark) if color_dark else _scaled(color, 0.45)
    mat, nt, bsdf = _new(name, color, 0.0, roughness)
    stretch = {"X": (0.06, 1.0, 1.0), "Y": (1.0, 0.06, 1.0), "Z": (1.0, 1.0, 0.06)}[grain_axis.upper()]
    vec = _coords(nt, tuple(s * grain_scale / RING_DENSITY for s in stretch))
    warp = _noise(nt, vec, 1.5, 4.0, 0.6)  # wobble the rings so they read as grain, not stripes
    wobble = nt.nodes.new("ShaderNodeVectorMath")
    wobble.operation = "SCALE"
    _link(nt, warp.outputs["Color"], wobble.inputs[0])
    wobble.inputs["Scale"].default_value = 0.35
    warped = nt.nodes.new("ShaderNodeVectorMath")
    warped.operation = "ADD"
    _link(nt, vec, warped.inputs[0])
    _link(nt, wobble.outputs[0], warped.inputs[1])
    wave = nt.nodes.new("ShaderNodeTexWave")
    wave.wave_type = "RINGS"
    wave.wave_profile = "SAW"
    wave.inputs["Scale"].default_value = 1.0
    wave.inputs["Distortion"].default_value = ring_distortion
    _link(nt, warped.outputs[0], wave.inputs["Vector"])
    fibres = _noise(nt, _coords(nt, tuple(s * grain_scale * 40 for s in stretch)), 1.0, 2.0)
    fac = _math(nt, "ADD", _math(nt, "MULTIPLY", wave.outputs["Fac"], 0.75),
                _math(nt, "MULTIPLY", fibres.outputs["Fac"], 0.25))
    _link(nt, _ramp(nt, fac, dark, color, 0.2, 0.8), _socket(bsdf, "base_color"))
    _link(nt, _map_range(nt, fac, roughness + 0.08, roughness - 0.08), _socket(bsdf, "roughness"))
    _bump(nt, bsdf, fac, 0.05, 0.0005)
    return mat


def brushed_metal(color=(0.78, 0.78, 0.79), roughness=0.3, brush_scale=400.0, direction="X",
                  name="brushed_metal"):
    """Metal with fine straight brush lines along `direction`; brush_scale is lines per metre."""
    color = _linear(color)
    mat, nt, bsdf = _new(name, color, 1.0, roughness)
    s = brush_scale
    stretch = {"X": (2.0, s, s), "Y": (s, 2.0, s), "Z": (s, s, 2.0)}[direction.upper()]
    streaks = _noise(nt, _coords(nt, stretch), 1.0, 6.0, 0.6)
    _link(nt, _map_range(nt, streaks.outputs["Fac"], max(0.02, roughness - 0.12), roughness + 0.12, 0.3, 0.7),
          _socket(bsdf, "roughness"))
    _set(bsdf, "anisotropic", 0.6)
    _bump(nt, bsdf, streaks.outputs["Fac"], 0.05, 0.0002)
    return mat


def polished_metal(color=(0.92, 0.92, 0.92), roughness=0.07, name="polished_metal"):
    """Mirror-like metal: chrome, polished steel (grey), brass or gold (warm colours), copper."""
    color = _linear(color)
    mat, nt, bsdf = _new(name, color, 1.0, roughness)
    smudge = _noise(nt, _coords(nt, (6.0, 6.0, 6.0)), 1.0, 3.0)
    _link(nt, _map_range(nt, smudge.outputs["Fac"], roughness * 0.7, roughness * 1.6, 0.35, 0.65),
          _socket(bsdf, "roughness"))
    return mat


def glass(color=(1.0, 1.0, 1.0), roughness=0.0, ior=1.45, name="glass"):
    """Clear or tinted glass (a light colour tints it); roughness above 0.1 makes frosted glass."""
    color = _linear(color)
    mat, nt, bsdf = _new(name, color, 0.0, roughness)
    _set(bsdf, "transmission", 1.0)
    _set(bsdf, "ior", ior)
    mat["mg_alpha"] = 0.35
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "DITHERED"
    elif hasattr(mat, "blend_method"):
        mat.blend_method = "HASHED"
    return mat


def ceramic(color=(0.92, 0.9, 0.86), roughness=0.3, glaze=0.8, speckle=0.0, name="ceramic"):
    """Glazed ceramic or porcelain. glaze is the glossy coat (0 for matte stoneware); speckle from
    0 to 1 adds the dark flecks of stoneware clay."""
    color = _linear(color)
    mat, nt, bsdf = _new(name, color, 0.0, roughness)
    _set(bsdf, "coat", glaze)
    _set(bsdf, "coat_roughness", 0.04)
    _set(bsdf, "subsurface", 0.02)
    if speckle > 0:
        flecks = _noise(nt, _coords(nt, (900.0, 900.0, 900.0)), 1.0, 1.0)
        mask = _map_range(nt, flecks.outputs["Fac"], 0.0, 1.0, 0.72 - 0.12 * speckle, 0.74 - 0.12 * speckle)
        _link(nt, _mix_color(nt, mask, color[:3], _scaled(color, 0.35)), _socket(bsdf, "base_color"))
    return mat


def leather(color=(0.32, 0.18, 0.1), roughness=0.55, grain_scale=350.0, name="leather"):
    """Leather with a pebbled grain; grain_scale is pebbles per metre."""
    color = _linear(color)
    mat, nt, bsdf = _new(name, color, 0.0, roughness)
    vor = nt.nodes.new("ShaderNodeTexVoronoi")
    vor.inputs["Scale"].default_value = grain_scale
    _link(nt, _coords(nt), vor.inputs["Vector"])
    tone = _noise(nt, _coords(nt, (8.0, 8.0, 8.0)), 1.0, 3.0)
    _link(nt, _ramp(nt, tone.outputs["Fac"], _scaled(color, 0.75), _scaled(color, 1.15), 0.3, 0.7),
          _socket(bsdf, "base_color"))
    _link(nt, _map_range(nt, vor.outputs["Distance"], roughness - 0.1, roughness + 0.1), _socket(bsdf, "roughness"))
    _bump(nt, bsdf, vor.outputs["Distance"], 0.25, 0.0004)
    return mat


def fabric(color=(0.35, 0.38, 0.45), weave_scale=500.0, roughness=0.85, sheen=0.6, name="fabric"):
    """Woven fabric (upholstery, canvas); weave_scale is threads per metre."""
    color = _linear(color)
    mat, nt, bsdf = _new(name, color, 0.0, roughness)
    _set(bsdf, "sheen", sheen)
    _set(bsdf, "specular", 0.2)
    warp = nt.nodes.new("ShaderNodeTexWave")
    weft = nt.nodes.new("ShaderNodeTexWave")
    for node, axis in ((warp, "X"), (weft, "Y")):
        node.wave_type = "BANDS"
        node.bands_direction = axis
        node.inputs["Scale"].default_value = weave_scale / RING_DENSITY
        _link(nt, _coords(nt), node.inputs["Vector"])
    weave = _math(nt, "MULTIPLY", warp.outputs["Fac"], weft.outputs["Fac"])
    _link(nt, _ramp(nt, weave, _scaled(color, 0.8), _scaled(color, 1.1)), _socket(bsdf, "base_color"))
    _bump(nt, bsdf, weave, 0.3, 0.0003)
    return mat


def painted(color=(0.12, 0.3, 0.5), roughness=0.4, wear=0.3, under_color=(0.6, 0.6, 0.62),
            under_metallic=1.0, name="painted"):
    """Painted surface with worn edges showing what is underneath (bare metal by default; give a
    wood colour and under_metallic=0 for painted wood). wear from 0 (new) to 1 (heavily worn)."""
    color, under_color = _linear(color), _linear(under_color)
    mat, nt, bsdf = _new(name, color, 0.0, roughness)
    if wear <= 0:
        return mat
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    edge = _map_range(nt, geo.outputs["Pointiness"], 0.0, 1.0, 0.52 - 0.04 * wear, 0.56 - 0.04 * wear)
    chips = _noise(nt, _coords(nt, (60.0, 60.0, 60.0)), 1.0, 5.0, 0.7)
    mask = _math(nt, "MULTIPLY", edge, _map_range(nt, chips.outputs["Fac"], 0.0, 1.0, 0.45, 0.55))
    mask = _math(nt, "MINIMUM", _math(nt, "MULTIPLY", mask, 1.0 + 2.0 * wear), 1.0)
    _link(nt, _mix_color(nt, mask, color[:3], under_color[:3]), _socket(bsdf, "base_color"))
    _link(nt, _math(nt, "MULTIPLY", mask, under_metallic), _socket(bsdf, "metallic"))
    _link(nt, _map_range(nt, mask, roughness, 0.35), _socket(bsdf, "roughness"))
    return mat


def plastic(color=(0.1, 0.1, 0.1), roughness=0.35, name="plastic"):
    """Moulded plastic with a faint surface texture."""
    color = _linear(color)
    mat, nt, bsdf = _new(name, color, 0.0, roughness)
    _set(bsdf, "specular", 0.5)
    tex = _noise(nt, _coords(nt, (800.0, 800.0, 800.0)), 1.0, 2.0)
    _bump(nt, bsdf, tex.outputs["Fac"], 0.03, 0.0002)
    return mat


def _facing_gradient(nt, bsdf, color, low=0.45, high=0.85):
    """Base colour lighter on faces turned up and darker on faces turned down: the soft gradient of
    a stylised render."""
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    _link(nt, geo.outputs["Normal"], sep.inputs["Vector"])
    up = _map_range(nt, sep.outputs["Z"], 0.0, 1.0, -1.0, 1.0)
    _link(nt, _ramp(nt, up, _scaled(color, low), _scaled(color, high), 0.15, 0.95), _socket(bsdf, "base_color"))


def stylised_paint(color=(0.12, 0.36, 0.78), roughness=0.5, gloss=0.0, name="stylised_paint"):
    """Clean toy-like car paint: a soft light-to-dark gradient by how each face is turned and a light
    clear coat, like a stylised game render. gloss is the clear coat from 0 to 1."""
    color = _linear(color)
    mat, nt, bsdf = _new(name, color, 0.0, roughness)
    _facing_gradient(nt, bsdf, color)
    _set(bsdf, "coat", gloss)
    _set(bsdf, "coat_roughness", 0.15)
    return mat


def car_paint(color=(0.6, 0.05, 0.05), roughness=0.35, metallic=0.3, flake=600.0, name="car_paint"):
    """Realistic glossy car paint: a metallic base with fine flake (flake per metre; 0 for none) under
    a smooth full clear coat."""
    color = _linear(color)
    mat, nt, bsdf = _new(name, color, metallic, roughness)
    _set(bsdf, "coat", 1.0)
    _set(bsdf, "coat_roughness", 0.03)
    if flake > 0:  # the bump is on the base layer only; the coat keeps its own smooth normal
        sparkle = _noise(nt, _coords(nt, (flake, flake, flake)), 1.0, 1.0)
        _bump(nt, bsdf, sparkle.outputs["Fac"], 0.08, 0.001)
    return mat


def stylised_glass(color=(0.84, 0.86, 0.89), roughness=0.18, name="stylised_glass"):
    """Opaque pale glass of a stylised render: it reads as glass by its colour and soft sheen, not by
    seeing through it."""
    color = _linear(color)
    mat, nt, bsdf = _new(name, color, 0.0, roughness)
    _facing_gradient(nt, bsdf, color, 0.8, 1.06)
    _set(bsdf, "coat", 0.6)
    _set(bsdf, "coat_roughness", 0.05)
    return mat


def lamp(color=(0.95, 0.95, 0.92), glow=0.0, roughness=0.15, name="lamp"):
    """A lamp lens: glossy, optionally glowing (glow is the emission strength, 0 for lamps off)."""
    lin = _linear(color)
    mat, nt, bsdf = _new(name, lin, 0.0, roughness)
    _set(bsdf, "coat", 0.8)
    if glow > 0:
        for key in ("Emission Color", "Emission"):
            if key in bsdf.inputs:
                bsdf.inputs[key].default_value = _rgba(lin)
                break
        if "Emission Strength" in bsdf.inputs:
            bsdf.inputs["Emission Strength"].default_value = glow
    return mat


def matte(color=(0.05, 0.05, 0.055), roughness=0.6, name="matte"):
    """A plain matte surface: trim, tyres, dark plastic in a stylised render."""
    color = _linear(color)
    mat, nt, bsdf = _new(name, color, 0.0, roughness)
    return mat


# --- buildings, grounds, rubber, windows and screens ----------------------------------

def _wall_coords(nt):
    """(u, v, 0) for a pattern laid on walls and slopes, in metres in the part's own frame: v is the
    height (Z) and u runs along the wall (Y on faces turned toward +X or -X, X on every other face),
    so courses stay level and wrap every side of a building."""
    tc = nt.nodes.new("ShaderNodeTexCoord")
    pos = nt.nodes.new("ShaderNodeSeparateXYZ")
    _link(nt, tc.outputs["Object"], pos.inputs["Vector"])
    nrm = nt.nodes.new("ShaderNodeSeparateXYZ")
    _link(nt, tc.outputs["Normal"], nrm.inputs["Vector"])
    side = _math(nt, "GREATER_THAN", _math(nt, "ABSOLUTE", nrm.outputs["X"], 0.0),
                 _math(nt, "ABSOLUTE", nrm.outputs["Y"], 0.0))
    u = _math(nt, "ADD", pos.outputs["X"],
              _math(nt, "MULTIPLY", side, _math(nt, "SUBTRACT", pos.outputs["Y"], pos.outputs["X"])))
    out = nt.nodes.new("ShaderNodeCombineXYZ")
    _link(nt, u, out.inputs["X"])
    _link(nt, pos.outputs["Z"], out.inputs["Y"])
    return out.outputs["Vector"]


def _courses(name, color, joint, unit_width, unit_height, joint_width, variation, roughness, bump):
    """Rows of units (bricks, tiles) with joints between them, each row shifted half a unit."""
    color, joint = _linear(color), _linear(joint)
    mat, nt, bsdf = _new(name, color, 0.0, roughness)
    tex = nt.nodes.new("ShaderNodeTexBrick")
    tex.offset = 0.5
    tex.offset_frequency = 2
    tex.inputs["Scale"].default_value = 1.0
    tex.inputs["Color1"].default_value = _rgba(_scaled(color, 1.0 + variation / 2))
    tex.inputs["Color2"].default_value = _rgba(_scaled(color, 1.0 - variation / 2))
    tex.inputs["Mortar"].default_value = _rgba(joint)
    tex.inputs["Mortar Size"].default_value = joint_width / 2  # the joint lies on both of two bricks' edges
    tex.inputs["Mortar Smooth"].default_value = 0.1
    tex.inputs["Bias"].default_value = 0.0
    tex.inputs["Brick Width"].default_value = unit_width + joint_width
    tex.inputs["Row Height"].default_value = unit_height + joint_width
    _link(nt, _wall_coords(nt), tex.inputs["Vector"])
    _link(nt, tex.outputs["Color"], _socket(bsdf, "base_color"))
    _bump(nt, bsdf, _math(nt, "SUBTRACT", 1.0, tex.outputs["Fac"]), bump, 0.004)
    return mat


def brick(color=(0.50, 0.22, 0.16), mortar=(0.66, 0.64, 0.60), brick_width=0.21, brick_height=0.065,
          mortar_width=0.012, variation=0.3, roughness=0.9, name="brick"):
    """Brick wall in running bond on every vertical face: sizes in metres (a standard brick is
    0.21 x 0.065 with a 0.012 joint), courses level around all sides. variation is how much single
    bricks differ in tone (0 to 1). Use dark mortar for old sooty walls, pale for new ones."""
    return _courses(name, color, mortar, brick_width, brick_height, mortar_width, variation, roughness, 0.6)


def roof_tiles(color=(0.42, 0.20, 0.14), tile_width=0.25, tile_height=0.18, variation=0.25, roughness=0.75,
               name="roof_tiles"):
    """Rows of roof tiles or slates on a sloping roof (clay red-brown by default; dark blue-grey
    (0.16, 0.17, 0.20) for slate), each row shifted half a tile, with a dark shadow line between."""
    return _courses(name, color, tuple(c * 0.35 for c in color[:3]), tile_width, tile_height, 0.012, variation,
                    roughness, 0.9)


def _mottled(nt, bsdf, color, scale, amount, fine_scale, bump, distance):
    """Soft patches of lighter and darker tone, and a fine grain in the surface."""
    patches = _noise(nt, _coords(nt, (scale, scale, scale)), 1.0, 4.0, 0.6)
    _link(nt, _ramp(nt, patches.outputs["Fac"], _scaled(color, 1.0 - amount), _scaled(color, 1.0 + amount), 0.3, 0.7),
          _socket(bsdf, "base_color"))
    grain = _noise(nt, _coords(nt, (fine_scale, fine_scale, fine_scale)), 1.0, 3.0, 0.6)
    _bump(nt, bsdf, grain.outputs["Fac"], bump, distance)


def concrete(color=(0.62, 0.61, 0.58), roughness=0.85, stains=0.1, name="concrete"):
    """Cast concrete, render or paving: an even grey with faint large stains (0 to 1) and a fine
    grain. Lighter (0.80, 0.78, 0.74) for precast floor bands, darker for asphalt."""
    color = _linear(color)
    mat, nt, bsdf = _new(name, color, 0.0, roughness)
    _mottled(nt, bsdf, color, 1.2, stains, 90.0, 0.15, 0.001)
    return mat


def stone(color=(0.52, 0.50, 0.46), roughness=0.8, mottle=0.25, scale=4.0, name="stone"):
    """Natural stone, rock or gravel: strong tonal patches (`scale` patches per metre) and a rough
    surface. Sandstone is (0.70, 0.60, 0.44), coal (0.03, 0.03, 0.03) with roughness 0.5."""
    color = _linear(color)
    mat, nt, bsdf = _new(name, color, 0.0, roughness)
    _mottled(nt, bsdf, color, scale, mottle, scale * 12, 0.5, 0.004)
    return mat


def grass(color=(0.22, 0.38, 0.15), roughness=0.95, patchiness=0.2, name="grass"):
    """A lawn or field seen from a distance: matte green with soft lighter and darker patches."""
    color = _linear(color)
    mat, nt, bsdf = _new(name, color, 0.0, roughness)
    _mottled(nt, bsdf, color, 0.8, patchiness, 60.0, 0.3, 0.01)
    return mat


def foliage(color=(0.17, 0.36, 0.13), roughness=0.7, variation=0.3, name="foliage"):
    """Tree crowns, hedges and bushes: leaf green with clumps of lighter and darker leaves and a
    soft sheen. Autumn leaves are (0.70, 0.38, 0.10)."""
    color = _linear(color)
    mat, nt, bsdf = _new(name, color, 0.0, roughness)
    _set(bsdf, "sheen", 0.3)
    _mottled(nt, bsdf, color, 2.5, variation, 25.0, 0.4, 0.02)
    return mat


def rubber(color=(0.035, 0.035, 0.038), roughness=0.8, name="rubber"):
    """Rubber: tyres, seals, grips, play-surface pour. Dull, with a faint grain. Never pure black."""
    color = _linear(color)
    mat, nt, bsdf = _new(name, color, 0.0, roughness)
    _set(bsdf, "specular", 0.3)
    grain = _noise(nt, _coords(nt, (300.0, 300.0, 300.0)), 1.0, 2.0)
    _bump(nt, bsdf, grain.outputs["Fac"], 0.05, 0.0003)
    return mat


def window_glass(color=(0.09, 0.12, 0.15), roughness=0.05, name="window_glass"):
    """The glass of a building's or a vehicle's windows as it looks from outside: dark, opaque and
    mirror-glossy, so nothing behind it has to be modelled and it never renders white. Use `glass`
    only for things really seen through (a drinking glass, a lamp cover). A curtain-wall tower takes
    a bluer colour such as (0.12, 0.22, 0.32)."""
    color = _linear(color)
    mat, nt, bsdf = _new(name, color, 0.0, roughness)
    _set(bsdf, "coat", 1.0)
    _set(bsdf, "coat_roughness", 0.02)
    _set(bsdf, "specular", 0.8)
    return mat


def screen(color=(0.55, 0.75, 0.95), glow=1.5, roughness=0.08, name="screen"):
    """A display that is switched on (phone, monitor, console, dashboard): it glows in `color` under
    a glossy cover. glow is the emission strength (about 1.5 in the studio; 0 gives a dark, off
    screen). An old LCD is (0.56, 0.62, 0.30) with glow 0.4."""
    lin = _linear(color)
    mat, nt, bsdf = _new(name, _scaled(lin, 0.5), 0.0, roughness)
    _set(bsdf, "coat", 1.0)
    _set(bsdf, "coat_roughness", 0.02)
    if glow > 0:
        for key in ("Emission Color", "Emission"):
            if key in bsdf.inputs:
                bsdf.inputs[key].default_value = _rgba(lin)
                break
        if "Emission Strength" in bsdf.inputs:
            bsdf.inputs["Emission Strength"].default_value = glow
    return mat


# --- putting materials on parts -----------------------------------------------------

def assign(obj, material):
    """Give the whole object this one material. A ready-made model placed as a part (it carries
    its own textures) is left as it is."""
    if obj.get("mg_asset"):
        return obj
    me = obj.data
    me.materials.clear()
    me.materials.append(material)
    for p in me.polygons:
        p.material_index = 0
    return obj


def assign_where(obj, material, test):
    """Give the material to the faces for which test(center, normal) is True. center and normal are
    in the part's own frame (mathutils Vectors), e.g. lambda c, n: n.z > 0.9 for upward faces."""
    me = obj.data
    if not me.materials:
        raise ValueError("assign a base material to the object first, then assign_where on top")
    if material.name not in [m.name for m in me.materials if m]:
        me.materials.append(material)
    index = [m.name if m else "" for m in me.materials].index(material.name)
    for p in me.polygons:
        if test(p.center, p.normal):
            p.material_index = index
    return obj


def assign_slot(obj, slot, material):
    """Give `material` to the faces of a placeholder slot ("glass", "trim", "lamp_rear"...): parts
    built with bpy_lib.shell carry one placeholder material per slot. Returns how many slots it filled."""
    me = obj.data
    filled = 0
    for i, current in enumerate(me.materials):
        if current is not None and current.get("mg_slot") == slot:
            me.materials[i] = material
            filled += 1
    if filled and material.get("mg_slot") is None:  # so slot_report can still say which slot it fills
        material["mg_slot"] = slot
    return filled


def _srgb_hex(color):
    """Blender's linear colour as the #RRGGBB a colour picker shows."""
    def one(c):
        c = max(0.0, min(1.0, float(c)))
        return c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
    return "#" + "".join(f"{round(one(c) * 255):02X}" for c in tuple(color)[:3])


def slot_report(objects=None):
    """What every material on the model really is, by slot: base colour (picker hex), metallic,
    roughness, transmission, coat, emission and whether a pattern drives the colour or roughness.
    objects: the model's objects (default: every mesh in the scene)."""
    objects = objects if objects is not None else [o for o in bpy.data.objects if o.type == "MESH"]
    out = {}
    for obj in objects:
        for mat in obj.data.materials:
            if mat is None:
                continue
            key = mat.get("mg_slot") or mat.name
            entry = out.setdefault(key, {"material": mat.name, "parts": []})
            part = obj.get("mg_part") or obj.name
            if part not in entry["parts"]:
                entry["parts"].append(part)
            if "base_colour" in entry or not mat.use_nodes:
                continue
            bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
            if bsdf is None:
                entry["note"] = "no Principled BSDF"
                continue

            def value(key, bsdf=bsdf):
                s = _socket(bsdf, key)
                if s is None:
                    return None
                if s.is_linked:
                    return "driven by a pattern"
                v = s.default_value
                return round(float(v), 3) if isinstance(v, float) else v

            base = _socket(bsdf, "base_color")
            if base is not None and base.is_linked:
                entry["base_colour"] = "driven by a pattern, around " + _srgb_hex(mat.get("mg_base_color", (0.5,) * 3))
            elif base is not None:
                entry["base_colour"] = _srgb_hex(base.default_value)
            for k in ("metallic", "roughness", "transmission", "coat"):
                entry[k] = value(k)
            strength = bsdf.inputs.get("Emission Strength")
            entry["emission_strength"] = round(float(strength.default_value), 3) if strength is not None else None
            entry["see_through"] = bool(mat.get("mg_alpha") is not None or getattr(mat, "blend_method", "OPAQUE")
                                        not in ("OPAQUE", None))
    return out


def car_finish(parts, paint=(0.12, 0.36, 0.78), style="stylised", accent=None, rim=None, glass_tint=None,
               trim=None):
    """Every material slot of a car's parts in one call. style "stylised" (soft gradient paint, pale
    opaque glass, like a toy or game render) or "realistic" (glossy clear-coated paint, dark tinted
    see-through glass). accent is the wing's colour (default a mid grey), rim the wheels' colour,
    glass_tint the glass colour, trim the dark trim colour. Returns the materials by slot to tune further."""
    stylised = style != "realistic"
    trim = trim or ((0.045, 0.045, 0.05) if stylised else (0.02, 0.02, 0.022))
    finish = {
        "paint": stylised_paint(paint, name="paint") if stylised else car_paint(paint, name="paint"),
        "glass": stylised_glass(glass_tint or (0.84, 0.86, 0.89), name="glass") if stylised
        else glass(glass_tint or (0.25, 0.28, 0.32), roughness=0.02, name="glass"),
        "trim": matte(trim, 0.55, name="trim"),
        # piano black: window surrounds, a blacked-out B-pillar, the mirror's triangle
        "gloss_black": (stylised_paint((0.03, 0.03, 0.033), 0.3, 0.2, name="gloss_black") if stylised
                        else car_paint((0.01, 0.01, 0.011), roughness=0.08, metallic=0.0, flake=0.0, name="gloss_black")),
        "lamp_front": lamp((0.96, 0.96, 0.93), name="lamp_front"),
        # a deep smoked red, so the lens reads apart from red paint
        "lamp_rear": lamp((0.4, 0.01, 0.02), glow=0.4 if stylised else 0.0, name="lamp_rear"),
        "lamp_amber": lamp((0.98, 0.68, 0.06), glow=0.3 if stylised else 0.0, name="lamp_amber"),
        # LED lines glow in either style: white daytime running lights at the front, red at the back
        "lamp_led": lamp((0.92, 0.95, 1.0), glow=6.0, roughness=0.1, name="lamp_led"),
        "lamp_led_rear": lamp((1.0, 0.16, 0.1), glow=8.0, roughness=0.1, name="lamp_led_rear"),
        "tyre": matte((0.09, 0.09, 0.095), 0.85, name="tyre"),
        "rim": (stylised_paint(rim or (0.68, 0.69, 0.71), 0.3, 0.4, name="rim") if stylised
                else brushed_metal(rim or (0.8, 0.8, 0.82), 0.25, name="rim")),
        "brake_disc": matte((0.42, 0.42, 0.44), 0.4, name="brake_disc"),
        "caliper": stylised_paint((0.72, 0.05, 0.04), 0.4, 0.2, name="caliper"),
        "chrome": polished_metal((0.9, 0.9, 0.92), 0.08, name="chrome"),
        # a lamp's clear cover, kept see-through even when the windows are made opaque for a render
        "lens": glass((0.95, 0.96, 0.97), roughness=0.01, name="lens"),
        "plate": matte((0.92, 0.92, 0.9), 0.45, name="plate"),  # a white number plate
        "accent": stylised_paint(accent or (0.42, 0.42, 0.44), 0.45, 0.2, name="accent"),
    }
    for name, mat in finish.items():  # keep the slot tag, so assign_slot can still replace a slot after this
        mat["mg_slot"] = name
    for objs in parts.values():
        for obj in objs:
            for name, mat in finish.items():
                assign_slot(obj, name, mat)
    return finish


RECIPES = ["wood", "brushed_metal", "polished_metal", "glass", "ceramic", "leather", "fabric",
           "painted", "plastic", "stylised_paint", "car_paint", "stylised_glass", "lamp", "matte",
           "brick", "roof_tiles", "concrete", "stone", "grass", "foliage", "rubber", "window_glass", "screen"]
