# ModelGen: mechanical parts for animated learning content

Written 2026-11-18 at the end of the session that built the `/v1` API and released 1.1.0. This file
is the plan a new session executes. It is a working note: **do not commit it** (add files to git by
name, never `git add -A`).

## How to run this plan (read first)

1. The user's global rule applies in full: before any `Edit` or `Write` on source, post the change
   list and wait for the user's next message to be the literal word **approve**.
2. The user asked for **one approval for the whole plan**. So: post the "Change list" section below
   (all five stages, as written), wait for **approve** once, then carry out every stage in order
   without asking again per stage.
3. That one approval covers only what is listed here. Anything found on the way that is not on the
   list (a different approach, an extra file, a change to cars, a release) goes back to the user
   first.
4. Finish each stage completely before starting the next: tools, tests, one real job, and a
   picture of the result next to a photo of the real part. Show the user that picture and say
   plainly how it compares to the real part. Never call a result good only because it is better
   than the last one. If a stage's result is not right, fix it before moving on.
5. Keep reports short: a few lines per stage, with the picture.
6. Commit after each finished stage (files by name). Push and release only if the user asks.

## The goal

The Village (`..\PlatformExperimental`, 2D videos drawn with PIL and HTML templates) makes animated
learning videos. Its animator draws parts such as gears flat. ModelGen should give it the same
parts as accurate 3D models that move correctly, delivered as looping transparent picture
sequences that catch the eye, plus exploded views, cutaways and the data to label them.

The user's words: no full cars, just the parts (gears, turbochargers and every other part); the
agents coordinated in a smart way; built the most efficient way possible; high quality; and a guide
the Village can follow so it sends everything ModelGen needs for the best result.

What the Village gets for one request, e.g. "spur gear pair 12/24", "turbocharger", "piston and
crank":
- an accurate 3D model whose parts move correctly (GLB with the animation, game files as today);
- transparent PNG sequences of it working, in a seamless loop;
- an exploded view and a cutaway (from stage 2);
- `parts.json`: where every part is in every frame, for labels and arrows.

Cars and the car generator are not touched by anything here.

## What exists today (checked in code on 2026-11-18)

- `modelgen/families.py`: `FAMILIES` (container, device, furniture, building, scene, vehicle,
  character, general). Each has `what`, `words` (regex), `budget`, `kit`, optional `views`,
  `studio`, and rule texts `plan`, `build`, `materials`, `review`. `guess()` picks by the last
  matching word; `_ORDER` lists the families tried. There is **no mechanical family**.
- `modelgen/bpy_lib/kits.py` (as `k` in builder code): vessel, handle, device_shell, wheel,
  vehicle_body, hood, walls, roof, tree, bench... **No gear, blade, piston, bearing or spring tool.**
