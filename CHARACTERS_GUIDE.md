# Asking ModelGen for characters: a guide for client programs

ModelGen makes people as rigged, animated game characters: any age from a baby to an old person,
dressed, with hair, in a realistic or a drawn style, with animation clips and hair and clothes that
swing. This guide says what to send so the result is right first time. The full contract is in
`API.md` (section Characters); the copy-in client is `modelgen_client.py`.

ModelGen runs on this PC at `http://127.0.0.1:8770/v1`. Check `GET /v1/health` first; every other
call needs the key from ModelGen's `data\api_key.txt` as `X-ModelGen-Key` (the client does this).

## Two ways to ask

| | a character sheet (`character`) | plain words (`object`) |
|---|---|---|
| what you send | the person's numbers and look | "an old grandma with a bun and glasses" |
| who decides | you | the designer, tailor and hair agents (listed back in `assumed`) |
| Claude usage | none | three short calls (about $0.05 together) |
| time | about 5 minutes with the 12 everyday clips | a minute more |
| exact | the same sheet gives the same character | close to what you described |

`GET /v1/characters` lists every field, the garment and hair types, the face sliders, the styles
and the clip names. Ask it rather than hard-coding this guide's lists.

## The sheet

```json
{"client": "mygame", "outputs": ["game", "full"],
 "character": {"name": "cool guy", "age": 26, "sex": "male", "build": "athletic",
               "style": "realistic",
               "hair": {"style": "undercut", "colour": "#1d1612"},
               "outfit": [{"type": "tshirt", "colour": "#f2f2f2"}, {"type": "jeans", "colour": "#2f3e5c"},
                          {"type": "sneakers", "colour": "#f5f5f5"}, {"type": "leather_jacket", "colour": "#1c1c1e"}],
               "face": {"chin-width-decr-incr": 0.5},
               "clips": ["Idle_Loop", "Walk_Loop", "Jog_Fwd_Loop", "Punch_Jab"]}}
```

- **Who**: `age` in years (0.5 a baby, 8 a child, 80 an old person), `sex`, `build`, `height_cm`
  (leave it out for the median of the age and sex), `ancestry`, `skin`, `eyes`.
- **Look**: `outfit` (one inner top, one outer top, one bottom or a dress or a onesie, one pair of
  shoes; skirts and dresses take a `length`), `hair`, `brows`, `accessories` (glasses), `face`
  sliders (-1..1; small values look natural).
- **Style**: `realistic`, `cartoon`, `shonen_anime` or `gritty_anime`. A drawn style is a general
  look (big eyes, flat cel colour, an outline); ModelGen never copies a named character from a show.
- **Clips**: leave out for 12 everyday clips; `"all"` for all 42; a list of names; `[]` for none.

Every value ModelGen fills in is listed in `assumed`. A sheet that cannot be built is refused with
the field named.

## What you get

- `game/<name>_game.glb` (20 000 triangles) and `_LOD1`, `_LOD2` (half, a fifth), each with the
  skeleton, the skin weights and every clip; `exports/` the full-detail GLB and FBX.
- The **53-bone game skeleton** (Unreal-style names: `pelvis`, `spine_01`..`03`, `upperarm_l`,
  fingers...), Z up in Blender (Y up in the GLB), facing the front, feet on the floor at the origin,
  real scale in metres.
- Swinging parts hang from **spring chains** (`spring_<piece>_<chain>_<bone>`), baked into every clip.
  To run them live instead, read `mg_spring` on each chain's root bone and `mg_collider` on the body
  bones (see `API.md`), and drop the baked spring keys.
- A drawn style's materials carry `mg_toon_colors`, `mg_toon_stops`, `mg_toon_light` and
  `mg_outline`: draw them as cel shading with an outline of that width.
- `clips.json` (the clips, their frames, 30 a second) and `renders/sheet_clips.jpg` (four frames of
  each).

## Using the clips

Clips are in place: they do not move the character forward. Move it in your game yourself, at a
speed where the feet do not slide (set it by eye once per clip; a shorter character takes shorter
steps). The loops (`*_loop`) repeat seamlessly; `jump_start`, `jump_loop` and `jump_land` play in
order.

## Common mistakes

- Two outer tops, a dress with trousers, a onesie with anything but shoes: refused, with the reason.
- Face slider values near 1 or -1 on many sliders at once look like caricatures: keep most under 0.6.
- `clips: "all"` triples the build time; ask for the clips your game uses.
- A long skirt or coat with deep knee lifts (a sprint, a kick) can show the leg through the cloth.
