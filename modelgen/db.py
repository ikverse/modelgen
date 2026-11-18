"""Everything ModelGen remembers: chat, projects, versions, agent runs with their cost, and events.

One SQLite file, data\\modelgen.db. Every event is also handed to the listeners (the web server
pushes them to the page), so the page shows live progress and the history survives a restart.
"""

import json
import re
import sqlite3
import threading
from datetime import datetime

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    name TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    request TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new',
    spec_json TEXT,
    accepted_version INTEGER,
    base_version INTEGER,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS versions (
    project TEXT NOT NULL,
    number INTEGER NOT NULL,
    created TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'building',
    score REAL,
    passed INTEGER NOT NULL DEFAULT 0,
    review_json TEXT,
    checks_json TEXT,
    note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project, number)
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT,
    version INTEGER,
    agent TEXT NOT NULL,
    model TEXT NOT NULL,
    doing TEXT NOT NULL DEFAULT '',
    started TEXT NOT NULL,
    ended TEXT,
    ok INTEGER,
    turns INTEGER,
    cost_usd REAL NOT NULL DEFAULT 0,
    error TEXT
);
CREATE TABLE IF NOT EXISTS agent_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    project TEXT,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT,
    agent TEXT,
    kind TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}',
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    client TEXT NOT NULL,
    request_key TEXT,
    object TEXT NOT NULL,
    request_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued',
    step TEXT NOT NULL DEFAULT '',
    project TEXT,
    version INTEGER,
    score REAL,
    passed INTEGER,
    source TEXT,
    error TEXT,
    notes TEXT NOT NULL DEFAULT '',
    retry_at TEXT,
    created TEXT NOT NULL,
    started TEXT,
    finished TEXT
);
"""

# Columns added after the first release, added to an older database on open.
MIGRATIONS = {
    "messages": {"meta_json": "TEXT"},
    "projects": {"options_json": "TEXT"},
    "runs": {"seat": "TEXT"},
}

_lock = threading.RLock()
_conn = None
_listeners = []


def now():
    return datetime.now().isoformat(timespec="seconds")


def conn():
    global _conn
    with _lock:
        if _conn is None:
            folder = config.data_dir()
            folder.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(folder / "modelgen.db", check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(SCHEMA)
            _migrate(_conn)
        return _conn


def _migrate(c):
    for table, columns in MIGRATIONS.items():
        have = {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
        for column, kind in columns.items():
            if column not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    c.commit()


def close():
    """Forget the open database (the tests use this to start from an empty one)."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None


def _exec(sql, args=()):
    with _lock:
        c = conn()
        cur = c.execute(sql, args)
        c.commit()
        return cur


def _rows(sql, args=()):
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def _row(sql, args=()):
    rows = _rows(sql, args)
    return rows[0] if rows else None


# --- live events ------------------------------------------------------------------

def subscribe(fn):
    _listeners.append(fn)


def unsubscribe(fn):
    if fn in _listeners:
        _listeners.remove(fn)


def _notify(item):
    for fn in list(_listeners):
        try:
            fn(item)
        except Exception:  # a broken listener must never stop the pipeline
            pass


def add_event(kind, project=None, agent=None, data=None):
    created = now()
    data = data or {}
    cur = _exec("INSERT INTO events (project, agent, kind, data_json, created) VALUES (?, ?, ?, ?, ?)",
                (project, agent, kind, json.dumps(data, ensure_ascii=False, default=str), created))
    item = {"id": cur.lastrowid, "project": project, "agent": agent, "kind": kind, "data": data,
            "created": created}
    _notify(item)
    return item


def events(project=None, after_id=0, limit=200):
    if project:
        rows = _rows("SELECT * FROM events WHERE project = ? AND id > ? ORDER BY id DESC LIMIT ?",
                     (project, after_id, limit))
    else:
        rows = _rows("SELECT * FROM events WHERE id > ? ORDER BY id DESC LIMIT ?", (after_id, limit))
    for r in rows:
        r["data"] = json.loads(r.pop("data_json") or "{}")
    return rows[::-1]


