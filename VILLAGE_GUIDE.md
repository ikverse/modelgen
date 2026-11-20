# Asking ModelGen for mechanical parts: a guide for the Village

ModelGen builds mechanical parts (gears now; turbochargers, pistons and more in later stages) as
accurate 3D models that move correctly, and hands them back as looping transparent picture
sequences for your learning videos, with the data to place labels and arrows. This guide says what
to send so the result is right first time. The full API contract is in `API.md`; the copy-in client
is `modelgen_client.py`.

ModelGen runs on this PC at `http://127.0.0.1:8770/v1`. Check `GET /v1/health` first; every other
call needs the key from ModelGen's `data\api_key.txt` as `X-ModelGen-Key` (the client does this).

## Two ways to ask

| | by numbers (`mechanism`) | in plain words (`object`) |
|---|---|---|
| what you send | every part's type and numbers | a sentence: "two gears, the small one drives the big one" |
| who decides the numbers | you | ModelGen's engineer agent (listed back in `assumed`) |
| time | 1 to 3 minutes | 2 to 4 minutes |
| Claude usage | none | one short call (about $0.05) |
| exact | yes: the same numbers always give the same model, and a repeat comes back at once | close to what you described |

Use numbers whenever your animation already has them (tooth counts you drew, a ratio the script
explains). Use words for a quick idea. Either way, read `assumed` in the answer: it lists every
value ModelGen chose for you.

`GET /v1/parts` lists every part type with its fields, units (mm, degrees, rpm), allowed ranges and
defaults. Ask it rather than hard-coding this guide's tables.

## What to send for each part type

Lengths in millimetres. **Required** fields must be there; the rest have sensible defaults.

### Spur gear (`spur_gear`): straight teeth
- **module** (tooth size; pitch diameter = module × teeth) and **teeth**.
- `width` (default 8 × module), `bore` (default about a quarter of the pitch diameter),
  `hub_diameter` + `hub_length` for a boss, `web`: `solid` / `holes` / `spokes` (with `holes` or
  `spokes` counts) for a big gear, `keyway: true` for a key slot.
- Fewer than 17 teeth: ModelGen shifts the profile so the teeth are not undercut and gives the mate
  the opposite shift, keeping the textbook centre distance. You do not need to do anything.

### Helical gear (`helical_gear`): teeth along a helix
- As a spur gear, plus `helix_angle` (default 20°) and `herringbone: true` for a double helix.
- Meshing helical gears get opposite hands by themselves; both need the same helix angle.

### Ring gear (`ring_gear`): teeth on the inside
- **module**, **teeth** (at least 10 more than the spur gear inside it), `rim_diameter`.
- The spur gear inside names it in `meshes_with`; they turn the same way.

### Rack (`rack`): a straight toothed bar
- **module**; `teeth` (default 12), `width`, `height`. Meshes only with a spur gear, which drives it;
  the rack slides back and forth (`motion.json` `mode: "swing"`).

### Bevel gear (`bevel_gear`): two cones meeting at 90°
- **module**, **teeth**; the pair's tooth counts set the cone angles. Comes in pairs only.

### Shaft (`shaft`)
- `diameter` (default: the bore of the first gear on it), `length` (default: the gears on it plus
  some each side), `keyway`. A gear says `"on_shaft": "<shaft name>"`; gears on one shaft turn
  together (a compound gear).

### Turbocharger: `compressor_wheel`, `turbine_wheel`, `volute`, `bearing`, `centre_housing`
- `compressor_wheel` and `turbine_wheel`: **diameter** (the rim). Defaults follow real wheels: the
  compressor has 6 blades and 6 splitters swept back 45°, the turbine 11 blades.
- `volute`: the snail housing; **houses** names its wheel. It is built round that wheel, so you give
  it no size.
