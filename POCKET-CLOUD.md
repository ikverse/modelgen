# ModelGen × Pocket Cloud

**Goal:** a finished model you choose is kept off the PC and can be opened on your phone through a share link;
`data\modelgen.db` gets a nightly off-PC copy.

**Space is the constraint.** `projects\` is ~3.8 GB of every version in five formats; the phone's pool is much
smaller and shared with everything else. So nothing is uploaded automatically except the database — models go
up only when you press "Keep on Pocket Cloud", and only the formats you pick (GLB by default).

## Before you start

- Pocket Cloud v2 steps 1 and 2 must be finished (see `Pocket-Cloud/docs/plan-v2.md`). This guide uses the
  **Python library** `Pocket-Cloud/clients/python`. It was written before the v2 library existed: read its
  README and `Pocket-Cloud/docs/app-api.md` first and use the real names where they differ.
- The global rule applies: list the changes, wait for "approve", then write code.
- This PC is already set up (`%USERPROFILE%\.pocket\config`). Install the library into ModelGen's `.venv`:
  `pip install -e "C:\Users\kabas\Documents\Claude\Claude Code\Pocket-Cloud\clients\python"`.
- Share links that open **outside** your tailnet need "Share links" switched on in the Pocket Cloud panel
  (it runs a Cloudflare quick tunnel). Inside the tailnet they work without it.

## What goes to Pocket Cloud

App id **`modelgen`**, `shared` bucket. Allocation in the panel: 5 GB.

| Key | What | When |
|---|---|---|
| `models/<project>/<version>/<name>.glb` (+ `.png` preview, optional `.stl/.obj/.fbx/.blend`) | A version you chose to keep | On "Keep on Pocket Cloud" |
| `backups/modelgen-<yyyy-MM-dd>.db` | Copy of `data\modelgen.db` (~7 MB) | Nightly |

Retention rule: `backups/` keep the last 14.

**Never uploaded:** `data\api_key.txt`, `~\.modelgen-release\signing-key.pem`, `assets_cc0\` (re-downloadable),
`library\references` unless you ask, `dist\` installers.

## Where in the code

- `modelgen\api_v1.py` — the Village-facing API (local only, refuses `Origin` headers — keep it that way).
- `modelgen\jobs.py` — a job's result `files`; the natural place to offer "keep".
- `modelgen_client.py` — the client Village uses (`station\tools\modelgen_client.py` in Village).
- The three.js viewer page — where the button goes.
- `config.yaml` / `data\settings.json` — settings.

## Steps

1. `modelgen\pocket.py`: lazy `pocket.app("modelgen")`; everything hidden when not reachable.
2. Viewer: "Keep on Pocket Cloud" on a version → choose formats (GLB + preview ticked) → upload with progress.
   A kept version shows a cloud mark; "Remove from Pocket Cloud" deletes it there.
3. "Share to my phone": `app.share(key, expires=7 days)` on the GLB, show the link and a QR code. Phones open
   GLB in Android's Scene Viewer or any GLB viewer.
4. Nightly DB copy (SQLite backup API to a temp file, then `app.backup(tmp, keep=14)`) from the existing
   scheduler; failures logged, never fatal.
5. Optional: `api_v1.py` gains `keep` and `share` job actions so Village can ask for them; same local-only rules.

## Test

- Unit: format selection → key names; reachability off hides the buttons.
- Keep a model, open its share link on the phone, remove it, check the panel shows the space back.

## Done when

Chosen models are one click from the phone, the database has nightly off-PC copies, and nothing uploads that
you didn't pick.
