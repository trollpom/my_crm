"""Слой базы данных: SQLite-схема, подключение и импорт старых данных."""
import datetime
import json
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "notion.db")
OLD_TASKS = os.path.join(BASE_DIR, "tasks.json")

STATUSES = ("todo", "doing", "done", "cancelled")
PRIORITIES = ("high", "normal", "low")

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT '',
    color       TEXT DEFAULT '#5b8cff',
    folder      TEXT,
    created     TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    description  TEXT DEFAULT '',
    project_id   INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    status       TEXT DEFAULT 'todo',
    priority     TEXT DEFAULT 'normal',
    deadline     TEXT,
    start_date   TEXT,
    progress     INTEGER DEFAULT 0,
    remind_at    TEXT,
    reminded_at  TEXT,
    source_uid   INTEGER,
    source_subj  TEXT,
    created      TEXT,
    updated      TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS activity (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    kind       TEXT DEFAULT 'note',
    source     TEXT DEFAULT 'user',
    title      TEXT DEFAULT '',
    body       TEXT DEFAULT '',
    mail_uid   INTEGER,
    seen       INTEGER DEFAULT 1,
    created    TEXT
);

CREATE TABLE IF NOT EXISTS thread_watch (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    project_id  INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    anchor_uid  INTEGER NOT NULL,
    subject     TEXT DEFAULT '',
    last_count  INTEGER DEFAULT 0,
    created     TEXT,
    last_checked TEXT
);

CREATE TABLE IF NOT EXISTS notes (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    title   TEXT DEFAULT '',
    content TEXT DEFAULT '',
    created TEXT,
    updated TEXT
);

CREATE TABLE IF NOT EXISTS mail_cache (
    uid     INTEGER PRIMARY KEY,
    subject TEXT,
    sender  TEXT,
    date    TEXT,
    body    TEXT,
    folder  TEXT DEFAULT 'INBOX',
    flagged INTEGER DEFAULT 0,
    seen    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS attachments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    mail_uid   INTEGER,
    filename   TEXT,
    path       TEXT,
    mime       TEXT,
    size       INTEGER,
    created    TEXT
);

CREATE TABLE IF NOT EXISTS ai_conversations (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT,
    kind    TEXT DEFAULT 'assistant',
    created TEXT,
    updated TEXT
);

CREATE TABLE IF NOT EXISTS ai_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_id     INTEGER REFERENCES ai_conversations(id) ON DELETE CASCADE,
    role        TEXT,
    content     TEXT,
    context_ref TEXT,
    created     TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def now_iso():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        _migrate_add_columns(conn)
        conn.commit()
        _migrate_old_tasks(conn)
    finally:
        conn.close()


def _migrate_add_columns(conn):
    """Добавляет колонки, появившиеся в схеме после создания БД."""
    for table, columns in {
        "projects": {"folder": "TEXT"},
        "tasks": {"remind_at": "TEXT", "reminded_at": "TEXT"},
        "mail_cache": {"flagged": "INTEGER DEFAULT 0", "seen": "INTEGER DEFAULT 0"},
        "attachments": {
            "task_id": "INTEGER", "project_id": "INTEGER", "mail_uid": "INTEGER",
            "filename": "TEXT", "path": "TEXT", "mime": "TEXT",
            "size": "INTEGER", "created": "TEXT",
        },
        "ai_conversations": {"name": "TEXT", "kind": "TEXT DEFAULT 'assistant'",
                             "created": "TEXT", "updated": "TEXT"},
        "ai_messages": {"conv_id": "INTEGER", "role": "TEXT", "content": "TEXT",
                        "context_ref": "TEXT", "created": "TEXT"},
        "activity": {
            "task_id": "INTEGER", "project_id": "INTEGER", "kind": "TEXT DEFAULT 'note'",
            "source": "TEXT DEFAULT 'user'", "title": "TEXT DEFAULT ''",
            "body": "TEXT DEFAULT ''", "mail_uid": "INTEGER", "created": "TEXT",
            "seen": "INTEGER DEFAULT 1",
        },
    }.items():
        try:
            existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.Error:
            continue
        for col, decl in columns.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def _setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def _set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def _migrate_old_tasks(conn):
    """Один раз переносит задачи и проекты из tasks.json."""
    if _setting(conn, "migrated_from_json") == "1":
        return
    if not os.path.exists(OLD_TASKS):
        _set_setting(conn, "migrated_from_json", "1")
        conn.commit()
        return

    try:
        with open(OLD_TASKS, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return

    status_map = {"open": "todo", "done": "done", "cancelled": "cancelled"}
    project_ids = {}

    for name in (data.get("projects") or {}).keys():
        cur = conn.execute(
            "INSERT OR IGNORE INTO projects(name, created) VALUES(?, ?)",
            (name, now_iso()),
        )
        if cur.lastrowid:
            project_ids[name] = cur.lastrowid
        else:
            project_ids[name] = conn.execute(
                "SELECT id FROM projects WHERE name=?", (name,)
            ).fetchone()["id"]

    for t in data.get("tasks", []):
        proj = t.get("project")
        proj_id = None
        if proj:
            if proj not in project_ids:
                cur = conn.execute(
                    "INSERT INTO projects(name, created) VALUES(?, ?)", (proj, now_iso())
                )
                project_ids[proj] = cur.lastrowid
            proj_id = project_ids[proj]

        status = status_map.get(t.get("status", "open"), "todo")
        conn.execute(
            """INSERT INTO tasks
               (title, description, project_id, status, priority, deadline,
                start_date, progress, created, updated, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                t.get("title", "(без названия)"),
                t.get("notes", ""),
                proj_id,
                status,
                t.get("priority", "normal"),
                t.get("deadline"),
                t.get("deadline"),
                100 if status == "done" else 0,
                t.get("created", now_iso()),
                now_iso(),
                t.get("created") if status == "done" else None,
            ),
        )

    _set_setting(conn, "migrated_from_json", "1")
    conn.commit()
