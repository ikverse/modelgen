# ModelGen API (v1)

Other programs on this PC can ask ModelGen for a 3D object and pick up its files when it is ready.
The Village is the first one. ModelGen must be running (`ModelGen.bat`); the API lives on the same
server as the page, `http://127.0.0.1:8770/v1`.

The quickest way in is [`modelgen_client.py`](modelgen_client.py): copy that one file into your
project (standard library only).

```python
from modelgen_client import ModelGen

mg = ModelGen(client="village")
if mg.health()["ok"]:
    job = mg.get("fire hydrant", notes="old cast iron, red", size={"h": 80, "unit": "cm"},
                 outputs=["game", "views"], style="flat", timeout=3600)
    if job["status"] == "done" and job["passed"]:
        glb = job["files"][job["main"]["game"]]["path"]
```

## Access

- Only programs on this PC: a request from another address is refused (403).
- Never from a web page: a request with an `Origin` header is refused (403), so a website open in
  the browser cannot start builds.
- Every call except `/v1/health` needs the key, sent as the `X-ModelGen-Key` header. ModelGen makes
  it on first start in `data\api_key.txt` (in the ModelGen folder); `modelgen_client.py` reads it
  from there. A missing or wrong key gets 401.

Errors come back as `{"detail": "what is wrong"}` with status 400 (a bad request), 401, 403 or 404.

## How a job runs

1. Jobs are built **one at a time, oldest first**. A job also waits while any other build runs,
   including one started on ModelGen's page.
2. With `reuse` on (the default), ModelGen first looks for an accepted model it made before. A small
   agent (about $0.05) checks that a match really is the object asked for; then the job is done in
   seconds with `source: "existing"` and costs nothing more.
3. Otherwise a new model is made, the same way as from the page:
   - an object in the free ready-made catalogue is made from it in about a minute (`ready_made`);
   - a car is made by the car generator in under a minute (`car_generator`);
   - anything else is built by the agent team (`built`), roughly 20–60+ minutes per version.
4. **Nobody is asked anything.** An agent that would ask a question decides itself. Reaching the
   job's `budget_usd`, or today's spending limit, stops the build instead of asking.
5. After each version the next one is built with the critic's changes, until a version passes
   (score 80 or more with every check) or `max_versions` versions are made. The best version is
   **accepted automatically**.
6. `passed` says whether that version's score is at least your `min_score` (a ready-made or
   generated model has no score: then it is whether its checks passed). Decide yourself whether to
   use a model that did not pass.
7. If `views` were asked for, the angle pictures are rendered last (a few minutes).
8. Every model is made generic: no brand names, logos or a brand's signature shapes.

**The Claude usage limit.** The agents run on this PC's Claude subscription through ClaudeIn;
`cost_usd` is what the runs would cost, an estimate. When the usage limit is reached the job goes to
`waiting_for_usage` and **the whole queue pauses**, trying again every 15 minutes until the limit
resets. Nothing fails because of it.

**Restarts and updates.** A job that was building when ModelGen closed goes back in the queue and
carries on with its model; queued jobs stay queued. While jobs are queued, ModelGen's page asks
before installing an update.

**On ModelGen's page** a job's model shows in the model list as "from village · ...". The page and
its chat never jump to it on their own; the person can open, stop or delete it like any other.

## Endpoints

### `GET /v1/health` (no key)

```json
{"ok": true, "app": "ModelGen", "api": "v1", "version": "1.0.1", "blender": true, "claudein": true,
 "queued": 2, "building": "j_3f2a9c1b7d4e", "paused_for_usage": false, "retry_at": null}
```

If this call fails, ModelGen is not running: fall back to something else. Do not run
`ModelGen.bat` from another program blindly; it stops a ModelGen that is already running.

### `POST /v1/jobs`

```json
{"object": "fire hydrant",
 "client": "village",
 "notes": "old cast iron, red",
 "size": {"w": 30, "d": 30, "h": 80, "unit": "cm"},
 "references": ["C:\\photos\\hydrant.jpg", "https://example.com/hydrant.png"],
 "request_key": "news-0412-hydrant",
 "outputs": ["game", "full", "views"],
 "style": "realistic", "camera": "persp",
 "reuse": true, "max_versions": 2, "min_score": 70, "budget_usd": 6}
```

| field | | |
|---|---|---|
| `object` | required | what to make, in a few words (up to 200 characters) |
| `client` | required | your program's name: lowercase letters, digits, `-`, `_` |
| `notes` | | details: colour, style, era, condition (up to 2000 characters) |
| `size` | | any of `w`, `d`, `h` (or `width`, `depth`, `height`) and `unit` `mm`, `cm` or `m` (default `cm`) |
| `references` | | up to 4 images: full file paths on this PC or http(s) links; they are copied in |
| `request_key` | | your own id for this request; sending the same key again returns the same job |
| `outputs` | `["game", "full"]` | which files you want (see Files) |
| `style` | `realistic` | angle pictures: `realistic` (photo studio) or `flat` (flat colours, dark outline) |
| `camera` | `persp` | angle pictures: `persp` (15° above) or `ortho` (flat, side-on, no shadows) |
| `reuse` | `true` | use a model made before when one fits |
| `max_versions` | `2` | 1 to 5 |
| `min_score` | `70` | 0 to 100: the score `passed` needs (the page shows it divided by 10) |
| `budget_usd` | the page's per-model limit | the spending limit for this model |

