# ModelGen: the characters path

Written 2026-11-20. The user approved the whole change list below with one "approve" on 2026-11-20.
That approval covers only what is listed; anything else goes back to the user first. Finish each
stage fully (tools, tests, one real job, pictures next to real references), report briefly, commit
per stage by file name (never `git add -A`), push or release only when asked. Another session may
be editing car files, `director.py` and `pipeline.py` at the same time: stage only this plan's hunks.

## The goal

The user's words: "create a characters path from scratch to achieve high quality real characters
like persons old young baby girl woman cool guy nerd cartoon real in a specific direction like
dragon ball style or attack on titan style ... the characters should be animated with a small built
in house physics engine ... built on ai to achieve the best models". Constraints from earlier
sessions: free only, AI allowed, game-ready (about 20k triangles), original designs (never copy a
named character; a general drawing style is fine).

## The approach

- **Body**: the MakeHuman base mesh and macro targets (CC0), as packaged in the Anny body model
  (NAVER, Apache-2.0 code, CC0 assets, `assets_cc0/anny_body`). Newborn to 90 years, sex, muscle,
  weight, height, proportions, ancestry, blended exactly as Anny/MakeHuman do. Only the free assets
  are used; Anny's non-commercial SMPL/SMPL-X parts are never installed.
  *Change from the posted plan:* the network ran at about 13 KB/s, so instead of installing PyTorch
  and Anny (hundreds of MB), only the 48 MB Anny wheel was downloaded (SHA-256 checked against
  PyPI) and its body maths re-implemented in numpy inside Blender (`bpy_lib/character_body.py`).
  Fewer downloads than planned, the same body.
- **Skeleton**: the 53-bone game-engine rig of that data (pelvis, spine_01..03, neck_01, head,
  clavicle, upperarm, lowerarm, hand, five three-bone fingers, thigh, calf, foot, ball), joints
  placed from the shaped mesh, MPFB2's painted weights, at most four per vertex.
- **Design**: the `character_designer` agent turns words into a character sheet
  (`tools/character.py`); a program can send the sheet itself (`character` in a `/v1` job), then no
  agent runs.
- **Hair, clothes, faces, styles** (stage 2), **clips** (stage 3), **physics** (stage 4),
  **text-to-motion** (stage 5) as listed below.

## Change list (approved 2026-11-20)

### Stage 1: body and skeleton
1. `CHARACTERS_PLAN.md` (this file).
2. `PLAN.md`: characters may use rigging, animation and local AI models.
3. `modelgen/tools/character.py`: character sheet, validation, catalogue.
4. `modelgen/bpy_lib/character_body.py`: the body and skeleton from the sheet.
5. `modelgen/agents/character_designer.md`, its caps line and a `characters:` section in `config.yaml`.
6. `modelgen/families.py`: person words win outright.
7. `modelgen/pipeline.py`: `_try_character` / `_run_character` / `_report_character`, kind `character`,
   no ready-made catalogue for people.
8. Exports keep rigs: `bpy_lib/rig.py`, `bpy_lib/helpers.py`, `bpy_lib/game.py`, step kinds in
   `tools/blender.py`.
9. `modelgen/web/viewer.js`: clip picker.
10. Source `character` in `modelgen/jobs.py`, `modelgen/studio_state.py`, one label in `map/js/app.js`.
11. `tests/test_characters.py`.
Finished when a baby, girl, woman, old man, cool guy and nerd stand side by side, rigged, next to
real references.

### Stage 2: hair, clothes, faces, styles
12. `bpy_lib/character_parts.py`: hair and garments fitted to the body, weights taken from it.
13. Agents `character_hair.md`, `character_tailor.md`, with caps.
14. `modelgen/character_styles.yaml`: realistic, cartoon, shonen-anime, gritty-anime profiles.
15. `bpy_lib/materials.py`: skin, eyes, cel shading with outlines.
16. `web/viewer.js`: toon shading and outlines.
17. `modelgen/rubric_character.yaml` and the rubric choice in `tools/agent_tools.py`.
18. `bpy_lib/measure.py`, `tools/checks.py`: weights, bone count, clothes through the body.
19. `bpy_lib/game.py`: LODs keep their weights.
20. `agents/brief.md`, `critic.md`, `director.md` for characters.
Finished when the six people are dressed and haired, and one request is shown in all four styles.

### Stage 3: animation clips
21. `assets_cc0/animations/` (free clip libraries).
22. `bpy_lib/retarget.py`: library clips onto the skeleton as named clips.
23. `tools/clips.py`: clip catalogue.
24. API: `clips` field, `clips.json`, `GET /v1/characters` (`jobs.py`, `api_v1.py`, `modelgen_client.py`).
25. `tools/render.py`: a frame strip per clip.
26. `agents/animator.md` chooses clips for characters.
Finished when a character plays at least ten named clips in the viewer and in GLB and FBX.

### Stage 4: in-house physics
27. `modelgen/physics/`: particles, spring chains, XPBD distance/bend, capsule colliders, ragdoll limits.
28. `bpy_lib/physics_bake.py`: hair, cloth and accessory motion baked into every clip.
29. `web/physics.js`: the same engine live in the viewer.
30. Spring settings in the GLB extras.
31. A check across each clip that hair and cloth never pass through the body.
32. `tests/test_physics.py`.
Finished when hair and a coat swing in walk and run, and a ragdoll drop works in the viewer.

### Stage 5: text-to-motion
33. `tools/text_motion.py`: NVIDIA Kimodo locally, its output as a named clip.
34. `characters.text_motion` switch in `config.yaml`, falling back to the clip library.
Finished when "does a spinning kick" makes a new clip, or it is reported that Kimodo cannot run here.

### Across all stages
35. Docs each stage: `API.md`, `README.md`, `CHARACTERS_GUIDE.md`.
36. Downloads, all free; each size reported; stop and ask over 3 GB.
37. Commits per stage by file name, no push; ask before committing the user's own uncommitted files.

Not in this plan: interiors, facial animation and lip sync, copies of named characters, neural
image-to-3D models (21-24 GB of video memory).

## Progress

- Stage 1: built and tested (46 tests in `tests/test_characters.py`, real Blender jobs included).
