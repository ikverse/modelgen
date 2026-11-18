"""Object families: what kind of thing a model is, and what that changes.

The spec names a family (the brief chooses it; `of` guesses from the object's name when it did
not). Each family has its own short rules for the architect, the builders, the materials agent and
the critic, the kit functions (bpy_lib.kits) its builders start from, a triangle limit for a game
asset, and for some the flat views and the studio its renders use. `general` is everything else and
changes nothing.
"""

import re

FAMILIES = {
    "container": {
        "what": "a hollow vessel: mug, cup, bowl, bottle, vase, jar, pot, bucket, glass, jug",
        "words": r"mugs?|cups?|bowls?|bottles?|vases?|jars?|pots?|buckets?|jugs?|pitchers?|teapots?|kettles?|"
                 r"goblets?|tumblers?|flasks?|barrels?|planters?|urns?|cans?",
        "budget": 5000,
        "kit": ["vessel", "handle", "fuse"],
        "plan": (
            "- The vessel is ONE part: wall, inside, rim and foot are one solid on the real object. Plan a single "
            "`body` built with `k.vessel` from its OUTER profile ((radius, z) points, bottom to top) with a wall "
            "thickness; it makes the real hollow, the rounded rim and the foot ring itself. Never plan `interior`, "
            "`lip`, `rim`, `glaze` or `foot_ring` as parts: built apart they show as a lid over the opening and a "
            "collar round the rim.\n"
            "- A handle moulded onto the body (mug, jug, teapot) belongs to the same part: `k.handle`, then "
            "`k.fuse(body, [handle])`, so it meets the wall with a fillet and no joint. Only what really comes off "
            "is its own part: a lid, a cork, a cap, a saucer, a spoon.\n"
            "- The inside and outside colours are material slots of that one part (`inside`, `outside`), not parts. "
            "A glaze that wraps over the lip is `k.vessel`'s `inside_over_rim` (how far below the top edge the two "
            "colours meet on the outside), never a separate part or hand-painted faces.\n"
            "- `k.vessel` is exactly as tall as its profile (rim and foot ring included), so give a profile from "
            "z = 0 to the full height. `k.handle` takes the heights of its two joints and the body's radius there "
            "(top and bottom radius for a tapered body); it swells into the wall by itself.\n"
            "- Real sizes: ceramic walls 3 to 5 mm, glass 1.5 to 3 mm, a mug 80 to 85 mm across and 90 to 100 mm "
            "tall, a handle section about 14 x 10 mm with room for two or three fingers."),
        "build": (
            "- Build the body with `k.vessel` and a handle with `k.handle` + `k.fuse`, with the kit's own "
            "arguments (`inside_over_rim` for a glaze line, `flare` for the handle's joints, `squareness` for its "
            "shape): do not correct its height by hand, repaint its faces or reshape its joints afterwards. Do not "
            "revolve a solid and lay a disc on it. Give the outer profile the object's real curve (a slight taper, a rounded base "
            "corner, a shoulder and neck for a bottle), not a plain cylinder."),
        "materials": (
            "- The body carries the slots `outside` and `inside`: fill each with `m.assign_slot(obj, slot, "
            "material)`. `m.assign` would wipe both and paint the inside like the outside."),
        "review": (
            "- Look into the opening: there must be a real hollow with the inner wall and floor visible, a rim "
            "rounded and flush with the wall, and a handle that flows into the body. A flat disc just below the "
            "rim, a ring wider than the wall, or a notch or knob where the handle joins are faults of the part's "
            "construction: ask for the body to be rebuilt as one vessel, not for numbers to be nudged."),
    },
    "device": {
        "what": "a cased electronic or mechanical device: phone, console, remote, controller, speaker, radio, "
                "camera, monitor, laptop, clock, calculator",
        "words": r"phones?|smartphones?|consoles?|game ?boys?|remotes?|controllers?|gamepads?|speakers?|radios?|"
                 r"cameras?|monitors?|tvs?|televisions?|laptops?|tablets?|calculators?|watch(es)?|clocks?|routers?|"
                 r"keyboards?|headphones?|earbuds?|chargers?|walkie[- ]talkies?|microwaves?|toasters?|printers?",
        "budget": 6000,
        "kit": ["device_shell", "pocket", "plate", "round_button", "dpad", "slots"],
        "plan": (
            "- The case is ONE part built with `k.device_shell`, even when the real case is two moulded halves: "
            "the join is its `seam` groove. Never plan a front shell and a back shell as two parts; they never "
            "line up. Every recess is cut into that part by its builder: the screen well, a well under each "
            "button, ports, the battery or cartridge bay (`k.pocket`), speaker slots (`k.slots`).\n"
            "- Separate parts are only what is separate on the real thing, each sitting in its pocket with a "
            "0.2 to 0.4 mm gap: the screen bezel and glass (`k.plate`), buttons (`k.round_button`, `k.dpad`), "
            "wheels, a cartridge, lens rings.\n"
            "- Write every position ONCE, in the plan, as (cx, cz) on the face it sits on with its size, in the "
            "case's `details` and in the part's own entry, so the pocket and the piece that sits in it agree.\n"
            "- Give the back and the sides their details too: screws, a label recess, a battery door, ports, "
            "vents, rubber feet. A blank back reads as unfinished."),
        "build": (
            "- The case: `k.device_shell`, then `k.pocket` and `k.slots` for every recess in your plan entry, at "
            "exactly the plan's positions. A piece that sits in a pocket: `k.plate`, `k.round_button` or "
            "`k.dpad`, built about the anchor in the plan."),
        "materials": (
            "- A screen that is on takes `m.screen`; a lens or dark cover takes `m.window_glass`; moulded cases "
            "take `m.plastic` with a roughness of 0.4 to 0.6. Kit pieces carry slots: fill them with "
            "`m.assign_slot`."),
        "review": (
            "- Check the case is one piece with no step where halves meet, that every button sits in a well, and "
            "that the back and sides have their details. The screen must read as a screen (lit or a dark glass "
            "with a bezel), never a blank black slab."),
    },
    "furniture": {
        "what": "furniture: chair, table, desk, shelf, cabinet, bed, sofa, stool, wardrobe",
        "words": r"chairs?|tables?|desks?|shel(f|ves)|bookcases?|cabinets?|cupboards?|beds?|sofas?|couch(es)?|"
                 r"stools?|wardrobes?|dressers?|drawers?|armchairs?|nightstands?|sideboards?|thrones?|cribs?",
        "budget": 8000,
        "kit": [],
        "plan": (
            "- Parts are the real pieces of joinery: top, legs, aprons, stretchers, seat, back slats, doors, "
            "drawer fronts, pulls. Give each its real section: legs 35 to 60 mm, tops and shelves 18 to 40 mm, "
            "aprons 70 to 100 mm deep, set 10 to 20 mm in from the legs' faces.\n"
            "- Standard heights: seat 0.45 m, dining table 0.74 m, desk 0.73 m, coffee table 0.42 m, worktop 0.9 m.\n"
            "- Every board edge gets a 2 to 3 mm roundover, tops a slight overhang, legs a taper or a turned "
            "profile where the style has one. Upholstery is a soft subdivided form, never a sharp box.\n"
            "- Say each wooden part's grain direction (along its length) for the materials agent."),
        "build": "- Round or chamfer every board edge yourself (2 to 3 mm); taper legs; keep joints tight (parts touch).",
        "materials": "- One `m.wood` per grain direction (`grain_axis` along each part's length), grain_scale 8 to 14.",
        "review": "- Judge the thicknesses against real furniture: legs or tops that are too fat read as a toy.",
    },
    "building": {
        "what": "a building: house, tower, shop, church, barn, castle, garage, apartment block",
        "words": r"houses?|buildings?|towers?|skyscrapers?|shops?|stores?|church(es)?|mosques?|temples?|barns?|"
                 r"castles?|garages?|sheds?|cabins?|cottages?|villas?|apartments?|offices?|hotels?|schools?|"
                 r"stations?|lighthouses?|windmills?|warehouses?|factor(y|ies)|huts?|pagodas?|townhouses?",
        "budget": 20000,
        "kit": ["openings", "walls", "windows", "door", "roof", "curtain_tower"],
        "views": ["front_flat", "side", "rear_flat", "left_flat"],
        "plan": (
            "- EVERY wall gets its windows and doors: front, back and both sides. A blank wall is a fault unless "
            "it is a party wall against a neighbour (then say so in `assembly_notes`).\n"
            "- One layout feeds every part: work out the `k.openings(...)` arguments once (width, depth, floors, "
            "floor_height, bays per face, window size, sill height, the door) and write those exact numbers into "
            "the entries of `walls` (built with `k.walls`: the openings sunk as reveals), `windows` (`k.windows`: "
            "frames, glazing bars, sills and dark panes) and `door` (`k.door`), so they agree.\n"
            "- The roof is `k.roof` (gable, hip or flat with a parapet). A glass office or apartment tower is one "
            "`k.curtain_tower` part per tower. Build by hand only what the kit has not got: a gable ornament, "
            "steps, a chimney, gutters, a porch, balconies, a shop sign.\n"
            "- Real sizes: storeys 2.7 to 3.2 m in a house and 3.6 to 4.0 m in an office, sills 0.9 m above the "
            "floor, doors 2.1 m, window reveals 0.10 to 0.15 m deep, a plinth 0.3 to 0.6 m."),
        "build": (
            "- `walls`, `windows` and `door` must call the kit with the SAME openings numbers the plan gives; "
            "never retype positions by hand. Ornaments stand proud of the wall by a real amount (cornice 0.15 m, "
            "sill 0.05 m)."),
        "materials": (
            "- Walls: `m.brick`, `m.concrete` or `m.stone`; roofs: `m.roof_tiles`; window panes: `m.window_glass` "
            "(dark and opaque; `m.glass` shows the empty inside as white). Kit parts carry slots (`wall`, "
            "`reveal`, `frame`, `glass`, `sill`, `door`, `roof`...): fill them with `m.assign_slot`."),
        "review": (
            "- Walk round it: the flat views sheet shows all four sides. Every side must have its openings; glass "
            "must be dark, set back in a reveal; brick must read as brick at turntable distance (not pink)."),
    },
    "scene": {
        "what": "several objects on a ground: park, playground, street corner, plaza, garden, courtyard, diorama",
        "words": r"parks?|playgrounds?|streets?|plazas?|squares?|gardens?|courtyards?|dioramas?|scenes?|villages?|"
                 r"camp(site|ground)s?|farms?|markets?|landscapes?|islands?|harbou?rs?|yards?|crossroads?|campus(es)?",
        "budget": 60000,
        "kit": ["tree", "bench", "fence", "lamp_post"],
        "plan": (
            "- A scene is judged by its props, so each prop is built at its real size with the detail it would "
            "have as a model of its own. Plan at most about 18 parts: the ground surfaces (one slab part each), "
            "then the props.\n"
            "- Use the kit for common props: `k.tree` (give different `seed`s and heights so no two trees match), "
            "`k.bench`, `k.fence` along a list of points, `k.lamp_post`. Real sizes: a tree 4 to 8 m with a crown "
            "3 to 5 m across, a bench 1.5 m long and 0.45 m to the seat, a fence 1.0 to 1.2 m, a lamp post 4 m.\n"
            "- A ready-made model from the free catalogue can be a part: when `search_assets` finds a fitting one, "
            "name its id in the entry's `asset` (it comes with its own textures and needs no builder).\n"
            "- Place repeated props with varied rotation and uneven spacing in `instances`; never in a grid unless "
            "the real place has one. Everything stands on the ground it is placed on (give the ground's top Z)."),
        "build": "- Build your prop at real scale with real detail: it will be seen close up as well as from afar.",
        "materials": (
            "- Grounds: `m.grass`, `m.concrete`, `m.rubber`, `m.stone`; crowns `m.foliage`. Kit props carry slots "
            "(`trunk`, `leaves`, `wood`, `metal`, `post`, `rail`, `lamp`): fill them with `m.assign_slot`. A part "
            "that is a ready-made model keeps its own textures; leave it alone."),
        "review": (
            "- Judge the props from the close-ups, not only the wide views: lollipop trees, slab benches and "
            "ribbon-thin slides are faults. Check scale between props (a bench against a tree against a fence)."),
    },
    "vehicle": {
        "what": "a wheeled vehicle that is not a car: bus, truck, train, locomotive, tram, tractor, trailer, "
                "motorbike, bicycle, forklift",
        "words": r"bus(es)?|trucks?|lorr(y|ies)|trains?|locomotives?|trams?|tractors?|trailers?|motorbikes?|"
                 r"motorcycles?|scooters?|bicycles?|bikes?|forklifts?|karts?|carts?|wagons?|carriages?|"
                 r"ambulances?|fire ?(trucks?|engines?)|bulldozers?|excavators?|tanks?|rovers?|pickups?",
        "budget": 25000,
        "kit": ["vehicle_body", "hood", "wheel", "wheel_arches", "axle_positions", "pocket"],
        "views": ["side", "front_flat", "rear_flat", "top_flat"],
        "plan": (
            "- Set the side-view proportions first and write them into `assembly_notes`: overall length, wheelbase "
            "(each axle's Y), front and rear overhang, floor height, roof height, wheel radius. Real numbers: a "
            "city bus 12 x 2.55 x 3.1 m with 0.5 m wheels and a 6 m wheelbase; a school bus wheel 0.5 m; a truck "
            "wheel 0.52 m; a tractor rear wheel 0.8 m.\n"
            "- The body is ONE part built with `k.vehicle_body`: its side windows, windshield, rear window, doors "
            "and every lamp are openings sunk into that one solid, each with its own material slot (`glass`, "
            "`door`, `lamp_red`, `lamp_amber`, `lamp_clear`). NEVER plan windows, a windshield, doors, tail lamps, "
            "warning lamps or headlights as separate parts laid on the body: a plate on a solid body shows the "
            "body's colour through and around it, and each one costs a builder and hundreds of triangles. Give "
            "the body's entry the exact numbers: the window rows (count, first and last Y, bottom and top Z), the "
            "windshield and rear window heights, each door (side, position, width, heights) and each lamp (face, "
            "position, height, size, colour).\n"
            "- An engine hood (a conventional bus's or truck's nose) is `k.hood`: it narrows and slopes down to "
            "the grille, carries the grille, the headlights and the front fenders, and sits against the body's "
            "front. Never make it a box or a second `k.vehicle_body`: a box of the same length looks longer and "
            "heavier than the real nose. Its length is the distance from the body's front to the grille as "
            "MEASURED on a side view of the real vehicle or published; when the spec has only a guess for it, "
            "look at the reference photos and keep it short rather than long (on a full-size conventional bus "
            "the hood is plainly shorter than the gap between the axles is long, and its top stays below the "
            "windshield's bottom edge). Say in `assembly_notes` where the length came from. In its entry give the hood's top and its "
            "underside as heights above the ground in `details` (its underside is level with the body's floor, "
            "so the wheels and the road show under it), and in `dimensions` its own height, top minus underside. "
            "A cargo box or a "
            "trailer is a second `k.vehicle_body` without glass. The wheel arches are cut into the hood and the "
            "body with `k.wheel_arches` (8 to 10 percent bigger than the wheel). Wheels are `k.wheel` (one "
            "entry per wheel size, `count` and `instances` for the copies; `dual=True` for twin rear wheels).\n"
            "- Separate parts are only what stands off the body: bumpers, grille, mirrors, a stop arm, steps, a "
            "sign board, an exhaust, a chassis rail. Keep them few (a bus is about 10 to 12 parts in all) and "
            "plain: a mirror is 100 to 200 triangles, lettering is a flat board in another colour, never "
            "modelled letter by letter."),
        "build": (
            "- The body: `k.vehicle_body` with the plan's window rows, windshield, rear window, doors and lamps, "
            "then `k.wheel_arches`; a further recess is `k.pocket` with a slot name. The hood: `k.hood` with its "
            "grille, headlights and fenders, never a box. Do not build glass, doors or "
            "lamps as pieces of their own. Wheels: `k.wheel` with the plan's radius and width (give it `hub_reach` "
            "so its axle stub reaches into the body and the wheel is not left floating)."),
        "materials": (
            "- The body carries slots: `body` (the paint: `m.car_paint` with `flake=0` and `metallic=0`), `glass` "
            "(`m.window_glass`), `door` (the paint, or a shade darker so the door reads), `lamp_red`, `lamp_amber` "
            "and `lamp_clear` (`m.lamp` in red, amber and near-white). Fill every one with `m.assign_slot`; "
            "`m.assign` on the body would paint over its glass and lamps. Tyres are the wheel's `tyre` slot "
            "(`m.rubber`), rims its `rim` slot."),
        "review": (
            "- Use the flat side view to judge wheelbase, overhangs and wheel size against the real vehicle before "
            "anything else. Look hard at the hood or nose: compare its share of the length, and its height "
            "against the windshield, with the side-view photo; a hood that is too long or too tall is the first "
            "change, with the length you see in the photo. Then the glass: every window, the windshield and the doors' panes must read as dark "
            "glass sunk into the body, and red, amber and clear lamps must each be their own colour. Glass or "
            "lamps that show the body's colour mean they were built as plates: ask for the body to be rebuilt "
            "with them as openings (target `plan` when they are separate parts)."),
    },
    "character": {
        "what": "a stylised figure: toy figure, mascot, statue, robot, animal, creature",
        "words": r"characters?|figures?|figurines?|mascots?|statues?|robots?|dolls?|animals?|creatures?|monsters?|"
                 r"dogs?|cats?|horses?|birds?|dragons?|dinosaurs?|bears?|rabbits?|fish(es)?|humans?|people|"
                 r"persons?|soldiers?|knights?|aliens?|zombies?|heroes?",
        "budget": 15000,
        "kit": [],
        "plan": (
            "- Stylised only: build the body as a few soft, overlapping forms (subdivided boxes and lofts) that "
            "sink into each other at the joints, one side mirrored to the other. Head, torso and pelvis are "
            "separate soft forms; limbs taper; hands and feet are simple mitten and boot shapes.\n"
            "- Faces are simple: eyes as inset discs or painted slots of the head, a brow, a small mouth. Hair is "
            "one solid mass with a few large clumps. Clothes are the body's own surface in another colour, with a "
            "raised hem or belt where they end, not a second shell."),
        "build": "- Keep forms soft (subdivision), sink each form 10 to 20 percent into its neighbour so no gap shows.",
        "materials": "- Matte, toy-like colours (`m.matte`, `m.plastic` at roughness 0.5 or more); no skin gloss.",
        "review": "- Judge the silhouette and the pose first; then that no gap or floating piece shows at a joint.",
    },
    "mechanical": {
        "what": "a mechanical part or mechanism shown working: gear, gear train, rack, shaft, bearing, pulley, "
                "piston, crankshaft, camshaft, turbocharger, spring, bolt, valve, gearbox",
        "words": r"gears?|gearing|gear ?trains?|gearbox(es)?|gear ?wheels?|cogs?|cogwheels?|sprockets?|pulleys?|"
                 r"bearings?|shafts?|axles? shafts?|pistons?|crank ?shafts?|crankshafts?|cranks?|cam ?shafts?|"
                 r"camshafts?|cams?|turbos?|turbochargers?|superchargers?|turbines?|impellers?|"
                 r"compressor wheels?|springs?|bolts?|nuts?|washers?|screws?|valves?|differentials?|racks? and pinions?|"
                 r"pinions?|ring gears?|planetary|worm gears?|worm wheels?|flywheels?|clutch(es)?|"
                 r"connecting rods?|con ?rods?|rocker arms?|mechanisms?|transmissions?",
        "budget": 30000,
        "kit": ["spur_gear", "helical_gear", "ring_gear", "rack", "bevel_gear", "shaft", "gear_pair_layout"],
        "views": ["front_flat", "side"],
        "plan": (
            "- Plan from the engineering numbers in the mechanism sheet, never by eye: module, tooth counts, face "
            "widths, bores, centre distances. A part the part tools make (a gear, a rack, a shaft) is already "
            "built from the sheet: keep its entry and its name, and plan only what no tool makes (a housing, a "
            "bracket, a base), sized to the sheet's numbers (a housing clears the gears' tip circles by 2 to 5 mm).\n"
            "- Real proportions: face width 6 to 12 modules, a rim at least 2 modules under the tooth roots, a hub "
            "1.6 to 2 times the bore, keys and keyways to standard sizes."),
        "build": (
            "- Never hand-shape teeth: a gear is `k.spur_gear`, `k.helical_gear`, `k.ring_gear`, `k.rack` or "
            "`k.bevel_gear` with the sheet's numbers in metres, a shaft `k.shaft`; place a meshing pair with "
            "`k.gear_pair_layout`. Build only the part the plan gives you."),
        "materials": (
            "- Gears and shafts carry the slots `tooth_face`, `hub` and `bore` (a shaft `shaft`): fill each with "
            "`m.assign_slot`. Machined steel is `m.brushed_metal` (roughness 0.3 to 0.45), brass a warm "
            "`m.brushed_metal`, a cast housing `m.painted` or `m.matte`. No rust or grime unless asked."),
        "review": (
            "- Judge tooth shape and meshing first: involute flanks, the tooth count and depth of the sheet, teeth "
            "sitting in each other's gaps all through the motion strip, never cutting into or floating off their "
            "mate (the mesh check measures this). Then speeds and directions, then proportions, then the look."),
    },
    "general": {"what": "anything else", "words": "", "budget": 12000, "kit": []},
}

