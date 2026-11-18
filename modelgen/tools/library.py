"""The shared library: part functions and material recipes that worked, kept for future models.

Stored as plain files under library\\: parts\\<name>.py, materials\\<name>.py and index.json with a
description and tags for each, so the user can read and edit them too.
"""

import json
import re
import shutil
import threading
from pathlib import Path

from .. import config, db

KINDS = {"part": "parts", "material": "materials"}
_lock = threading.Lock()


def clean_name(name):
    name = re.sub(r"[^a-z0-9_]+", "_", (name or "").strip().lower()).strip("_")
    if not name:
        raise ValueError("a library entry needs a name")
    return name[:60]


def source_score(source):
    """The critic's score (out of 100) of the version a library entry came from ("<project> vN
    part <part>"), or None when the source is not a version or that version has no score."""
    match = re.match(r"^(\S+) v(\d+)\b", source or "")
    if not match:
        return None
    try:
        version = db.get_version(match.group(1), int(match.group(2)))
    except Exception:
        return None
    score = (version or {}).get("score")
    return round(float(score), 1) if score is not None else None


class Library:
    def __init__(self, root=None):
        self.root = root or config.library_dir()

    def _index_path(self):
        return self.root / "index.json"

    def _load(self):
        p = self._index_path()
        if not p.is_file():
            return {"parts": [], "materials": []}
        return json.loads(p.read_text(encoding="utf-8"))

    def _save(self, index):
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._index_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._index_path())

    def add(self, kind, name, description, code, tags=(), source=""):
        name = clean_name(name)
        folder = self.root / KINDS[kind]
        folder.mkdir(parents=True, exist_ok=True)
        with _lock:
            index = self._load()
            entries = index.setdefault(KINDS[kind], [])
            entry = next((e for e in entries if e["name"] == name), None)
            if entry is None:
                entry = {"name": name, "revisions": 0}
                entries.append(entry)
            entry.update({"description": description, "tags": list(tags), "source": source,
                          "file": f"{KINDS[kind]}/{name}.py", "updated": db.now()})
            score = source_score(source)
            if score is not None:
                entry["score"] = score
            entry["revisions"] = entry.get("revisions", 0) + 1
            (folder / f"{name}.py").write_text(code, encoding="utf-8")
            self._save(index)
        return entry

    def add_part(self, name, description, code, tags=(), source="", preview=None):
        entry = self.add("part", name, description, code, tags, source)
        if preview and Path(preview).is_file():
            self.previews().mkdir(parents=True, exist_ok=True)
            shutil.copy2(preview, self.previews() / f"{entry['name']}.jpg")
        return entry

    def add_material(self, name, description, code, tags=(), source=""):
        return self.add("material", name, description, code, tags, source)

    def list(self, kind=None):
        index = self._load()
        if kind:
            return [dict(e, kind=kind) for e in index.get(KINDS[kind], [])]
        return [dict(e, kind=k) for k, folder in KINDS.items() for e in index.get(folder, [])]

    def get(self, name, kind="part", count=False):
        """The entry's code. count=True records one more reuse (an agent starting from it)."""
        name = clean_name(name)
        path = self.root / KINDS[kind] / f"{name}.py"
        if not path.is_file():
            raise KeyError(f"there is no {kind} called '{name}' in the library")
        if count:
            with _lock:
                index = self._load()
                entry = next((e for e in index.get(KINDS[kind], []) if e["name"] == name), None)
                if entry is not None:
                    entry["uses"] = entry.get("uses", 0) + 1
                    self._save(index)
        return path.read_text(encoding="utf-8")

    # --- previews for the page ------------------------------------------------------

    def previews(self):
        return self.root / "previews"

    def preview(self, name):
        """The part's picture, or None. A part saved before previews were kept gets one copied
        from the builder's preview in the version it came from, when that still exists."""
        name = clean_name(name)
        own = self.previews() / f"{name}.jpg"
        if own.is_file():
            return own
        entry = next((e for e in self.list("part") if e["name"] == name), None)
        match = re.match(r"^(\S+) v(\d+) part (\w+)$", (entry or {}).get("source", ""))
        if not match:
            return None
        project, number, part = match.groups()
        sheets = []
        for n in range(int(number), 0, -1):  # an unchanged part was carried over from an earlier version
            folder = config.projects_dir() / project / f"v{n}" / "previews" / part
            sheets = sorted(folder.glob("*/sheet.jpg")) if folder.is_dir() else []
            if sheets:
                break
        if not sheets:
            return None
        self.previews().mkdir(parents=True, exist_ok=True)
        shutil.copy2(sheets[-1], own)
        return own

    def _matches(self, query, kind=None):
        """(match, entry) for every entry, match = how many query words appear in its name (2 each),
        description and tags (1 each), best match first."""
        words = [w for w in re.findall(r"[a-z0-9]+", (query or "").lower()) if len(w) > 1]
        scored = []
        for e in self.list(kind):
            text = " ".join([e["name"].replace("_", " "), e.get("description", ""), " ".join(e.get("tags", []))]).lower()
            score = sum(2 if w in e["name"] else 1 for w in words if w in text)
            if score or not words:
                scored.append((score, e))
        scored.sort(key=lambda s: -s[0])
        return scored

    def search(self, query, kind=None, limit=8):
        """Entries ranked by how many query words appear in their name, description and tags."""
        return [{"name": e["name"], "kind": e["kind"], "description": e.get("description", ""),
                 "tags": e.get("tags", [])} for _, e in self._matches(query, kind)[:limit]]

    def examples(self, query, limit=2, exclude=(), min_match=2, pool=6, max_chars=4000):
        """Worked examples for a builder: of the `pool` parts that match the query best (at least
        `min_match`, and within one of the best match so a part that only mentions the word never
        beats one named for it), the `limit` whose models the critic scored highest. Each is
        {name, description, score, code}, the code cut at `max_chars`."""
        skip = {clean_name(x) for x in exclude if x}
        query = " ".join(dict.fromkeys(re.findall(r"[a-z0-9]+", (query or "").lower())))  # each word once
        found = [(m, e) for m, e in self._matches(query, "part") if m >= min_match and e["name"] not in skip]
        best = found[0][0] if found else 0
        close = [e for m, e in found if m >= best - 1][:pool]
        for e in close:
            if e.get("score") is None:
                e["score"] = source_score(e.get("source"))
        close.sort(key=lambda e: -(e.get("score") or 0))
        out = []
        for e in close:
            if len(out) >= limit:
                break
            path = self.root / e.get("file", f"parts/{e['name']}.py")
            if not path.is_file():
                continue
            code = path.read_text(encoding="utf-8")
            if len(code) > max_chars:
                code = code[:max_chars].rsplit("\n", 1)[0] + "\n# ... (cut here)"
            out.append({"name": e["name"], "description": e.get("description", ""), "score": e.get("score"),
                        "code": code})
        return out

    def backfill_scores(self):
        """Write the critic's score onto entries saved before scores were kept. Returns how many."""
        n = 0
        with _lock:
            index = self._load()
            for e in index.get("parts", []) + index.get("materials", []):
                if e.get("score") is None:
                    score = source_score(e.get("source"))
                    if score is not None:
                        e["score"] = score
                        n += 1
            if n:
                self._save(index)
        return n
