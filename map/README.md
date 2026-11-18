# ModelGen studio page

The ModelGen web page. The Studio screen is one model's workspace: the chat with Hange is docked
on the left, and the open model's work is split into four stages on the right, each its own tab:

| Stage | What it holds |
|---|---|
| **Brief** | The request, the size and budget, the reference images, what the research found (real size, proportions, features, colours, what was guessed, the photos it kept), the spec in short, and how the model is made: a ready-made model, the car generator, a car body from a sheet, or the team part by part. |
| **Build** | The studio map (nine agents drawn as characters in a 3D-model production studio, animated by their state) at full size, and beside it the run's steps, the live 3D preview of the part being built, and every agent's tool calls. Clicking a character opens its panel over the map. |
| **Review** | The large 3D viewer with the versions in a strip under it, and beside it Annie's review (score, summary, criteria, checks, renders, the changes she asked for), what the next version will change, and the decision bar: Accept, Build next version, Add my comments. |
| **Export** | The selected version's files (GLB, FBX, OBJ, STL, .blend, all of it as a zip), its game-ready files (game GLB, LOD1, LOD2, collision, with their triangle counts), and the 3D view for recolouring before saving a recoloured GLB. |

The stage follows the model by itself: a build that starts opens Build, a version that gets its
score opens Review, accepting opens Export. A stage you pick stays until the next of those, and
the page remembers the stage each model was left on. Each tab carries a state mark (working,
needs you, done, failed), and the top bar has the one progress line and a "Needs you" chip.

The chat shows the open model's messages; "All models" shows every message with a tag where the
talk moves to another model.

Everything here is plain HTML, CSS and JavaScript with no build step and no framework.

## Run it

The ModelGen engine serves this folder: start `ModelGen.bat` and the page opens at
<http://127.0.0.1:8770/map/>. `js/live.js` connects it to the engine.

Add `?demo=1` to the address to play the scripted demo instead (`js/demo.js` with
`mock.json`): a rocking chair run of about three minutes that loops, with no engine work and
no cost. Ctrl K then has a Demo group: pause, step, restart, a hand-off run, kickoff and
review meetings, force every agent into one state, and 1 to 3 parallel builders. The demo
also works without the engine, from any static server in this folder, for example
`python -m http.server 8791 --bind 127.0.0.1` and then <http://127.0.0.1:8791/?demo=1>.

## Files