- `bearing`: on the shaft; bore, outer diameter and width follow the shaft unless you give them.
- `centre_housing`: the cast bearing housing between the two volutes; **houses** names the shaft.
  It fills the room between the volutes' back plates round the bearings, with an oil inlet on top
  and a drain below (`oil_ports: false` leaves them off). Its body is 1.1 of the compressor wheel
  across unless you give `outer_diameter`.
- One `shaft` carries them front to back in the order you list them: compressor wheel, bearings,
  turbine wheel. The wheels' noses point away from each other.

```json
{"parts": [
  {"name": "shaft", "type": "shaft"},
  {"name": "compressor", "type": "compressor_wheel", "diameter": 50, "on_shaft": "shaft"},
  {"name": "bearing_front", "type": "bearing", "on_shaft": "shaft"},
  {"name": "bearing_back", "type": "bearing", "on_shaft": "shaft"},
  {"name": "turbine", "type": "turbine_wheel", "diameter": 45, "on_shaft": "shaft"},
  {"name": "compressor_housing", "type": "volute", "houses": "compressor"},
  {"name": "turbine_housing", "type": "volute", "houses": "turbine"},
  {"name": "centre_housing", "type": "centre_housing", "houses": "shaft"}],
 "rpm": 30}
```

Ask for `outputs: ["motion", "cutaway", "exploded"]` with `angle: "side"`: the cutaway slices all
three housings through the shaft so the wheels and bearings show turning inside; the exploded view
slides the parts apart along the shaft (the centre housing lifts off the bearings) and back.
`parts.json` has the air paths (`intake`, `compressed` on the compressor side; `exhaust_in`,
`exhaust` on the turbine side) and the `oil` path through the centre housing, in picture pixels:
draw them as arrows over the frames. A real turbo spins at 100 000 rpm or more; 20 to 40 rpm is easy to follow. The
loop is a few turns long because the bearings' balls run round more slowly than the shaft.