NAMES = list(FAMILIES)
_GUESS = [(name, re.compile(rf"\b({f['words']})\b", re.IGNORECASE)) for name, f in FAMILIES.items() if f["words"]]
# A scene names other families' things ("a park with benches"), so it is asked first; a vehicle
# before a building ("a bus station" is a building, but "a school bus" is a bus: the last word wins below).
_ORDER = ["scene", "vehicle", "building", "device", "furniture", "container", "character", "mechanical"]
# A part named by its own word is the part, not the machine it comes from: "a truck gearbox", "car engine
# piston" and "bicycle sprocket" are mechanical parts, whichever word comes last.
_MECHANICAL_FIRST = re.compile(r"\b(gears?|gearbox(es)?|cogs?|cogwheels?|sprockets?|pistons?|crank ?shafts?|"
                               r"crankshafts?|cam ?shafts?|camshafts?|turbochargers?|superchargers?|turbos?|"
                               r"differentials?|connecting rods?|flywheels?|pinions?|impellers?|"
                               r"compressor wheels?|bearings?|transmissions?|pulleys?|belt drives?|roller chains?|"
                               r"chain drives?|worm (gears?|drives?|wheels?)|planetary|epicyclic|cam followers?|"
                               r"cams? and followers?|poppet valves?|valve springs?|coil springs?|compression springs?|"
                               r"extension springs?|hex bolts?|bolts? and nuts?|nuts? and bolts?)\b", re.IGNORECASE)