| File | What it holds |
|---|---|
| `index.html` | The page: header, navigation bar, the Studio screen (chat column, model header, stage tabs and the four stage panes), the other screens (New model, Models, Parts library, Activity, Settings), the footer, the command menu, and the icon set. |
| `css/app.css` | The app's look and the docked layout: warm neutral greys, one blue accent for the main action and the selected stage, colour only for state (blue working, amber needs you, green done, red failed). Dark by default with a light theme. |
| `css/studio.css` | The map's colours for each theme, the screens and machines, and every character pose and animation (seated at a computer, the VR headset). |
| `js/cast.js` | The nine characters on one shared rig, with front, side and back views. Nobody carries props: the tech lives on the desks. Jean, the animator, works in the right-hand focus pod (the Animation suite). |
| `js/stations.js` | The studio as data: the rooms, every workstation and machine with its depth key, the screens and what each shows, doors, walkways, every spot, and which tool sends which character where. |
| `js/studio.js` | The motion engine: walkers, routing, depth sorting, studio life, hand-offs, meetings, the live screens and the GPU room, hover, click, zoom and pan. |
| `js/panel.js` | The agent panel and the hover card. |
| `js/app.js` | The shell: screens, the chat (one model's messages or all), the workspace (model header, stage tabs and which stage opens when, Brief, the Build side column, Review, Export), the 3D viewer's tools, the New model options, Settings, command menu, shortcuts, toasts, theme, `applyState` and `loadData`. |
| `js/live.js` | The link to the engine: the websocket, page data, and every action sent back (chat, answers, accept, changes, uploads, settings). Loaded unless the address has `?demo=1`. |
| `js/demo.js` | The scripted demo, loaded only with `?demo=1`. |
| `mock.json` | Demo data: the scripted run, chat, models, parts, activity, and the model under review. |

The 3D viewer itself is the engine's `modelgen/web/viewer.js` (three.js), which `live.js`
loads into the workspace.

### The 3D viewer

There is one viewer. It fills the left of the Review stage, moves to the Build stage's live
preview while a version builds (with only the large-view button), and to the Export stage for
recolouring. The toolbar along the top of the view has:

| Tool | What it does |
|---|---|
| Views | Front, back, left, right, top and 3/4 (keys 1 to 6), orthographic or perspective, fit to model, reset |
| Display | Shaded, wireframe, wireframe over shaded, clay (plain grey) and X-ray |
| Lighting, background, grid | Studio, soft or contrast lighting with an exposure slider; background to match the theme, light or dark; grid and axis gizmo on or off; spin speed; units (auto, mm, cm, m) |
| Dimensions | A box around the model with its width, depth and height |
| Measure | Click two points on the model for the distance between them |
| Section cut | Slices the model along X, Y or Z, with a slider for where and which side to keep |
| Model stats | Triangles, vertices, parts, meshes, materials and size |
| Parts | The large view with a list of the model's parts: show or hide each one, click to select, isolate one |
| Spin, screenshot | Turntable spin; save the view as a PNG |
| Play, pause | Only for a model with motion: plays the animator's loop (turning wheels, a bounce), draws smoke and steam at its markers, and slides the ground under a vehicle. It starts playing by itself unless the system asks for reduced motion |
| Large view, full screen | The view over the whole workspace (V, Esc to go back), or the whole screen |

Click a part in the view to select it; the chip at the bottom left can isolate or hide it.
Click an axis on the gizmo to look from that side.

## The data contract

The page never works out agent state itself. The engine calls one function with a full
snapshot whenever something changes:

```js
applyState({
  agents: [
    {
      id: "builder-2",             // unique per agent
      role: "builder",             // director | brief | architect | builder | assembler | materials | critic | librarian
      state: "waiting",            // idle | working | waiting | done | failed
      doing: "rail_right: thickness unclear",   // one line, shown under the character and in panels
      since: "2026-10-25T10:44:00Z",            // when the current state started
      tool: "write_part",          // optional: the tool being called, shown in the panels
      spot: "run_blender",         // optional: the map's name for where that tool is used (see Tool to spot); else tool picks it
      calls: [ { tool: "run_blender", text: "rail_left built", at: "2026-10-25T10:43:00Z", ok: true } ],  // optional, recent first or last, up to ~12
      question: { text: "Rails at 40 mm or 45 mm thick?", options: ["40 mm", "45 mm"] },  // optional, when waiting on the human; Hange's "accept this version?" has kind: "accept" and version: 3, and shows in Review's decision bar too
      chat: false,                 // optional: true if the human can message this agent directly
      messages: [ { from: "you" | "agent", text: "...", at: "..." } ],  // optional, when chat is true
      cost: 0.03,                  // optional, dollars so far in this run
      model: "claude-sonnet-5-5"   // optional
    }
  ],
  handoffs: [ { id: "h12", from: "architect", to: "builder-1", label: "part list: 6 parts" } ],
  runs: [ {
    id: "run-7", title: "Rocking chair v3", round: "Round 3 of 5", started: "...", cost: 0.11,
    steps: [ { agent: "brief", label: "Spec v3", state: "done", at: "...", note: "Plain armrests" },
             { agent: ["builder-1", "builder-2"], label: "Build", state: "active" } ]   // step state: done | active | waiting | failed | (none = to do)
  } ],
  meetings: [ { id: "m3", kind: "kickoff" | "review", agents: ["director", "brief", "architect"] } ]
});
```

Rules:

- `role` decides the character and station. Every agent with role `builder` gets its own
  Eren at the workbench (up to 4 seats), seated in id order.
- An unknown `state` is treated as `idle`, with a console warning. An unknown `role` is ignored.
- Calling `applyState` again with the same snapshot changes nothing. Hand-offs and meetings
  play once each, keyed by `id` (or by their content if there is no id), so the engine can
  keep sending the recent ones.
- Agents the snapshot leaves out keep their last state.
- A hand-off is walked from the sender's station to the receiver's. The sender runs if the
  receiver's state is `waiting`, and walks otherwise. A sender who is busy (in a meeting,
  carrying another parcel) finishes that first.
- A `done` agent celebrates once when back at their station, then shows a small green check.

## Events the page sends

All bubble up to `document` as `CustomEvent`s:

| Event | `detail` | When |
|---|---|---|
| `studio:director` | `{ agent, prefill? }` | Hange is clicked (the page opens or closes the chat), or "Ask Hange about …" is pressed (`prefill` is "@Name "). |
| `studio:select` | `{ agent }` | Any other character is clicked; the agent panel opens. |
| `studio:answer` | `{ agent, choice }` | The human answers a question, from the agent panel or a pinned card. `choice` is an option or typed text. |
| `studio:chat` | `{ agent, text }` | The human messages an agent directly (only when that agent has `chat: true`). Messages to Hange come through `MG.app.handlers.chatDirector(text, { attachments, options })`. |
| `studio:handoff` | `{ from, to, label }` | A hand-off starts walking (used for the activity feed). |
| `studio:meeting` | `{ kind, agents }` | A meeting starts. |
| `studio:zoom` | `{ pct }` | The map zoom changes. |

## How the engine is wired in

- **Snapshots.** The engine builds the snapshot in `modelgen/studio_state.py` (`snapshot()`)
  and sends it over the websocket (`/ws`, message type `studio`) shortly after every change,
  and every few seconds so done and failed fade back to free. `live.js` passes it to
  `applyState`. Builders are seats `builder-1` to `builder-4`. Done lasts 25 seconds and failed
  90 seconds (`questions.done_s` and `failed_s` in `config.yaml`). Waiting means an open
  question; the director also waits when a finished version has not been accepted. Hand-offs
  from the last 90 seconds and meetings from the last 30 are sent.