def events_since(created, kinds=None, project=None, limit=500):
    """Events written at or after this time, oldest first, optionally of some kinds only."""
    sql, args = "SELECT * FROM events WHERE created >= ?", [created]
    if kinds:
        sql += f" AND kind IN ({', '.join('?' for _ in kinds)})"
        args += list(kinds)
    if project:
        sql += " AND project = ?"
        args.append(project)
    rows = _rows(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))
    for r in rows:
        r["data"] = json.loads(r.pop("data_json") or "{}")
    return rows[::-1]


def last_event(kind, project=None):
    sql, args = "SELECT * FROM events WHERE kind = ?", [kind]
    if project:
        sql += " AND project = ?"
        args.append(project)
    row = _row(sql + " ORDER BY id DESC LIMIT 1", args)
    if row:
        row["data"] = json.loads(row.pop("data_json") or "{}")
    return row


def events_of(kinds, project=None, agent=None, limit=200):
    """The newest events of these kinds (agent may be a seat id or a role prefix), oldest first."""
    sql, args = f"SELECT * FROM events WHERE kind IN ({', '.join('?' for _ in kinds)})", list(kinds)
    if project:
        sql += " AND project = ?"
        args.append(project)
    if agent:
        sql += " AND (agent = ? OR agent LIKE ?)"
        args += [agent, f"{agent}-%"]
    rows = _rows(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))
    for r in rows:
        r["data"] = json.loads(r.pop("data_json") or "{}")
    return rows[::-1]


# --- chat -------------------------------------------------------------------------

def add_message(role, text, project=None, meta=None):
    """meta: optional extras the page shows with the message (attachments, a version card)."""
    created = now()
    meta = meta or {}
    cur = _exec("INSERT INTO messages (project, role, text, created, meta_json) VALUES (?, ?, ?, ?, ?)",
                (project, role, text, created, json.dumps(meta, ensure_ascii=False, default=str)))
    msg = {"id": cur.lastrowid, "project": project, "role": role, "text": text, "created": created, "meta": meta}
    _notify({"kind": "message", "project": project, "data": msg})
    return msg


def _message_row(row):
    row["meta"] = json.loads(row.pop("meta_json", None) or "{}")
    return row