### Piston and crank: `crankshaft`, `connecting_rod`, `piston`, `piston_rings`, `cylinder`
- `crankshaft`: **stroke** (the piston's travel; it takes the bore if you leave it out). Crankpin,
  journals, webs and counterweights follow real proportions. Several rods make an inline engine;
  `throw_angles` sets each throw (default the usual firing order: 0/180/180/0 for four).
- `connecting_rod`: **on_crank** names the crankshaft; `length` (centre to centre, default 1.7 ×
  stroke); `throw` (0 at the front) when you want a rod on a particular throw.
- `piston`: **on_rod** names its rod; **bore** (default the stroke); `crown` flat, dish or dome.
  Ring grooves, skirt and gudgeon pin follow the bore.
- `piston_rings`: **on_piston**; two compression rings and an oil ring in the grooves.
- `cylinder`: **houses** names the piston; `style` `liner` (a sleeve) or `block` (a block section
  with a water jacket). Its height is worked out so the crown reaches the top and the rings stay
  inside at the bottom, clear of the counterweights.
- The crankshaft drives: the rod rides its crankpin and the piston slides straight up and down,
  worked out exactly every frame. One turn of the crank is the loop (72 frames at 20 rpm).

```json
{"parts": [
  {"name": "crank", "type": "crankshaft", "stroke": 80},
  {"name": "rod", "type": "connecting_rod", "on_crank": "crank"},
  {"name": "piston", "type": "piston", "bore": 80, "on_rod": "rod"},
  {"name": "rings", "type": "piston_rings", "on_piston": "piston"},
  {"name": "cylinder", "type": "cylinder", "houses": "piston"}],
 "rpm": 20}
```

Ask for `outputs: ["motion", "cutaway", "exploded"]` with `angle: "front"` (looking along the
crank): the cutaway slices the cylinder so the piston shows sliding inside; the exploded view lifts
rod, piston, rings and cylinder off the crank in that order. A real engine idles at about 800 rpm;
10 to 30 rpm is easy to follow. ModelGen refuses a rod too short for its stroke and says how long it
must be.

### Fasteners: `bolt`, `nut`, `washer`
- `bolt`: `diameter` (the M size, default 10) and `length`; the ISO coarse pitch and hexagon follow.
- `nut` and `washer`: **on_bolt** names the bolt. The nut sits seated with five pitches of thread
  below it; as the driver it screws four turns off and back on (`rpm` is its fastest speed). The
  threads are true ISO helices: the nut's thread runs in the bolt's the whole way.

### Spring: `spring`
- `mean_diameter`, `wire_diameter`, `free_length`, `coils`, `kind` (`compression` or `extension`).
- As the driver it is squeezed by `squeeze` (default 0.3 of its length) and let go, `rpm` times a
  minute. ModelGen refuses a squeeze that would close the coils solid and says how far it can go.

### Belt and chain drives: `pulley` + `belt`, `sprocket` + `chain`
- `pulley`: **datum_diameter**, `grooves`, `section` (Z, A or B). `belt`: **on** `[driver, driven]`,
  `centre_distance` (default the two diameters added). Both pulleys turn the same way, the driven
  at driver diameter / driven diameter. A belt wrapping under 120° of the small pulley is refused.
- `sprocket`: **teeth**, `pitch` (12.7 mm). `chain`: **on** `[driver, driven]`, `centre_distance`.
  ModelGen moves the distance to the nearest one a whole, even number of links allows, and lists it
  under `assumed`. The chain's rollers sit in the tooth gaps and run round with the sprockets.

### Cam, follower, valve: `cam`, `follower`, `valve` (+ a `spring` on the valve)
- `cam`: **base_diameter**, **lift**, `rise`, `dwell`, `fall` (degrees; cycloidal rise and fall).
- `follower`: **on_cam**; `kind` `roller` or `flat`; it stands above the cam and rides it all the
  way round (checked). `valve`: **on_cam**, **head_diameter**; it hangs under the cam with a bucket
  tappet, opened downward as an overhead cam does; add `{"type": "spring", "on_valve": "valve"}` for
  its spring, sized to the valve and squeezed as it opens. A cam that would jam a roller or undercut
  it is refused.

### Planetary set: `planetary_set`
- **module**, **sun_teeth**, **planet_teeth**, `planets` (3), `fixed` (ring), `input` (sun). The
  ring has sun + 2 × planet teeth. It comes back as the parts `<name>_sun`, `<name>_planet1`...,
  `<name>_ring`, `<name>_carrier`. Every member turns by Willis's equation (sun in, ring held:
  carrier = sun / (1 + ring / sun)), and the planets ride round on the carrier. Sun + ring teeth
  must divide by the number of planets.

### Worm drive: `worm` + `worm_wheel`
- `worm`: **module** (axial), `starts`, `pitch_diameter` (10 × module), `hand`. `worm_wheel`:
  **teeth**, **meshes_with** the worm. The wheel sits above the worm and turns starts / teeth of a
  turn per worm turn; the worm must drive (a worm wheel cannot turn its worm).

```json
{"parts": [
  {"name": "small", "type": "sprocket", "teeth": 18},
  {"name": "big", "type": "sprocket", "teeth": 36},
  {"name": "chain", "type": "chain", "on": ["small", "big"], "centre_distance": 400}],
 "rpm": 20}
```

Look along the shafts (`angle: "front"`) for belts, chains, cams, planetary sets and worm drives;
`"side"` shows a bolt and nut or a spring best. The exploded view slides nuts off past the bolt's
tip, belts and chains off their wheels, followers away from their cams, a worm wheel up off its
worm and a planetary set apart along its axis.

### From your own design: `design`
Send the design you drew instead of numbers, and ModelGen makes the 3D part that matches it:

```json
{"client": "village", "design": "C:\\...\\data\\library\\parts\\two-gears\\design.json",
 "outputs": ["motion"], "style": "explainer", "angle": "front", "camera": "ortho"}
```

