# ModelGen

A team of Claude agents that turns an object you describe, in English or Egyptian Arabic,
into a 3D model built purely from Blender Python code the agents write, render, critique and
refine. No 3D generation service and no API key: every agent asks Claude through ClaudeIn, the tray
program that uses the Claude login of this PC.

## Install (on any Windows PC)

1. Download `ModelGen-Setup.exe` from the latest release
   (https://github.com/ikverse/modelgen/releases/latest) and run it. Windows may warn that the file
   is not signed: choose **More info**, then **Run anyway**.
2. Keep ClaudeIn running while you use ModelGen (its icon is in the hidden icons by the clock).
   ModelGen reaches Claude only through it.

The setup checks the PC and installs only what is missing:
- ClaudeIn, from its latest release (https://github.com/ikverse/ClaudeIn/releases). Its own setup
  window opens, where you pick its options and sign in to Claude.
- Python 3.14, from python.org.
- ModelGen's packages.
- Blender 5.2, from blender.org (about 400 MB), when the PC has no Blender or one older than 5.2.
  It goes into ModelGen's own folder; a Blender you installed yourself is left alone.

ModelGen then updates itself: when a newer release is out, the top bar offers it.

## Setup from this source folder (development)

1. Install Blender (`winget install --id BlenderFoundation.Blender -e`). ModelGen finds it under
   `C:\Program Files\Blender Foundation\` by itself; elsewhere, put the path to `blender.exe` on the
   Settings page (or in `config.yaml` under `blender: path:`).
2. Put ClaudeIn's folder next to this one (`..\ClaudeIn`) and keep ClaudeIn running.
3. Double-click `Setup-ModelGen.bat`. It builds ModelGen's own Python environment in `.venv`,
   installs the packages and the ClaudeIn client, installs the 3D viewer library (three.js, needs
   Node.js) and says whether Blender was found.

A source folder never updates itself. To publish a new version for installed copies: raise
`__version__` in `modelgen\__init__.py`, commit, then run `installer\Publish-Release.bat`
(`installer\Build-Installer.bat` only builds `dist\ModelGen-Setup.exe`; it needs Inno Setup 6).

## Use

Start ModelGen from the Start menu (or double-click `ModelGen.bat`). The page opens at
http://127.0.0.1:8770/ (it moves to `/map/`).

The page is the studio. The chat with Hange is on the left; the open model's work is on the right,
split into four stages you move between with the tabs under its name: **Brief** (what was asked
for), **Build** (the team at work), **Review** (the model and Annie's review) and **Export** (the
files). The page opens the right stage by itself: Build when a build starts, Review when a version
gets its score, Export when you accept. Each tab shows a mark when it is working, needs you, is
done or failed.

The team works as nine characters on the studio floor in the Build stage, and each one walks to
the tool it is using. Hange (the director) is at the front desk, Armin writes the spec, Pieck plans
the parts, up to four Erens build parts at once, Mikasa assembles, Reiner does the materials, Jean
adds the motion from the Motion booth, Annie renders and reviews, and Historia keeps the parts
library.

- **Chat with Hange** (left): describe what you want, in English or Egyptian Arabic. Hange asks at
  most two questions, then starts. The photo button (or pasting an image) attaches a reference
  image the team looks at. "@Annie why is the score low?" asks Hange about one team member. The
  chat shows the open model's messages; *All models* shows everything.
- **Questions** pin to the top of the chat when someone needs you: a team member blocked by a
  choice only you can make, the spending limit, or "accept this version?" at the end. The top bar
  shows *Needs you* and the stage the question belongs to turns amber. An agent's question waits
  nine minutes, then the agent decides itself.
- **Brief**: your request, size, budget and reference images; what the researcher found about the
  real object (size, proportions, features, colours, what it had to guess, the photos it kept);
  the spec in short; and how the model is made (a ready-made model, the car generator, or the team).
- **Build**: the studio floor. Click a character for what they are doing, their recent tool calls,
  and a chat with them directly (Hange is the chat on the left). Double-click a room to zoom in, F
  to fit. Beside the floor: the steps of the build (a run that looked for a ready-made model or
  did research shows those as steps too), a live 3D preview of the part being built, and every
  agent's tool calls.
- **Review**: the 3D model (drag to orbit, scroll to zoom; tools for views, wireframe, lighting,
  measuring and more), the versions with their score out of 10 under it, and beside it Annie's
  review with its renders (click one to see it large) and what the next version will change. The
  bar at the bottom is the decision: *Accept*, *Build next version*, *Add my comments*, or
  *Request changes*. *Build changes from* under an older version starts the next one from it.
- **Export**: the files of the selected version: GLB, FBX, OBJ, STL, the .blend, or all of it as a
  zip; the game-ready files with their triangle counts; and a GLB with colours you changed by
  clicking parts in the view.
- **Motion**: a vehicle on wheels moves by itself. Armin puts a `motion` entry in the spec (the
  speed, the wheels, and smoke or steam for a steam engine), and Jean writes a short looping
  motion after the materials: the wheels turn at the right speed for their size, the body can
  bounce on its springs, and markers say where smoke or steam rises. The viewer plays it (a play
  button appears next to Spin; the ground slides under the vehicle, which stays in place) and
  draws the smoke and steam at the markers. The GLB and FBX downloads carry the wheel motion, so a
  game can play it; OBJ and STL cannot hold motion. Smoke and steam are drawn by the page only.
  Ask Hange for a speed, for something else to move, or for no motion. Annie sees a strip of
  frames across the loop and sends motion changes to Jean.
- **One version per build**: the team builds one version, Annie reviews it, and the build stops.
  Hange then asks: *Accept* it, *Build next version* (with Annie's change list, at most seven
  changes), or *Add my comments* (your comments and Annie's open changes go into the next version
  together; yours win where they disagree). Nothing more is built until you choose. A next
  version starts from the best version so far, so a worse result never becomes the starting
  point; *Build changes from* still picks another one.
- **Delete a version**: *Delete* under a version in the Review stage removes it for good (its
  folder and its row) after you confirm; it is hidden while that model is building. If it was the
  accepted version or the next version's starting point, that moves to the best version left.
  What it cost and the activity log stay, and its number is never given to another version.
- **New model**: describe it, add reference images, a size and a budget for that model.
- **Models**, **Parts library** and **Activity** list every model, every saved part and
  everything the team did.
- **Settings**: the daily and per-model spending limits, and the Blender path.

Adding `?demo=1` to the studio address plays a scripted demo run with no engine work and no cost.

Everything is kept: `projects\<model>\v1, v2, ...` hold each version's scripts, previews,
renders, exports, .blend and review; `data\modelgen.db` holds the chat, direct messages, events and
every agent run with its cost; `data\uploads` holds reference images; `data\settings.json` holds
what you changed on the Settings page; `library\` holds part functions, material recipes and part
pictures worth reusing.

## A sample session in Egyptian Arabic

> **أنت:** عايز مج قهوة سيراميك، شكله بسيط ومودرن، لونه أوف وايت.
>
> **الديركتور:** حلو. سؤالين بس: المج ده للرندر ولا هتطبعه 3D؟ وعايزه حجم عادي (حوالي ٣٥٠ مل) ولا كبير؟
>
> **أنت:** للرندر، حجم عادي.
>
> **الديركتور:** تمام، الفريق بدأ. هتلاقي التقدم على الصفحة، وأول نسخة هتظهر في الـ viewer لما تخلص.
>
> *(بعد كام دقيقة)*
>
> **الديركتور:** النسخة v2 خدت ٨٣ من ١٠٠ وعدّت كل الاختبارات. الشكل والإيد طالعين حلوين، والناقد شايف إن
> لمعة الجليز ممكن تبقى أهدى شوية. تقدر تلفّه في الـ viewer، تقبله، أو تقولي تغيّر إيه.
>
> **أنت:** خلي الإيد أتخن شوية والجليز مطفي.
>
> **الديركتور:** ماشي، بعت التعديلات للفريق: إيد أتخن وجليز مطفي. هقولك لما النسخة الجديدة تخلص.

The team works in English internally; the director always answers in the language you used.

## How an object that is not a car is made

1. **A ready-made model, when the catalogue has it.** ModelGen keeps a catalogue of free
   public-domain models (Poly Haven: about 520 realistic, textured props, furniture, plants, rocks
   and tools). When you ask for one ordinary object it has ("a wooden chair", "a barrel"), a small
   agent checks that a candidate really is what you asked for, and the asset is made from it in
   about a minute with no build team: resized to the size you gave, recoloured if you named a
   colour. *Build next version* tries the next candidate; a colour or a size changes the same
   model; "build it from scratch" hands it to the team. The model's files are downloaded from
   polyhaven.com the first time it is used and kept in `assets_cc0\objects`. Two requests for the
   same thing can give the same model. Turn this off with `objects: generator: agents` in
   `config.yaml`; refresh the catalogue with
   `.venv\Scripts\python.exe -m modelgen.tools.asset_catalog --build`. Any `.glb` pack you put in
   `assets_cc0\objects\<name>\` (a Quaternius or Kenney pack) is added by the same command.
2. **Otherwise the team builds it**, with three things that keep it accurate:
   - **Research.** Before the spec, a research agent looks up the real object on the web: its
     published size, proportions, features and colours, and up to four reference photos the
     builders and the critic compare with (about $0.10 a model; `research: enabled: false` skips it).
   - **Families.** The spec names what kind of thing it is: container, device, furniture,
     building, scene, vehicle or character. Each family has its own rules for planning, building,
     materials and review (`modelgen\families.py`).
   - **Kits.** For containers, devices, buildings, wheeled vehicles and scene props the builders
     start from tested building blocks (`modelgen\bpy_lib\kits.py`): one hollow vessel with a real
     inside and a fused handle, one device shell with pockets, walls with windows on every side,
     roofs, a glass tower, wheels in arches, trees, benches, fences, lamp posts. A scene can also
     place ready-made catalogue models as parts.

## How a person is made

A request for a person ("a cool guy", "an old grandma", "baby girl", "a nerd") goes to the
**character path**, not the team. The character designer agent turns your words into a character
sheet (age, sex, height, build, ancestry, skin tone), and the body is built from it exactly in one
Blender run: MakeHuman's free body data (CC0, as packaged by the Anny body model, in
`assets_cc0\anny_body`) covers a newborn to a 90-year-old, at the real height for the age and sex
unless you give one. Every character gets a **53-bone game skeleton** (the usual names: `pelvis`,
`spine_01`, `upperarm_l`, fingers...) and skin weights, kept in the GLB and FBX files and in the game
files (20,000 triangles, lower-detail versions keep the skeleton). The viewer plays its clips; a
clip picker appears when it has more than one. Ask for changes in words ("make her ten years
older"). A robot, an animal, a statue or a monster is not a person: the team builds those as
stylised figures.

So far a character is a bare, uniformly coloured body with eyes and one test clip; hair, clothes,
drawn styles (cartoon, anime), real clips (idle, walk, run) and hair and cloth physics are the next
stages of `CHARACTERS_PLAN.md`.

Every finished model, however it was made, also gets **game-ready files** in its `game` folder
(they are listed in the Export stage and are in the *All of it* zip): `<name>_game.glb` within the triangle limit of its family, with
the pivot at the centre of its base and real-world scale, `<name>_LOD1.glb` and `_LOD2.glb` at half
and a fifth of the triangles, `<name>_collision.glb` (one box), and `game.json` with the counts.
The triangle limits are in `modelgen\families.py` (5,000 for a container to 60,000 for a scene).
Patterns such as brick or wood grain are drawn by Blender's shaders and are not in any GLB: a game
file carries each material's plain colour, or a ready-made model's own textures.

## What it is good at, honestly

Code-built geometry is excellent for manufactured, hard-surface objects: furniture, tableware,
tools, machines, buildings, vehicles, props. It is much weaker at realistic organic forms (faces,
animals, plants, draped cloth); for those the director offers a stylised version.

## Settings

The Settings page changes the spending limits and the Blender path. Before any agent run that
could take spending past today's limit or the model's limit, the build waits and Hange asks you
whether to raise the limit and continue or stop. A budget set on the New model screen replaces the
per-model limit for that model.

`config.yaml` holds the defaults and everything else: Blender time limit, render engine, size and
samples, pass score, versions per build (`loop: max_rounds`, 1 so you are asked before each
next version), parallel builders, the default limits, how long a question
waits, and the turn and dollar caps for each agent's runs (`talk` is for direct messages). Each
agent's model and role prompt is in `modelgen\agents\<name>.md`; change the model there.

## Tests

```
.venv\Scripts\python.exe -m pytest tests
```

The Blender tests (a mug, a chair and a table built from fixed scripts) skip with the install
command when Blender is not installed. Every Claude call in the tests is a fake, so they cost
nothing; `tests\test_studio.py` covers the studio page's data, questions, limits and endpoints.