def messages(limit=50):
    return [_message_row(r) for r in _rows("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,))[::-1]]


def messages_since(created):
    """The person's and the director's messages written at or after this time, oldest first."""
    return [_message_row(r) for r in _rows("SELECT * FROM messages WHERE created >= ? ORDER BY id", (created,))]


# --- direct messages with one agent ---------------------------------------------------

def add_agent_message(agent, role, text, project=None):
    """role is 'you' or 'agent'."""
    created = now()
    cur = _exec("INSERT INTO agent_messages (agent, project, role, text, created) VALUES (?, ?, ?, ?, ?)",
                (agent, project, role, text, created))
    msg = {"id": cur.lastrowid, "agent": agent, "project": project, "role": role, "text": text, "created": created}
    _notify({"kind": "agent_message", "project": project, "agent": agent, "data": msg})
    return msg


def agent_messages(agent, limit=20):
    return _rows("SELECT * FROM agent_messages WHERE agent = ? ORDER BY id DESC LIMIT ?", (agent, limit))[::-1]


# --- projects and versions --------------------------------------------------------

def slugify(text):
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return "-".join(words)[:40].strip("-") or "model"


def create_project(title, request="", options=None):
    """options: what the person set for this model on the page (references, size, budget_usd)."""
    base = slugify(title)
    name, n = base, 2
    while get_project(name):
        name, n = f"{base}-{n}", n + 1
    _exec("INSERT INTO projects (name, title, request, created, options_json) VALUES (?, ?, ?, ?, ?)",
          (name, title, request, now(), json.dumps(options or {}, ensure_ascii=False)))
    return get_project(name)


def get_project(name):
    row = _row("SELECT * FROM projects WHERE name = ?", (name,))
    if row:
        row["spec"] = json.loads(row.pop("spec_json") or "null")
        row["options"] = json.loads(row.pop("options_json", None) or "{}")
    return row


def list_projects():
    rows = _rows("SELECT name, title, status, accepted_version, base_version, created FROM projects "
                 "ORDER BY created DESC, rowid DESC")
    return rows


def update_project(name, **fields):
    if "spec" in fields:
        fields["spec_json"] = json.dumps(fields.pop("spec"), ensure_ascii=False)
    if "options" in fields:
        fields["options_json"] = json.dumps(fields.pop("options"), ensure_ascii=False)
    sets = ", ".join(f"{k} = ?" for k in fields)
    _exec(f"UPDATE projects SET {sets} WHERE name = ?", (*fields.values(), name))


def delete_project(name):
    """Remove a model's row and its versions' rows. What it cost (runs), its activity log (events)
    and the chat stay as history, so the day's spend stays right."""
    with _lock:
        c = conn()
        c.execute("DELETE FROM versions WHERE project = ?", (name,))
        c.execute("DELETE FROM projects WHERE name = ?", (name,))
        c.commit()


def add_version(project, number, note=""):
    _exec("INSERT OR REPLACE INTO versions (project, number, created, note) VALUES (?, ?, ?, ?)",
          (project, number, now(), note))


def update_version(project, number, **fields):
    for key in ("review", "checks"):
        if key in fields:
            fields[f"{key}_json"] = json.dumps(fields.pop(key), ensure_ascii=False, default=str)
    sets = ", ".join(f"{k} = ?" for k in fields)
    _exec(f"UPDATE versions SET {sets} WHERE project = ? AND number = ?", (*fields.values(), project, number))


def _version_row(row):
    row["review"] = json.loads(row.pop("review_json") or "null")
    row["checks"] = json.loads(row.pop("checks_json") or "null")
    row["passed"] = bool(row["passed"])
    return row


def versions(project):
    return [_version_row(r) for r in _rows("SELECT * FROM versions WHERE project = ? ORDER BY number",
                                            (project,))]


def get_version(project, number):
    row = _row("SELECT * FROM versions WHERE project = ? AND number = ?", (project, number))
    return _version_row(row) if row else None


def delete_version(project, number):
    """Remove one version's row. Its runs (what it cost) and events stay as history."""
    _exec("DELETE FROM versions WHERE project = ? AND number = ?", (project, number))


def highest_version_number(project):
    """The highest version number this model ever used, counting deleted versions through their
    runs, so a number is never given to a second version."""
    row = _row("SELECT MAX(n) AS n FROM (SELECT MAX(number) AS n FROM versions WHERE project = ? "
               "UNION ALL SELECT MAX(version) FROM runs WHERE project = ?)", (project, project))
    return int(row["n"] or 0) if row else 0


def project_client(name):
    """The other program a model was made for through the /v1 API ("village"), or None for a model
    the person started on the page."""
    proj = get_project(name) if name else None
    api = ((proj or {}).get("options") or {}).get("api") or {}
    return api.get("client") or None


# --- jobs from other programs (the /v1 API) ---------------------------------------------

JOB_LIVE = ("queued", "building", "waiting_for_usage")


def _job_row(row):
    row["request"] = json.loads(row.pop("request_json") or "{}")
    row["passed"] = None if row["passed"] is None else bool(row["passed"])
    return row


def add_job(job_id, client, obj, request, request_key=None):
    _exec("INSERT INTO jobs (id, client, request_key, object, request_json, created) VALUES (?, ?, ?, ?, ?, ?)",
          (job_id, client, request_key, obj, json.dumps(request, ensure_ascii=False), now()))
    return get_job(job_id)


def get_job(job_id):
    row = _row("SELECT * FROM jobs WHERE id = ?", (job_id,))
    return _job_row(row) if row else None


def update_job(job_id, **fields):
    if "request" in fields:
        fields["request_json"] = json.dumps(fields.pop("request"), ensure_ascii=False)
    if "passed" in fields and fields["passed"] is not None:
        fields["passed"] = int(bool(fields["passed"]))
    sets = ", ".join(f"{k} = ?" for k in fields)
    _exec(f"UPDATE jobs SET {sets} WHERE id = ?", (*fields.values(), job_id))
    return get_job(job_id)


def list_jobs(client=None, status=None, limit=200):
    """Newest first. status may be one status or a list of them."""
    sql, args = "SELECT * FROM jobs WHERE 1 = 1", []
    if client:
        sql += " AND client = ?"
        args.append(client)
    if status:
        wanted = [status] if isinstance(status, str) else list(status)
        sql += f" AND status IN ({', '.join('?' for _ in wanted)})"
        args += wanted
    return [_job_row(r) for r in _rows(sql + " ORDER BY created DESC, rowid DESC LIMIT ?", (*args, limit))]


def job_by_key(client, request_key):
    row = _row("SELECT * FROM jobs WHERE client = ? AND request_key = ? ORDER BY rowid DESC LIMIT 1",
               (client, request_key))
    return _job_row(row) if row else None


def queued_jobs():
    """Jobs waiting their turn, oldest first."""
    return [_job_row(r) for r in _rows("SELECT * FROM jobs WHERE status IN ('queued', 'waiting_for_usage') "
                                       "ORDER BY created, rowid")]


# --- agent runs and spend ---------------------------------------------------------

def start_run(agent, model, project=None, version=None, doing="", seat=None):
    """seat: which character ran it on the map (builder-2); the agent name when there is one seat."""
    cur = _exec("INSERT INTO runs (project, version, agent, model, doing, started, seat) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (project, version, agent, model, doing, now(), seat or agent))
    return cur.lastrowid


def finish_run(run_id, cost_usd, turns=None, ok=True, error=None):
    _exec("UPDATE runs SET ended = ?, ok = ?, turns = ?, cost_usd = ?, error = ? WHERE id = ?",
          (now(), int(bool(ok)), turns, float(cost_usd or 0), error, run_id))


def move_chat_turn(old_project, new_project, since):
    """Move one chat turn from the model that was open to the model it just started: the person's
    messages and the director's chat runs (what the chat cost) written at or after since.
    old_project may be None (no model existed yet). Only the person's messages and the director's
    'chatting' runs move, so a build report for the old model that lands meanwhile stays put."""
    _exec("UPDATE messages SET project = ? WHERE project IS ? AND role = 'user' AND created >= ?",
          (new_project, old_project, since))
    _exec("UPDATE runs SET project = ? WHERE project IS ? AND agent = 'director' AND doing = 'chatting' "
          "AND started >= ?", (new_project, old_project, since))


def runs(project=None, limit=100):
    if project:
        return _rows("SELECT * FROM runs WHERE project = ? ORDER BY id DESC LIMIT ?", (project, limit))
    return _rows("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))


def spend_where(project=None, since=None, seat=None, version=None):
    """Dollars of the runs matching every filter given."""
    sql, args = "SELECT COALESCE(SUM(cost_usd), 0) AS s FROM runs WHERE 1 = 1", []
    for column, value in (("project", project), ("seat", seat), ("version", version)):
        if value is not None:
            sql += f" AND {column} = ?"
            args.append(value)
    if since:
        sql += " AND started >= ?"
        args.append(since)
    return round(_row(sql, args)["s"], 4)


def spend(project=None):
    """Dollars spent on this model (all its runs) and today (every run started today)."""
    today = datetime.now().date().isoformat()
    day = _row("SELECT COALESCE(SUM(cost_usd), 0) AS s FROM runs WHERE substr(started, 1, 10) = ?", (today,))
    model = _row("SELECT COALESCE(SUM(cost_usd), 0) AS s FROM runs WHERE project = ?", (project,)) if project else None
    return {"model": round(model["s"], 4) if model else 0.0, "today": round(day["s"], 4)}