Answer: the job (as below) with `status: "queued"` and its `position` in the queue. An identical
request from the same program that is still queued returns that job instead of queueing twice.

### `GET /v1/jobs/{id}`

```json
{"id": "j_3f2a9c1b7d4e", "client": "village", "request_key": "news-0412-hydrant", "object": "fire hydrant",
 "status": "done", "step": "", "position": null,
 "project": "fire-hydrant", "version": 2, "score": 84, "passed": true, "source": "built",
 "cost_usd": 4.12, "error": null, "notes": [], "retry_at": null,
 "created": "2026-11-18T14:02:11", "started": "2026-11-18T14:02:12", "finished": "2026-11-18T14:51:40",
 "request": {"object": "fire hydrant", "notes": "...", "size": {"height": 80, "unit": "cm"}, "...": "..."},
 "main": {"game": "game/fire-hydrant_game.glb", "full": "exports/fire-hydrant.glb",
          "views": "views/realistic-persp/views.json"},
 "files": {"game/fire-hydrant_game.glb": {"url": "http://127.0.0.1:8770/files/fire-hydrant/v2/game/fire-hydrant_game.glb",
                                          "path": "C:\\...\\ModelGen\\projects\\fire-hydrant\\v2\\game\\fire-hydrant_game.glb"},
           "...": "..."}}
```

| `status` | |
|---|---|
| `queued` | waiting its turn (`position` 1 is next) |
| `building` | being made; `step` says what is happening now |
| `waiting_for_usage` | paused for the Claude usage limit until `retry_at` |
| `done` | finished: `files`, `score`, `passed` are set |
| `failed` | `error` says why |
| `cancelled` | cancelled by you |

`notes` lists anything worth knowing that did not fail the job (a later version failed and an
earlier one was kept; the angle pictures could not be made).

`files` maps each file's path inside the version folder to its `url` and its `path` on disk. A
program on this PC can simply copy the file from `path`. `main` names the file to start from for
each output.

### Other calls

- `GET /v1/jobs?client=village&status=queued,building&limit=50`: jobs, newest first.
- `POST /v1/jobs/{id}/cancel`: takes a job out of the queue or stops its build (what was built is
  kept on ModelGen's page).
- `GET /v1/models?q=fire hydrant&limit=10`: `{"models": [...], "ready_made": [...]}`. `models` are
  accepted models that match, best first, each with `name`, `title`, `source`, `version`,
  `score`, `passed`, `size_m`, `views` (the angle picture sets it has), `files` and `main`.
  `ready_made` are catalogue models not made yet; a job for that object makes one in a minute.
- `GET /v1/models/{name}`: one accepted model with all its files.

## Files

Inside the version folder `projects\<model>\v<n>\`:

| output | files |
|---|---|
| `game` | `game/<model>_game.glb` within the object family's triangle limit, `_LOD1.glb`, `_LOD2.glb` (lower detail), `_collision.glb`, `game.json` (triangles, size) |
| `full` | `exports/<model>.glb`, `.fbx`, `.obj`, `.stl` at full detail, and `model.blend` |
| `views` | `views/<style>-<camera>/`: transparent 1024 px PNGs `front`, `front34`, `side`, `back34`, `back`, `top`, a 24-frame turntable `turntable_00` … `turntable_23`, and (persp only) `<view>_shadow.png` for every view but top, plus `views.json` |

Angle pictures: azimuth 0 looks at the model's front, 90 at its side, 180 at its back; the
turntable starts at the front and turns 15° a frame. The shadow pictures hold only the soft shadow
on the ground (transparent elsewhere): draw the shadow, then the model, at the same place.

`views.json`:

```json
{"style": "flat", "camera": "persp", "size_px": 1024,
 "size_m": {"width": 0.3, "depth": 0.3, "height": 0.8},
 "views": {"front": {"azimuth_deg": 0, "elevation_deg": 15, "camera": "persp", "lens_mm": 50.0,
                     "distance_m": 2.1, "box_px": [312.0, 40.5, 712.0, 990.2], "ground_px": 990.2},
           "...": "..."},
 "turntable": {"frames": 24, "elevation_deg": 15, "first_azimuth_deg": 0},
 "shadows": true, "files": ["back.png", "back34.png", "..."]}
```

`box_px` is where the model is in the picture (x0, y0, x1, y1 from the top left) and `ground_px`
the height of its lowest point: put that row on your ground line, and scale the picture so the
model's real `size_m` matches your scene. An `ortho` view also gives `pixels_per_metre`.
