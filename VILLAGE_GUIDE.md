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
| `outputs` | `motion`, `views`, `game`, `full` | `motion` is the loop; `views` the stills; `game`/`full` the 3D files |
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