- `design` is the design's JSON itself or the full path of its `design.json`.
- Every group whose filled outline is a toothed ring becomes a gear: the teeth counted from your
  drawing, the bore and hub from the round shapes inside it, its colour your fill. Gears drawn
  touching mesh at the angle you drew them; the largest gear's teeth start where you drew them.
  Lines, shadows and anything not toothed are listed in `design.skipped` with the reason.
- The scale: `design_module` (mm) or `design_mm_per_px`, else a standard module near half your drawn
  module in pixels. Draw teeth that size: tip diameter = (teeth + 2) x module.
- Your cast (people) have no part ModelGen's tools make: such a design is refused, naming each group.

**Swapping your flat part for the 3D one.** Ask for `camera: "ortho"` and `angle: "front"`: the
3D part then faces you exactly as drawn. The job answers `design.mm_per_px` and, for each part,
`design.parts.<name>.centre_px` (where its centre is in your canvas) and `tip_radius_px`. In
`motion/explainer-ortho/parts.json`, each frame gives that part's `centre_px` in the rendered
picture, and `view.pixels_per_metre` in `motion.json` says the picture's scale. Scale the frames
by `1000 / (mm_per_px x pixels_per_metre)` (picture pixels to your pixels), then move them so the
largest gear's rendered centre lands on your `centre_px`: every gear then sits exactly over your
flat one (tested: within 0.02 of your pixels). The largest gear is tooth on tooth with your
drawing at the first frame; the gears it drives are turned to mesh with it, so draw them in mesh
too or their teeth will sit a little round from yours.

### Connecting them
- `meshes_with`: the gear(s) it meshes with (name the link once). Meshing gears need the same module
  and pressure angle.
- `at`: where a gear sits round the one it meshes with, in degrees (0 right, 90 above). Leave it out
  and ModelGen lays the train out left to right, moving a gear round if it would hit another.