def mechanical_word(text):
    """Whether the text names a mechanical part outright (a gear, a piston, a turbocharger...): such a
    request is that part, never a car or a ready-made model."""
    return bool(_MECHANICAL_FIRST.search(str(text or "")))


def guess(text):
    """The family an object's name points to, or "general". The family whose word comes last in
    the text wins ("school bus" is a vehicle, "bus station" a building), a scene word beats all, and
    a word that names a mechanical part outright beats every other family ("truck gearbox")."""
    text = str(text or "")
    if mechanical_word(text):
        return "mechanical"
    best, where = "general", -1
    for name in _ORDER:
        rx = dict(_GUESS)[name]
        hits = [m.start() for m in rx.finditer(text)]
        if not hits:
            continue
        if name == "scene":
            return name
        if hits[-1] > where:
            best, where = name, hits[-1]
    return best


def of(spec):
    """The family of a spec: the one it names, else a guess from its object."""
    name = str((spec or {}).get("family") or "").strip().lower()
    return name if name in FAMILIES else guess((spec or {}).get("object"))


def rules(family, role):
    """This family's rules for one role (plan, build, materials, review) as a prompt section, or ""."""
    text = (FAMILIES.get(family) or {}).get(role)
    if not text:
        return ""
    title = {"plan": "planning", "build": "building", "materials": "the materials", "review": "the review"}[role]
    return f"## This is a {family}: rules for {title}\n{text}\n"


def kit(family):
    """The kit functions (bpy_lib.kits) this family's builders start from."""
    return list((FAMILIES.get(family) or {}).get("kit") or [])


def budget(family):
    """The triangle limit of a game asset of this family."""
    return int((FAMILIES.get(family) or FAMILIES["general"])["budget"])


def views(family):
    """The flat views a family's final renders add (a sheet of their own for the critic), or []."""
    return list((FAMILIES.get(family) or {}).get("views") or [])


def studio(family):
    """The studio a family is rendered in: "vehicle" for the car studio, else None."""
    return (FAMILIES.get(family) or {}).get("studio")


def brief_list():
    """The families in one line each, for the brief agent's task."""
    return "\n".join(f"- `{name}`: {f['what']}" for name, f in FAMILIES.items())