- `modelgen/bpy_lib/motion.py` (as `m` in the animator's script): `loop`, `travel`, `spin(objs,
  axis, turns, rpm, reverse)`, `bounce`, `marker`, `report`. Spins are whole turns per loop so the
  loop is seamless. There is no meshing drive, no slide, no crank.
- `modelgen/pipeline.py`: `_run` tries the car generator (`uses_remix`, `at.looks_like_car`), then
  the ready-made catalogue (`_try_library`), then the agent team: `_research`, `_brief`,
  `_architect`, builders, assembler, materials, animator (when the spec has motion), `_final_build`
  (Blender build, measure, `checks.evaluate`, renders, game export), `_critic`, librarian.
  `_options_text(project)` is the text every planning agent gets about a model's options.
- `modelgen/tools/blender.py`: Blender jobs are lists of steps run by `BOOTSTRAP`; render sets
  include `asset_views` (the API's angle pictures). `modelgen/bpy_lib/rig.py`:
  `render_asset_views` (transparent views, shadows, turntable; styles `realistic` and `flat`,
  cameras `persp` and `ortho`), `_flat_style`, `_picture_box`.
- `modelgen/jobs.py` and `modelgen/api_v1.py`: the `/v1` API (see `API.md`). Jobs are unattended,
  one at a time; outputs `game`, `full`, `views`; `version_files`, `render_views`, `_reuse`
  (caster-confirmed), `find_models`. `modelgen_client.py` is the copy-in client.
- `modelgen/tools/checks.py`, `modelgen/bpy_lib/measure.py`: the measured gates of a version.
- Rubrics: `modelgen/rubric.yaml`, `rubric_car.yaml`, `rubric_game.yaml`; `at.rubric_file(plan,
  spec)` picks one.
- The Village's 2D designs are JSON "vector designs" (example: its
  `data\library\cast\metal-gear-fan\design.json`).

Known before starting:
- "car engine piston" would be sent to the car generator today (`looks_like_car`).
- The ready-made Poly Haven "Fire Hydrant" is two hydrants in one model (not part of this plan;
  the user knows).
- 12 tests already fail without any change: 7 in `tests/test_car_types.py`, 4 car-body tests in
  `tests/test_bpy_models.py`, 1 in `tests/test_blueprint.py`. Do not count them against this work,
  and do not fix them here.
- Test a real job against a separate copy: `.claude/launch.json` has `modelgen-api-e2e` (port
  8777, its own data folder in a scratchpad; point it at this session's scratchpad). Never use
  port 8765 (the Village) and leave 8770 for the user's own ModelGen.
- Releases are signed (`installer\Publish-Release.bat`); never regenerate the signing key.

---

# Change list (post this, then wait for "approve")

## Stage 1: gears, and the foundation everything else reuses

1. **A "mechanical" family** (`modelgen/families.py`)
   - Words that select it: gear, cog, sprocket, pulley, bearing, shaft, piston, crankshaft,
     camshaft, turbo, turbine, impeller, compressor wheel, spring, bolt, nut, valve, gearbox,
     differential, and similar.
   - A mechanical word wins over a car word, so a part is never sent to the car generator
     (`pipeline.uses_remix` / `at.looks_like_car`).
   - Mechanical requests skip the ready-made catalogue (`_try_library`): a downloaded model cannot
     be animated part by part.
   - Rules for each agent: the architect plans from engineering numbers; builders call the part
     tools and never hand-shape teeth; materials uses the slots the tools make (`tooth_face`,
     `hub`, `bore`); the critic judges tooth shape and meshing first.
   - Flat views: front and side.

2. **Gear tools** (`modelgen/bpy_lib/kits.py`), each from real numbers, at real size, one closed solid
   - `spur_gear`: module, teeth, width, bore, pressure angle; hub, spokes or lightening holes,
     keyway, edge chamfer. True involute teeth.
   - `helical_gear`: the same with a helix angle; herringbone option.
   - `ring_gear`: internal teeth.
   - `rack`: a straight toothed bar.
   - `bevel_gear`: cone angle from the pair's ratio.
   - `shaft`: steps, keyway, chamfers.
   - `gear_pair_layout`: from two tooth counts and a module, the exact centre distance and the
     second gear's starting angle, so a tooth sits in a gap from frame 1.

3. **The engineer step** (new `modelgen/agents/engineer.md`, wired in `modelgen/pipeline.py`,
   tools in `modelgen/tools/agent_tools.py`, caps read from config with a code default)
   - Runs for the mechanical family only, after research and before the spec.
   - Produces a "mechanism sheet": each moving part with its key numbers; how parts connect
     (meshes with, shares a shaft with, which one drives); each part's motion (axis, speed
     relative to the driver, direction).
   - The sheet goes to the architect, builders, assembler, animator and critic, so every agent
     works from the same numbers.

4. **Motion from the sheet** (`modelgen/bpy_lib/motion.py`, `modelgen/agents/animator.md`)
   - New `m.mesh_drive(driver, driven, ratio, same_direction=False)`: a pair turning at the exact
     ratio, in opposite directions (same direction for belts and internal gears).
   - The loop length is chosen so every part ends where it began.
   - The animator takes speeds from the sheet and does not choose them.

5. **The mesh check** (`modelgen/tools/checks.py`, `modelgen/bpy_lib/measure.py`, a step in
   `modelgen/tools/blender.py`)
   - Measured at 12 moments across the loop: meshing teeth do not cut into each other beyond a
     small tolerance; they do not float apart; the centre distance matches the sheet; nothing else
     passes through anything while moving.
   - A failure names the pair and goes back to the assembler (spacing, angle) or the animator
     (ratio, direction).

6. **Moving pictures** (new API output `motion`: `modelgen/bpy_lib/rig.py`,
   `modelgen/tools/blender.py`, `modelgen/jobs.py`)
   - `motion/<style>-<camera>/frame_000.png ...`: one seamless loop, transparent, 1024 px, 24
     frames a second, from a chosen angle (`front`, `front34`, `side`).
   - `motion.json`: frame count, frame rate, loop length, camera, and how to repeat the frames.
   - `parts.json`: per frame, each part's name, colour, box in the picture and centre point.

7. **The "explainer" style** (a third `style` value, `modelgen/bpy_lib/rig.py`, `modelgen/jobs.py`)
   - Each part its own clean saturated colour from a fixed palette of eight that stay distinct;
     the request can set a colour per part.
   - Soft even light, light shading for form, a crisp dark outline, no grime.
   - Works for stills and motion frames.

8. **Critic and rubric** (new `modelgen/rubric_mechanical.yaml`, `modelgen/agents/critic.md`,
   `at.rubric_file`)
   - The critic sees the motion strip as well as the stills.
   - Scores: tooth shape against reference photos, meshing, proportions, the motion reading
     clearly, the look.

9. **The fast path: tool-built parts need no agents** (`modelgen/pipeline.py`, `modelgen/jobs.py`,
   new `modelgen/tools/mechanism.py`)
   - A job can carry a structured `mechanism` (part types and numbers). When every part has a
     tool, ModelGen builds straight from the tools, runs the checks and renders: about 1 to 3
     minutes, no Claude usage (like the car generator and ready-made paths; `source:
     "part_tools"`).
   - When numbers are missing, the engineer fills them in with one short call, then the fast path
     does the rest.
   - The agent team builds only a part that has no tool (a custom housing); tool-built parts inside
     it still come from the tools.
   - On the fast path the measured checks stand in for the critic.

10. **Exact reuse** (`modelgen/jobs.py`)
    - A tool-built part is stored under its numbers; the same numbers asked for again return at
      once, with no caster agent.

11. **Efficient rendering** (`modelgen/bpy_lib/rig.py`, `modelgen/jobs.py`)
    - A part whose picture repeats (a plain gear pair repeats every tooth) is rendered only for
      that short stretch; `motion.json` says to repeat it. A part with a visible mark (keyway,
      holes) gets the full turn.
    - The explainer style renders with Blender's fast engine; a job's frames use as few Blender
      runs as possible, two at a time.

12. **Several parts in one job** (`modelgen/jobs.py`, `modelgen/api_v1.py`)
    - A job can ask for a set of parts for one video; they share the style, lighting and palette.

13. **A live catalogue and clear answers** (`modelgen/api_v1.py`, `modelgen/jobs.py`)
    - `GET /v1/parts`: every part type the tools can build, with its fields, units, allowed ranges
      and defaults.
    - A request that cannot work is refused with the field named ("gear B has no tooth count",
      "these gears cannot mesh: different module").
    - Every value ModelGen filled in is listed in the job under `assumed`.

14. **Documents** (`API.md`, `modelgen_client.py`, new `VILLAGE_GUIDE.md`)
    - `API.md` and the client: the new fields and outputs (nothing existing changes).
    - `VILLAGE_GUIDE.md`, for the user to give to the Village session: the two ways to ask (exact
      numbers: fast, free, exact; plain words: slower, guessed); a checklist per part type of what
      to send; the choices for the video (style, angle, loop speed, size, motion / exploded /
      cutaway); one complete request and answer per part type; how to place frames with
      `parts.json`, loop them and attach labels; what each path costs in time and usage; common
      mistakes. Gear sections complete in this stage; each later stage adds its part.

15. **Tests** (new `tests/test_mechanical.py`, additions to `tests/test_api_v1.py`)
    - Geometry: the tooth count asked for; tip and root diameters match the standard formulas; one
      closed solid.
    - Layout and motion: `gear_pair_layout` gives the textbook centre distance; `mesh_drive` gives
      the right ratio and direction; first and last frames match.
    - The mesh check passes a correct pair and fails one set too close, too far, or at the wrong
      ratio.
    - Fast path: a `mechanism` job builds with no agent run; missing fields are refused by name;
      `assumed` lists filled-in values; exact reuse returns the stored part; `GET /v1/parts`.
    - A mechanical word beats the car generator and the ready-made catalogue.
    - One real Blender test: a 12/24 pair renders a short transparent loop.

**Stage 1 is finished when** a gear pair asked for with numbers comes back in a few minutes with
no agent usage, its teeth mesh through the whole loop, the frames have been shown to the user next
to a photo of a real gear pair, and `VILLAGE_GUIDE.md` is ready to hand over. Also run one job
asked for in plain words ("two gears, the small one drives the big one") to prove the engineer
path.

## Stage 2: turbocharger

16. **Tools** (`modelgen/bpy_lib/kits.py`): `compressor_wheel` and `turbine_wheel` (hub profile,
    blade count, sweep and twist, splitter blades), `volute` (the snail housing with inlet and
    outlet flanges), `bearing` (balls, races, cage).
17. **`exploded` output**: a sequence where the parts slide apart along the shaft in assembly order
    and back; the order comes from the mechanism sheet.
18. **`cutaway` output**: the housing sliced open on a chosen plane, cut faces in a flat accent
    colour; as stills and as a motion loop with the wheels spinning inside.
19. **Air-flow paths** in `parts.json` (intake, compressed, exhaust) for the Village to draw; not
    rendered.
20. **Check**: blades clear the housing through a full turn.
21. Catalogue entries, guide section, tests, and one real job judged against real cutaway photos.

## Stage 3: piston and crank

22. **Tools**: `piston` (crown, ring grooves, skirt, pin bore), `piston_rings`, `connecting_rod`,
    `crankshaft` (throws, counterweights, journals), `cylinder` (liner or a simple block section).
23. **Motion**: `m.crank_slider`: the crank turns, the rod swings, the piston slides, computed
    exactly from crank radius and rod length.
24. **Check**: the pin stays in its bore, the rod never cuts the cylinder, the piston stays inside
    the liner.
25. Catalogue entries, guide section, tests, and one real single-cylinder job (with a cutaway)
    judged against a real one.

## Stage 4: the remaining parts

26. One tool, its motion where it moves, its check, its catalogue entry, its guide section, its
    tests and one real job each, judged against the real part:
    `spring` (compression, extension); `bolt`, `nut`, `washer` with real thread profiles; `pulley`
    and `belt`; `sprocket` and `chain`; `cam` and `follower`; `valve` (poppet); `planetary_set`
    (sun, planets, ring, carrier; motion from the planetary ratio); `worm` and `worm_wheel`.

## Stage 5: the Village's 2D design in, 3D out

27. A job can attach one of the Village's vector designs (its JSON file) as `design`.
28. ModelGen reads its outline, tooth or blade count, proportions and colours; the part's numbers
    are set to match, so the 3D part lines up with the 2D one the Village's animator drew.
29. Guide section: how the Village swaps its flat part for the 3D sequence at the same size and
    place.
30. Finished when the Village's `metal-gear-fan` design comes back as a matching 3D part.

## Across all stages

- The API only gains fields; nothing existing changes. `API.md`, `modelgen_client.py`,
  `GET /v1/parts` and `VILLAGE_GUIDE.md` are updated in every stage.
- After each stage: a short note for the user to paste into the Village session saying what is new.
- Not part of this plan: cars, the two-hydrant ready-made model, the 12 old failing tests,
  releases (ask the user when a stage is done).