- **Page data.** `GET /api/page?project=&version=` returns `model`, `models`, `parts`, `chat`,
  `spend`, `budget`, `blender`, `settings` and `limits` for `MG.app.loadData`. Versions carry
  `thumb`, `glb`, `downloads`, `export`, their own `review` (score out of 10, criteria,
  gates, changes and render URLs), `game` (links to the game GLB, LOD1, LOD2 and collision
  files, with the triangle counts, limit and size from `game.json`; null without them) and
  `path` (`library`, `remix`, `shell` or `team`: how the version was made). The model carries
  `brief`: the request, size, budget, `references` and `found` image URLs, the `spec` in short,
  the `research` block, the `path`, and for a ready-made model or a generated car its `asset`.
  Chat messages carry the `project` they are about. `live.js` reloads the data after events
  that change it.
- **Run steps.** Spec, Plan, Build, Assemble, Materials, Motion (a model that moves), Render,
  Review, Library; a run that looked for a ready-made model or did research also has
  Ready-made and Research before Spec.
- **What the page sends back**, all through `MG.app.handlers` in `live.js`:

| Handler | Engine |
|---|---|
| `chatDirector(text, { attachments, options })` | websocket `chat` (or `POST /api/chat`) |
| `chat({ agent, text })` | `POST /api/agents/{agent}/chat` |
| `answer({ agent, choice })` | `POST /api/answer`; for the director's accept question it accepts, or returns `compose` to open the composer |
| `accept(project, number)`, `revert(project, number)` | `POST /api/projects/{name}/accept`, `/revert` |
| `upload(file)` | `POST /api/uploads` (a data URL); images are served from `/uploads/` |
| `saveSettings(change)` | `POST /api/settings` |
| `loadActivity(agent)` | `GET /api/activity?agent=` |
| `mountViewer(element)` | imports `/static/viewer.js` |

The Export stage's rows link straight to `/files/...` and
`/api/projects/{name}/versions/{n}/export.zip`.

## The studio

Every agent has a computer workstation. Working, a character sits in their office chair
with their back to you, typing, so you see their screens over their shoulder; idle, waiting
or done, they stand in front of it facing you.

| Zone | Who and what |
|---|---|
| Review room | The wall screen with the latest render; meetings; the critic reviews renders here |
| Asset library, Material lab, Critic desk, Planning | The back row: librarian, materials, critic and architect at their workstations, with a storage tower, a material wall display, a render wall and a reference screen |
| Coffee bar, Lounge, Team dashboard | Break spots, and a screen with the run's steps |
| Director, Brief desk, Modeling bay, Assembly bay | The front row: Hange, Armin with a touch-screen board, up to four builders with two monitors each and a build board, Mikasa with a curved monitor |
| Showcase | A screen turning the latest model, with two stools |
| Focus pods, Animation suite | Two quiet pods anyone can use, and Jean's pod |
| GPU room | GPU servers, drive racks and a console; the lights speed up while Blender works (a builder or the assembler working, or a render) or anyone is inside |
| VR test zone | Headset, tracking beacons and a mirror screen; whoever stands on the mat wears the headset |
| Scan & print lab | Two 3D printers, a scanner turntable and a shelf of prototypes |

**Live screens.** Each workstation's screens follow their owner's state: a dim screensaver
when idle; while working, something for the role (a turning wireframe for builders, a
material sphere, a render and its checklist, the parts tree and a blueprint, the spec being
typed, the asset grid, the animation timeline, the fit checks, the director's dashboard and
chat); an amber `?` when waiting, a red ✕ when failed, a green check when done. A builder
seat with nobody at it stays dark. The screens carry `data-o` (the owner's cast id, or
`seat0` to `seat3`) in `js/stations.js`.

## Tool to spot

Where `spot` (or, without it, `tool`) sends a character while working. Any other tool sits
them at their own computer. The engine maps its real tool names onto these in `TOOL_SPOTS` in
`modelgen/studio_state.py`; for example a builder's `write_part` goes to `run_blender`, and
the critic's rendering step goes to `render_views`.

| Role | Tool | Spot |
|---|---|---|
| librarian | `search_library` | their computer |
| librarian | `read_part` | the storage tower |
| librarian | `save_part`, `archive_version` | the drive racks in the GPU room |
| materials | `make_material` | their computer |
| materials | `pick_material` | the material wall display |
| critic | `view_renders` | the review room screen |
| critic | `render_views` | the GPU room console |
| architect | `plan_parts` | their computer |
| architect | `fetch_plans` | the reference screen |
| architect | `print_plan` | the 3D printer in the lab |
| director | `check_queue` | their computer |
| brief | `pin_note` | the touch-screen board |
| assembler | `check_fit` | their computer |
| animator | `motion_desk`, `preview_frames` | their computer |
| builder | `read_parts`, `run_blender` | their computer |

The list lives in `js/stations.js` (`STATIONS` and `SEATS`).

## Keyboard

Ctrl K command menu · Ctrl \ chat · Ctrl B navigation · Alt 1 to 4 the stages (Brief, Build,
Review, Export) · F fit the studio and L labels (in Build) · A accept · C ask for a change ·
N new model · V large 3D view · 1 to 6 3D views (front, back, left, right, top, 3/4) · Esc close.