- Top level: `driver` (default the first gear), `rpm` (the driver's speed, default 20),
  `direction` (`clockwise` or `counterclockwise` seen from the front).
- Never close a loop of meshing gears: it cannot turn, and ModelGen refuses it.

## Choices for the video

| field | values | for |
|---|---|---|
| `outputs` | `motion`, `cutaway`, `exploded`, `views`, `game`, `full` | `motion` is the loop; `cutaway` the loop with housings sliced open; `exploded` the parts sliding apart and back; `views` the stills; `game`/`full` the 3D files |
| `cut` | `auto` (default), `top`, `side`, `front` | where a cutaway slices the housings: `auto` through the axis, the half nearest the camera removed |
| `style` | `explainer` (recommended), `flat`, `realistic` | `explainer`: each part one clean saturated colour, soft light, crisp dark outline |
| `angle` | `front` (default), `front34`, `side` | gears read best from the `front`; `front34` shows thickness but a big gear can hide a small one |
| `camera` | `persp`, `ortho` | `ortho` for a flat, diagram-like look with no perspective |
| `colours` | `{"pinion": "#e4572e"}` | your own colour per part (explainer); the rest take the palette |
| `rpm` | in `mechanism` | slower is easier to follow: 10 to 30 rpm for a learner |

Palette (in part order): `#e4572e`, `#2e86ab`, `#f2c14e`, `#4cb944`, `#8e5bd6`, `#f28c28`,
`#17bebb`, `#e84a8a`. Match your 2D design's colours with `colours` so the 3D parts look like yours.

## One complete request and answer

```python
from modelgen_client import ModelGen
mg = ModelGen(client="village")
job = mg.mechanism({"parts": [
        {"name": "pinion", "type": "spur_gear", "module": 2, "teeth": 12, "meshes_with": "wheel"},
        {"name": "wheel", "type": "spur_gear", "module": 2, "teeth": 24}],
    "rpm": 20, "direction": "clockwise"},
    outputs=["motion", "game"], style="explainer", angle="front", request_key="gears-ep3-pair")
```

The answer (trimmed; a real run took 160 s, $0):

```json
{"status": "done", "source": "part_tools", "passed": true, "cost_usd": 0,
 "assumed": ["pinion.width = 16.0 (8 x module)", "pinion.bore = 6", "wheel.bore = 12",
             "pinion.shift = 0.294 (so the small gear is not undercut)", "..."],
 "main": {"motion": "motion/explainer-persp/motion.json", "game": "game/spur-gear-pair-12-24_game.glb"},
 "mechanism": {"key": "…", "sizes_mm": {"wheel": {"pitch_diameter": 48.0, "tip_diameter": 50.824}},
               "colours": {"pinion": "#e4572e", "wheel": "#2e86ab"}},
 "files": {"motion/explainer-persp/frame_000.png": {"path": "C:\\…\\frame_000.png", "url": "…"}, "…": "…"}}
```

Other shapes of request, same pattern:

- **Compound train**: `{"name": "s1", "type": "shaft"}`, then two gears with `"on_shaft": "s1"`,
  one meshing with the input gear and the other with the output gear.
- **Planet-style ring**: `{"name": "ring", "type": "ring_gear", "module": 2, "teeth": 60}` and
  `{"name": "sun", "type": "spur_gear", "module": 2, "teeth": 18, "meshes_with": "ring"}`.
- **Rack and pinion**: `{"name": "pinion", "type": "spur_gear", "module": 2, "teeth": 16,
  "meshes_with": "rack"}`, `{"name": "rack", "type": "rack", "module": 2, "teeth": 20}`.
- **Bevel pair**: two `bevel_gear`s with the same module, one `meshes_with` the other.
- **Helical pair**: two `helical_gear`s with the same module and `helix_angle`.

## Using the frames

1. Read `motion.json`: `frames`, `fps` (24), `loop_seconds`, `repeat`.
2. Play `frame_000.png` … `frame_<frames-1>.png` in order and start again at `frame_000`: the loop
   is seamless (the frame after the last is the first). It is the shortest stretch after which
   every part looks as it began, so a plain pair loops in half a second: repeat it as long as your
   scene needs. A gear with a keyway or holes needs whole turns, so its loop is longer.
3. Place it: every frame is 1024 × 1024 with a transparent background and the same framing, so
   scale and place all frames together. `motion.json` `view` says where the camera stood.
4. Labels and arrows: `parts.json` gives, per frame, each part's `box_px` (x0, y0, x1, y1 from the
   top left) and `centre_px` (the gear's axis). Pin a label to `centre_px`; draw a turning arrow
   round it the way `parts` in `motion.json` says (`turns` per loop: + is counterclockwise seen
   from the front). The colour of each part is in `colour`.
5. To show a speed difference, say it with numbers from `motion.json`: the pinion makes
   `parts.pinion.turns`, the wheel `parts.wheel.turns` per loop.

## Time and usage

| path | time | Claude usage |
|---|---|---|
| numbers, built before (same numbers) | seconds | none |
| numbers, new | 1 to 3 minutes | none |
| words | 2 to 4 minutes | one short engineer call (about $0.05) |
| words, a part no tool makes (a housing) | 20 to 60+ minutes | the agent team |

`motion` adds about 10 to 30 seconds; `views` about a minute.

## Common mistakes

- Different modules on meshing gears: refused ("cannot mesh: different module"). Gears that mesh
  share one module.
- A bore too big for a small gear: refused with the largest bore that fits.
- Leaving out `module` or `teeth`: refused with the gear named. Words go in `object`, numbers in
  `mechanism`; do not put numbers only in `notes`.
- Expecting a long loop: the loop is short on purpose; repeat it.
- Asking for `front34` for a pair of very different sizes: the big gear can hide the small one;
  use `front`.
- Changing only a colour: send `colours`; it does not rebuild the model.
