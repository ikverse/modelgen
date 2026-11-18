"""The car body built from a real car's views (bpy_lib.shells.car_body), and its wheels. Runs inside
Blender.

body(name) skins the reference's cross-sections into one smooth closed mesh, sizes it to the
reference's length, width and height (smoothing pulls a skin a little inside its sections), and cuts
each wheel opening with its well: a short cylinder from outside the body in to the well's inner wall,
so the opening shows the tyre and the well behind it, not a tunnel through the car. wheels(name)
builds a wheel of the reference's size and places one at each corner.
"""

import array

import bmesh

import bpy

from bpy_lib import helpers as h
from bpy_lib import shell
from bpy_lib.shells import car_body

CAGE_LEVELS = 1   # subdivision levels on the dense cage of sections


def _fit_together(objs, half_width, y_range, z_range):
    """Scale the objects by one mapping so that together they are exactly half_width wide each side and
    span y_range and z_range (smoothing pulls a skin a little inside its sections)."""
    vs = [v.co for o in objs for v in o.data.vertices]
    kx = half_width / max(abs(v.x) for v in vs)
    y0, y1 = min(v.y for v in vs), max(v.y for v in vs)
    z0, z1 = min(v.z for v in vs), max(v.z for v in vs)
    ky = (y_range[1] - y_range[0]) / (y1 - y0)
    kz = (z_range[1] - z_range[0]) / (z1 - z0)
    for o in objs:
        for v in o.data.vertices:
            v.co.x *= kx
            v.co.y = y_range[0] + (v.co.y - y0) * ky
            v.co.z = z_range[0] + (v.co.z - z0) * kz
        o.data.update()


def _clean(obj, dist=1e-5):
    """Weld points closer than `dist` and dissolve the faces that leaves degenerate, in one pass, so a
    join's tiny slivers cannot tear the mesh when it is welded again later."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=dist)
    bmesh.ops.dissolve_degenerate(bm, edges=bm.edges[:], dist=dist)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def _cut_with_materials(obj, cutters):
    """Cut each cutter out of obj (exact solver), the faces each leaves taking the cutter's material;
    the cutters are deleted."""
    for cutter in cutters:
        mod = obj.modifiers.new("boolean", "BOOLEAN")
        mod.operation = "DIFFERENCE"
        mod.object = cutter
        try:
            mod.solver = "EXACT"
        except (AttributeError, TypeError):
            pass
        if hasattr(mod, "material_mode"):
            mod.material_mode = "TRANSFER"
        cutter.hide_render = True
        h.apply_modifiers(obj)
        bpy.data.objects.remove(cutter, do_unlink=True)


def body(name, levels=CAGE_LEVELS, details=True):
    """The body of an example car type, as one closed mesh object named "body": the lower body and the
    glasshouse skinned apart and joined, so the seam between them is the crisp shoulder line; then a lip
    round each wheel opening, the openings and wells, and (details=True) the glass, lamps, grille and
    shut lines traced from the reference's blueprint (bpy_lib.shells.car_details)."""
    from bpy_lib.shells import car_details

    b = car_body.body(name)
    rings = b.rings()
    obj = shell.skin(rings, levels=levels, name="body", end_crease=0.0, end_centre_lines=b.cap_lines(rings))
    glass_rings = b.glass_rings()
    glass = (shell.skin(glass_rings, levels=levels, name="glasshouse", end_crease=0.0,
                        end_centre_lines=b.cap_lines(glass_rings)) if glass_rings else None)
    lowest = min(z for _, z in b.underside)
    _fit_together([obj] + ([glass] if glass else []), b.width / 2, (b.y_front, b.y_rear), (lowest, b.height))
    if glass:
        h.union(obj, [glass])
    _clean(obj)
    skipped = []
    inner = shell._Inner(obj) if details else None  # the clean skin, that every cut keeps an even depth below
    try:
        if details:
            skipped += car_details.apply(obj, car_details.arch_lips(b), inner)
        outer = b.width / 2 + 0.15
        cutters = []
        for x, y, z in b.wheel_positions():
            well = b.well_x()
            depth = outer - well
            side = 1 if x > 0 else -1
            cutter = h.cylinder(b.arch_radius(), depth, segments=96,
                                location=(side * (well + depth / 2), y, z), rotation_deg=(0, 90, 0),
                                name="wheel_opening")
            cutter.data.materials.append(shell.slot("trim"))  # the well's walls: a matte black liner
            cutters.append(cutter)
        _cut_with_materials(obj, cutters)
        _clean(obj)
        if details:
            skipped += car_details.apply(obj, car_details.features(name, b), inner)
    finally:
        if inner:
            inner.free()
    # no cleanup after the details: each cut is checked closed as it goes, and dissolving the tiny faces
    # where two lamp cuts meet would open the mesh there
    h.smooth(obj)  # smooth shading, crisp only where a detail's edge is sharp
    obj[h.NO_BEVEL_KEY] = 1  # a smooth body: no edge bevel, which would cut new slivers at the joins
    obj["mg_reference"] = name
    if skipped:
        obj["mg_skipped_details"] = ", ".join(skipped)
    return obj


def wheels(name, spokes=5, style="spoke"):
    """Four wheels of the reference's size at its wheel positions; returns them, the first built and
    the rest instances of it."""
    b = car_body.body(name)
    pieces = shell.wheel(diameter=2 * b.wheel_radius, width=b.tyre_width, spokes=spokes, style=style,
                         hub_reach=b.track / 2 - b.well_x())
    first = h.finalize_part(pieces, "wheel")  # the tyre, rim and hub as one wheel object
    out = []
    for i, (x, y, z) in enumerate(b.wheel_positions()):
        piece = first if i == 0 else h.instance(first)
        h.place(piece, location=(x, y, z), rotation_deg=(0, 0, 0 if x > 0 else 180))
        out.append(piece)
    return out


def dump_triangles(objs, path):
    """Write the objects' triangles in world space to `path` as little-endian float32, nine numbers
    (three corners) per triangle, for modelgen.tools.car_score."""
    dg = bpy.context.evaluated_depsgraph_get()
    data = array.array("f")
    for obj in objs:
        ev = obj.evaluated_get(dg)
        me = ev.to_mesh()
        me.calc_loop_triangles()
        mw = obj.matrix_world
        verts = [mw @ v.co for v in me.vertices]
        for tri in me.loop_triangles:
            for i in tri.vertices:
                p = verts[i]
                data.extend((p.x, p.y, p.z))
        ev.to_mesh_clear()
    if data.itemsize != 4:
        raise RuntimeError("float32 arrays are not 4 bytes here")
    with open(path, "wb") as f:
        data.tofile(f)
    return len(data) // 9
