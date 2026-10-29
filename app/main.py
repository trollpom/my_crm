"""FastAPI-приложение: планировщик задач, канбан, календарь, почта, графики."""
import datetime
import os
import threading
import time

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import ai, config, db, files, mail

BASE_DIR = db.BASE_DIR
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

app = FastAPI(title="Локальный планировщик")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def _startup():
    db.init_db()
    # первая фоновая синхронизация почты (плюс обновления флагов)
    try:
        mail.sync_full(background=True)
    except Exception:
        pass
    # фоновый мониторинг наблюдаемых веток писем
    threading.Thread(target=_monitor_loop, daemon=True).start()
    # фоновые напоминания о задачах
    threading.Thread(target=_remind_loop, daemon=True).start()


# ------------------------------------------------------------------ helpers
def _task_dict(row):
    d = dict(row)
    return d


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


def _fetch_task(conn, tid):
    row = conn.execute(
        """SELECT t.*, p.name AS project_name, p.color AS project_color
           FROM tasks t LEFT JOIN projects p ON p.id = t.project_id
           WHERE t.id = ?""",
        (tid,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Задача не найдена")
    return dict(row)


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value)
    except ValueError:
        return None


# ------------------------------------------------------------------ ход действий
STATUS_RU = {"todo": "Новая", "doing": "В работе", "done": "Готово", "cancelled": "Отменена"}
PRIO_RU = {"high": "Высокий", "normal": "Обычный", "low": "Низкий"}


def _add_activity(conn, task_id=None, project_id=None, kind="note", source="user",
                  title="", body="", mail_uid=None, created=None):
    # записи ИИ и писем — «непрочитанные» (кружок на карточке), пока не открыли журнал
    seen = 0 if source in ("mail", "ai") else 1
    cur = conn.execute(
        """INSERT INTO activity(task_id, project_id, kind, source, title, body, mail_uid, seen, created)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (task_id, project_id, kind, source, (title or "")[:300], (body or "")[:8000],
         mail_uid, seen, created or db.now_iso()),
    )
    conn.commit()
    return cur.lastrowid


@app.get("/api/activity/unread")
def activity_unread():
    """Счётчики непрочитанных записей хода действий по задачам и проектам."""
    conn = db.get_conn()
    try:
        tasks, projects = {}, {}
        rows = conn.execute(
            """SELECT task_id, project_id, COUNT(*) AS n FROM activity
               WHERE seen = 0 GROUP BY task_id, project_id"""
        ).fetchall()
        for r in rows:
            if r["task_id"]:
                tasks[r["task_id"]] = tasks.get(r["task_id"], 0) + r["n"]
            if r["project_id"]:
                projects[r["project_id"]] = projects.get(r["project_id"], 0) + r["n"]
        # непрочитанное в задачах проекта тоже считаем непрочитанным у проекта
        rows = conn.execute(
            """SELECT t.project_id AS pid, COUNT(*) AS n FROM activity a
               JOIN tasks t ON t.id = a.task_id
               WHERE a.seen = 0 AND t.project_id IS NOT NULL
               GROUP BY t.project_id"""
        ).fetchall()
        for r in rows:
            projects[r["pid"]] = projects.get(r["pid"], 0) + r["n"]
        return {"tasks": tasks, "projects": projects,
                "total": sum(tasks.values()) + sum(projects.values())}
    finally:
        conn.close()


@app.post("/api/activity/seen")
def activity_seen(payload: dict = Body(default={})):
    """Отмечает записи прочитанными: {task_id} | {project_id} | {all:true}."""
    conn = db.get_conn()
    try:
        n = 0
        if payload.get("all"):
            cur = conn.execute("UPDATE activity SET seen=1 WHERE seen=0")
            n = cur.rowcount
        elif payload.get("task_id"):
            cur = conn.execute(
                "UPDATE activity SET seen=1 WHERE seen=0 AND task_id=?",
                (int(payload["task_id"]),),
            )
            n = cur.rowcount
        elif payload.get("project_id"):
            pid = int(payload["project_id"])
            cur = conn.execute(
                """UPDATE activity SET seen=1 WHERE seen=0 AND
                   (project_id=? OR task_id IN (SELECT id FROM tasks WHERE project_id=?))""",
                (pid, pid),
            )
            n = cur.rowcount
        conn.commit()
        return {"ok": True, "marked": n}
    finally:
        conn.close()


# ------------------------------------------------------------------ напоминания
def reminder_tick():
    """Ставит в ход действий напоминание по задачам с наступившим remind_at."""
    conn = db.get_conn()
    try:
        now = db.now_iso()
        rows = conn.execute(
            """SELECT id, title, project_id, deadline FROM tasks
               WHERE remind_at IS NOT NULL AND reminded_at IS NULL
                 AND remind_at <= ? AND status NOT IN ('done', 'cancelled')""",
            (now,),
        ).fetchall()
        for r in rows:
            conn.execute("UPDATE tasks SET reminded_at=? WHERE id=?", (now, r["id"]))
            _add_activity(
                conn, task_id=r["id"], project_id=r["project_id"],
                kind="remind", source="system", title="Напоминание",
                body=f"Напоминаю: «{r['title']}»"
                     + (f" · дедлайн {r['deadline']}" if r["deadline"] else ""),
            )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def _remind_loop():
    time.sleep(20)
    while True:
        try:
            reminder_tick()
        except Exception:
            pass
        time.sleep(30)


@app.get("/api/reminders")
def reminders():
    """Просроченные, сегодняшние, ближайшие дедлайны и сработавшие напоминания."""
    today = datetime.date.today().isoformat()
    soon = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
    day_ago = (datetime.datetime.now() - datetime.timedelta(hours=24)).replace(microsecond=0).isoformat()

    def item(r):
        return {"id": r["id"], "title": r["title"], "deadline": r["deadline"],
                "remind_at": r["remind_at"], "project_name": r["project_name"]}

    conn = db.get_conn()
    try:
        base = ("SELECT t.*, p.name AS project_name FROM tasks t "
                "LEFT JOIN projects p ON p.id = t.project_id "
                "WHERE t.status NOT IN ('done', 'cancelled')")
        overdue = [item(r) for r in conn.execute(
            base + " AND t.deadline IS NOT NULL AND substr(t.deadline,1,10) < ? "
            "ORDER BY t.deadline LIMIT 20", (today,)).fetchall()]
        today_l = [item(r) for r in conn.execute(
            base + " AND t.deadline IS NOT NULL AND substr(t.deadline,1,10) = ? "
            "ORDER BY t.deadline LIMIT 20", (today,)).fetchall()]
        upcoming = [item(r) for r in conn.execute(
            base + " AND t.deadline IS NOT NULL AND substr(t.deadline,1,10) > ? "
            "AND substr(t.deadline,1,10) <= ? ORDER BY t.deadline LIMIT 20",
            (today, soon)).fetchall()]
        fired = [item(r) for r in conn.execute(
            base + " AND t.reminded_at IS NOT NULL AND t.reminded_at >= ? "
            "ORDER BY t.reminded_at DESC LIMIT 10", (day_ago,)).fetchall()]
        for f in fired:
            row = conn.execute("SELECT reminded_at FROM tasks WHERE id=?", (f["id"],)).fetchone()
            f["reminded_at"] = row["reminded_at"] if row else None
        return {"overdue": overdue, "today": today_l, "upcoming": upcoming,
                "fired": fired, "count": len(overdue) + len(today_l)}
    finally:
        conn.close()


def _activity_rows(conn, task_id=None, project_id=None, limit=300):
    where, params = [], []
    if task_id:
        where.append("a.task_id = ?")
        params.append(task_id)
    if project_id:
        where.append("a.project_id = ?")
        params.append(project_id)
    if not where:
        return []
    rows = conn.execute(
        f"""SELECT a.*, t.title AS task_title FROM activity a
            LEFT JOIN tasks t ON t.id = a.task_id
            WHERE {' AND '.join(where)}
            ORDER BY (a.created IS NULL), a.created, a.id LIMIT ?""",
        params + [limit],
    ).fetchall()
    return [dict(r) for r in rows]


def _has_activity(conn, mail_uid, task_id=None, project_id=None, source=None):
    sql = "SELECT id FROM activity WHERE mail_uid = ?"
    params = [mail_uid]
    if task_id:
        sql += " AND task_id = ?"
        params.append(task_id)
    if project_id:
        sql += " AND project_id = ?"
        params.append(project_id)
    if source:
        sql += " AND source = ?"
        params.append(source)
    return conn.execute(sql, params).fetchone() is not None


def _activity_from_thread(conn, uid, task_id=None, project_id=None, analyze=True, force=False):
    """Добавляет в ход действий записи по письму и всей его ветке.

    analyze=True — ИИ делает краткую заметку по каждому письму ветки.
    force=True — перезаписывает уже добавленные ранее записи.
    """
    result = {"added": 0, "skipped": 0, "thread": 0, "ai": False, "note": ""}
    thread = mail.thread_messages(uid)
    if not thread:
        return result
    result["thread"] = thread.get("count", 0)
    messages = thread.get("messages") or []
    if not messages:
        return result

    # у писем в кэше обычно нет тела — докачиваем из IMAP (до записей и анализа ИИ)
    if not force:
        missing = [m for m in messages if not (m.get("body") or "").strip()]
    else:
        missing = []
    if missing or (analyze and ai.enabled()):
        for m in messages:
            if (m.get("body") or "").strip():
                continue
            try:
                full = mail.get_message(m["uid"], cache=False) or {}
                m["body"] = (full.get("body") or "")[:4000]
            except Exception:
                pass

    for m in messages:
        if _has_activity(conn, m["uid"], task_id, project_id, source="mail") and not force:
            result["skipped"] += 1
            continue
        if force:
            conn.execute(
                "DELETE FROM activity WHERE mail_uid=? AND source='mail' AND task_id IS ? AND project_id IS ?",
                (m["uid"], task_id, project_id),
            )
        _add_activity(
            conn, task_id=task_id, project_id=project_id, kind="mail", source="mail",
            title="Письмо от " + (m.get("sender") or "?"),
            body=(m.get("body") or "")[:500], mail_uid=m["uid"], created=m.get("date") or None,
        )
        result["added"] += 1

    if analyze and ai.enabled():
        res = ai.summarize_thread(messages, target_title=thread.get("subject", ""))
        if res.get("ok"):
            result["ai"] = True
            result["note"] = res.get("note", "")
            if res.get("note"):
                _add_activity(
                    conn, task_id=task_id, project_id=project_id, kind="ai", source="ai",
                    title="Сводка по переписке", body=res["note"],
                )
            for e in res.get("entries") or []:
                if _has_activity(conn, e["uid"], task_id, project_id, source="ai") and not force:
                    result["skipped"] += 1
                    continue
                if force:
                    conn.execute(
                        "DELETE FROM activity WHERE mail_uid=? AND source='ai' AND task_id IS ? AND project_id IS ?",
                        (e["uid"], task_id, project_id),
                    )
                title = e.get("title") or "Событие"
                who = e.get("sender") or ""
                body = e.get("body") or ""
                if who:
                    body = f"{body}\n\n({who})"
                _add_activity(
                    conn, task_id=task_id, project_id=project_id, kind="ai", source="ai",
                    title=title, body=body, mail_uid=e["uid"], created=e.get("date") or None,
                )
                result["added"] += 1
        else:
            result["error"] = res.get("error")
    return result


def _watch_add(conn, anchor_uid, task_id=None, project_id=None, subject=""):
    """Ставит ветку писем под наблюдение: новые письма темы автоматически
    попадают в ход действий задачи/проекта (с разбором ИИ)."""
    if not anchor_uid:
        return
    thread = None
    try:
        thread = mail.thread_messages(int(anchor_uid))
    except Exception:
        thread = None
    count = thread.get("count", 0) if thread else 0
    subject = subject or (thread.get("subject") if thread else "") or ""
    row = conn.execute(
        """SELECT id FROM thread_watch
           WHERE anchor_uid = ? AND COALESCE(task_id, 0) = COALESCE(?, 0)
             AND COALESCE(project_id, 0) = COALESCE(?, 0)""",
        (anchor_uid, task_id, project_id),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE thread_watch SET subject = ?, last_checked = ? WHERE id = ?",
            (subject, db.now_iso(), row["id"]),
        )
    else:
        conn.execute(
            """INSERT INTO thread_watch
               (task_id, project_id, anchor_uid, subject, last_count, created, last_checked)
               VALUES(?, ?, ?, ?, ?, ?, ?)""",
            (task_id, project_id, anchor_uid, subject, count,
             db.now_iso(), db.now_iso()),
        )
    conn.commit()


def _watch_item(conn, r):
    d = dict(r)
    try:
        thread = mail.thread_messages(d["anchor_uid"]) or {}
    except Exception:
        thread = {}
    d["thread_count"] = thread.get("count", d.get("last_count") or 0)
    d["thread_subject"] = thread.get("subject") or d.get("subject") or ""
    return d


def _monitor_watch(conn, w):
    """Одна проверка ветки: новые письма → записи + ИИ-анализ новых."""
    thread = mail.thread_messages(w["anchor_uid"])
    if not thread:
        conn.execute(
            "UPDATE thread_watch SET last_checked=? WHERE id=?",
            (db.now_iso(), w["id"]),
        )
        conn.commit()
        return {"new": 0, "added": 0}
    messages = thread.get("messages") or []
    count = thread.get("count", len(messages))
    new = [
        m for m in messages
        if not _has_activity(conn, m["uid"], w["task_id"], w["project_id"], source="mail")
    ]
    added = 0
    if new:
        # докачать тела новых писем из IMAP (в т.ч. из Sent)
        for m in new:
            if (m.get("body") or "").strip():
                continue
            try:
                full = mail.get_message(m["uid"], cache=False) or {}
                m["body"] = (full.get("body") or "")[:4000]
            except Exception:
                pass
        for m in new:
            _add_activity(
                conn, task_id=w["task_id"], project_id=w["project_id"],
                kind="mail", source="mail",
                title="Письмо от " + (m.get("sender") or "?"),
                body=(m.get("body") or "")[:500],
                mail_uid=m["uid"], created=m.get("date") or None,
            )
            added += 1
        if ai.enabled():
            res = ai.summarize_thread(
                new, target_title=w.get("subject") or thread.get("subject", "")
            )
            if res.get("ok"):
                if res.get("note"):
                    _add_activity(
                        conn, task_id=w["task_id"], project_id=w["project_id"],
                        kind="ai", source="ai", title="Новая переписка",
                        body=res["note"],
                    )
                    added += 1
                for e in res.get("entries") or []:
                    if _has_activity(conn, e["uid"], w["task_id"],
                                     w["project_id"], source="ai"):
                        continue
                    body = e.get("body") or ""
                    who = e.get("sender") or ""
                    if who:
                        body = f"{body}\n\n({who})"
                    _add_activity(
                        conn, task_id=w["task_id"], project_id=w["project_id"],
                        kind="ai", source="ai", title=e.get("title") or "Событие",
                        body=body, mail_uid=e["uid"], created=e.get("date") or None,
                    )
                    added += 1
    conn.execute(
        "UPDATE thread_watch SET last_count=?, last_checked=? WHERE id=?",
        (count, db.now_iso(), w["id"]),
    )
    conn.commit()
    return {"new": len(new), "added": added}


_monitor_state = {"running": False, "last": None, "added": 0, "watches": 0}


def monitor_once():
    """Один проход по всем наблюдаемым веткам (можно вызывать вручную)."""
    if _monitor_state.get("running"):
        return {"running": True, "watches": 0, "new": 0, "added": 0}
    _monitor_state.update(running=True)
    result = {"watches": 0, "new": 0, "added": 0}
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT * FROM thread_watch ORDER BY id").fetchall()
        for r in rows:
            w = dict(r)
            try:
                res = _monitor_watch(conn, w)
            except Exception:
                continue
            result["watches"] += 1
            result["new"] += res.get("new", 0)
            result["added"] += res.get("added", 0)
    finally:
        conn.close()
        _monitor_state.update(
            running=False, last=db.now_iso(),
            added=result["added"], watches=result["watches"],
        )
    return result


def _monitor_loop():
    time.sleep(60)  # первый прогон — после старта и начальной синхронизации
    while True:
        try:
            monitor_once()
        except Exception:
            pass
        time.sleep(300)  # дальше каждые 5 минут


@app.post("/api/monitor/run")
def monitor_run():
    """Срочная проверка наблюдаемых веток (вместо ожидания фонового цикла)."""
    return monitor_once()


TASK_LOG_FIELDS = ("title", "status", "priority", "deadline", "start_date", "progress", "project_id", "description")


def _log_task_changes(conn, old, new):
    changes = []
    for f in TASK_LOG_FIELDS:
        a, b = old.get(f), new.get(f)
        if f == "description":
            continue
        if (a or "") == (b or ""):
            continue
        if f == "status":
            changes.append(f"Статус: {STATUS_RU.get(a, a or '—')} → {STATUS_RU.get(b, b or '—')}")
        elif f == "priority":
            changes.append(f"Приоритет: {PRIO_RU.get(a, a or '—')} → {PRIO_RU.get(b, b or '—')}")
        elif f == "project_id":
            changes.append(f"Проект: {old.get('project_name') or '—'} → {new.get('project_name') or '—'}")
        elif f == "progress":
            changes.append(f"Прогресс: {a or 0}% → {b or 0}%")
        elif f == "deadline":
            changes.append(f"Дедлайн: {a or '—'} → {b or '—'}")
        elif f == "start_date":
            changes.append(f"Начало: {a or '—'} → {b or '—'}")
        elif f == "title":
            changes.append(f"Название: «{a}» → «{b}»")
    if changes:
        _add_activity(
            conn, task_id=old.get("id"), project_id=old.get("project_id") or new.get("project_id"),
            kind="change", source="system", title="Изменения", body="\n".join(changes),
        )


# ------------------------------------------------------------------ tasks
@app.get("/api/tasks")
def list_tasks(status: str = None, project_id: int = None):
    conn = db.get_conn()
    try:
        sql = (
            "SELECT t.*, p.name AS project_name, p.color AS project_color "
            "FROM tasks t LEFT JOIN projects p ON p.id = t.project_id WHERE 1=1"
        )
        params = []
        if status:
            sql += " AND t.status = ?"
            params.append(status)
        if project_id:
            sql += " AND t.project_id = ?"
            params.append(project_id)
        sql += " ORDER BY (t.deadline IS NULL), t.deadline, t.id"
        return _rows(conn.execute(sql, params))
    finally:
        conn.close()


@app.post("/api/tasks")
def create_task(payload: dict = Body(...)):
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "Не задано название задачи")
    conn = db.get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO tasks
               (title, description, project_id, status, priority, deadline,
                start_date, progress, remind_at, source_uid, source_subj, created, updated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                title,
                payload.get("description", ""),
                payload.get("project_id"),
                payload.get("status", "todo"),
                payload.get("priority", "normal"),
                payload.get("deadline"),
                payload.get("start_date"),
                int(payload.get("progress", 0) or 0),
                payload.get("remind_at"),
                payload.get("source_uid"),
                payload.get("source_subj"),
                db.now_iso(),
                db.now_iso(),
            ),
        )
        conn.commit()
        task = _fetch_task(conn, cur.lastrowid)
        _add_activity(
            conn, task_id=task["id"], project_id=task.get("project_id"),
            kind="create", source="system", title="Задача создана",
            body=(task.get("description") or "")[:500] or task["title"],
        )
        if payload.get("source_uid"):
            try:
                _activity_from_thread(
                    conn, int(payload["source_uid"]), task_id=task["id"],
                    project_id=task.get("project_id"), analyze=True,
                )
                _watch_add(
                    conn, int(payload["source_uid"]),
                    task_id=task["id"], project_id=task.get("project_id"),
                    subject=payload.get("source_subj") or "",
                )
            except Exception:
                pass
        return _fetch_task(conn, task["id"])
    finally:
        conn.close()


ALLOWED_TASK_FIELDS = {
    "title", "description", "project_id", "status", "priority",
    "deadline", "start_date", "progress", "source_uid", "source_subj",
    "remind_at",
}


@app.patch("/api/tasks/{tid}")
def update_task(tid: int, payload: dict = Body(...)):
    conn = db.get_conn()
    try:
        old = _fetch_task(conn, tid)
        fields = {k: v for k, v in payload.items() if k in ALLOWED_TASK_FIELDS}
        if "status" in fields:
            if fields["status"] == "done":
                fields["progress"] = 100
                fields["completed_at"] = db.now_iso()
            elif fields["status"] in ("todo", "doing"):
                fields["completed_at"] = None
        if "project_id" in fields and fields["project_id"] == "":
            fields["project_id"] = None
        if "remind_at" in fields and fields["remind_at"] != (old.get("remind_at") or ""):
            fields["reminded_at"] = None  # новое время напоминания — сброс срабатывания
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(
                f"UPDATE tasks SET {sets}, updated = ? WHERE id = ?",
                list(fields.values()) + [db.now_iso(), tid],
            )
            conn.commit()
            new = _fetch_task(conn, tid)
            try:
                _log_task_changes(conn, old, new)
            except Exception:
                pass
        return _fetch_task(conn, tid)
    finally:
        conn.close()


@app.get("/api/tasks/{tid}/activity")
def task_activity(tid: int):
    conn = db.get_conn()
    try:
        _fetch_task(conn, tid)
        return _activity_rows(conn, task_id=tid)
    finally:
        conn.close()


@app.post("/api/tasks/{tid}/activity")
def add_task_activity(tid: int, payload: dict = Body(default={})):
    text = (payload.get("body") or "").strip()
    title = (payload.get("title") or "").strip()
    if not text and not title:
        raise HTTPException(400, "Пустая запись")
    conn = db.get_conn()
    try:
        task = _fetch_task(conn, tid)
        _add_activity(
            conn, task_id=tid, project_id=task.get("project_id"),
            kind=payload.get("kind", "note"), source="user",
            title=title or "Заметка", body=text,
        )
        return _activity_rows(conn, task_id=tid)
    finally:
        conn.close()


@app.post("/api/tasks/{tid}/activity/from-mail")
def task_activity_from_mail(tid: int, payload: dict = Body(default={})):
    """Собирает ход действий по ветке письма (или по письму-источнику задачи)."""
    conn = db.get_conn()
    try:
        task = _fetch_task(conn, tid)
        uid = payload.get("uid") or task.get("source_uid")
        force = bool(payload.get("force"))
        res = {"added": 0, "thread": 0, "ai": False}
        if uid:
            res = _activity_from_thread(
                conn, int(uid), task_id=tid, project_id=task.get("project_id"),
                analyze=True, force=force,
            )
            _watch_add(conn, int(uid), task_id=tid,
                       project_id=task.get("project_id"))
        elif task.get("source_subj"):
            thread = mail.thread_by_subject(task["source_subj"])
            if thread and thread.get("messages"):
                res = _activity_from_thread(
                    conn, thread["messages"][-1]["uid"], task_id=tid,
                    project_id=task.get("project_id"), analyze=True, force=force,
                )
                _watch_add(conn, thread["messages"][-1]["uid"],
                           task_id=tid, project_id=task.get("project_id"),
                           subject=task["source_subj"])
        return {**res, "items": _activity_rows(conn, task_id=tid)}
    finally:
        conn.close()


@app.get("/api/tasks/{tid}/activity/watches")
def task_activity_watches(tid: int):
    """Ветки писем, наблюдаемые автоматически для задачи."""
    conn = db.get_conn()
    try:
        _fetch_task(conn, tid)
        rows = conn.execute(
            "SELECT * FROM thread_watch WHERE task_id=? ORDER BY id", (tid,)
        ).fetchall()
        return {"watches": [_watch_item(conn, r) for r in rows],
                "monitor": dict(_monitor_state)}
    finally:
        conn.close()


@app.delete("/api/tasks/{tid}")
def delete_task(tid: int):
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM tasks WHERE id = ?", (tid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ------------------------------------------------------------------ config folders
@app.get("/api/config/folders")
def get_folders():
    return {"projects_base": config.projects_base()}


@app.post("/api/config/folders")
def set_folders(payload: dict = Body(...)):
    path = (payload.get("projects_base") or "").strip().rstrip("\\/")
    if not path:
        raise HTTPException(400, "Пустой путь")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        raise HTTPException(400, f"Невозможно создать папку: {e}")
    cfg = config.load_config()
    folders = cfg.get("folders") or {}
    folders["projects_base"] = path
    cfg["folders"] = folders
    config.save_config(cfg)
    return {"projects_base": config.projects_base()}


# ------------------------------------------------------------------ projects
@app.get("/api/projects")
def list_projects():
    conn = db.get_conn()
    try:
        rows = _rows(conn.execute("SELECT * FROM projects ORDER BY name"))
        for p in rows:
            stats = conn.execute(
                """SELECT
                     COUNT(*) AS total,
                     SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done,
                     SUM(CASE WHEN status IN ('todo','doing') THEN 1 ELSE 0 END) AS open
                   FROM tasks WHERE project_id = ?""",
                (p["id"],),
            ).fetchone()
            p["total"] = stats["total"] or 0
            p["done"] = stats["done"] or 0
            p["open"] = stats["open"] or 0
            p["activity_count"] = conn.execute(
                "SELECT COUNT(*) AS n FROM activity WHERE project_id = ?", (p["id"],)
            ).fetchone()["n"]
            if not p.get("folder"):
                try:
                    folder = files.project_default_folder(p["name"])
                    conn.execute("UPDATE projects SET folder = ? WHERE id = ?", (folder, p["id"]))
                    conn.commit()
                    p["folder"] = folder
                except Exception:
                    p["folder"] = None
        return rows
    finally:
        conn.close()


@app.post("/api/projects")
def create_project(payload: dict = Body(...)):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Не задано имя проекта")
    conn = db.get_conn()
    try:
        try:
            cur = conn.execute(
            "INSERT INTO projects(name, description, color, folder, created) VALUES(?, ?, ?, ?, ?)",
            (
                name,
                payload.get("description", ""),
                payload.get("color", "#5b8cff"),
                payload.get("folder"),
                db.now_iso(),
            ),
        )
        except Exception:
            raise HTTPException(400, "Проект с таким именем уже существует")
        pid = cur.lastrowid
        conn.commit()
        row = dict(conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone())
        try:
            folder = files.project_default_folder(row["name"])
            conn.execute("UPDATE projects SET folder = ? WHERE id = ?", (folder, pid))
            conn.commit()
            row["folder"] = folder
        except Exception:
            pass
        return row
    finally:
        conn.close()


@app.patch("/api/projects/{pid}")
def update_project(pid: int, payload: dict = Body(...)):
    conn = db.get_conn()
    try:
        fields = {k: v for k, v in payload.items() if k in ("name", "description", "color", "folder")}
        if fields:
            if "folder" in fields and fields["folder"]:
                try:
                    import os

                    os.makedirs(fields["folder"], exist_ok=True)
                except Exception:
                    pass
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE projects SET {sets} WHERE id = ?", list(fields.values()) + [pid])
            conn.commit()
        row = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        if not row:
            raise HTTPException(404, "Проект не найден")
        return dict(row)
    finally:
        conn.close()


@app.delete("/api/projects/{pid}")
def delete_project(pid: int):
    conn = db.get_conn()
    try:
        conn.execute("UPDATE tasks SET project_id=NULL WHERE project_id=?", (pid,))
        conn.execute("DELETE FROM projects WHERE id = ?", (pid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/projects/{pid}/activity")
def project_activity(pid: int):
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT id FROM projects WHERE id=?", (pid,)).fetchone()
        if not row:
            raise HTTPException(404, "Проект не найден")
        return _activity_rows(conn, project_id=pid)
    finally:
        conn.close()


@app.post("/api/projects/{pid}/activity")
def add_project_activity(pid: int, payload: dict = Body(default={})):
    text = (payload.get("body") or "").strip()
    title = (payload.get("title") or "").strip()
    if not text and not title:
        raise HTTPException(400, "Пустая запись")
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT id FROM projects WHERE id=?", (pid,)).fetchone()
        if not row:
            raise HTTPException(404, "Проект не найден")
        _add_activity(conn, project_id=pid, kind=payload.get("kind", "note"),
                      source="user", title=title or "Заметка", body=text)
        return _activity_rows(conn, project_id=pid)
    finally:
        conn.close()


@app.post("/api/projects/{pid}/activity/from-mail")
def project_activity_from_mail(pid: int, payload: dict = Body(default={})):
    """Собирает ход действий проекта по ветке письма (uid) или по письмам его задач."""
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT id FROM projects WHERE id=?", (pid,)).fetchone()
        if not row:
            raise HTTPException(404, "Проект не найден")
        force = bool(payload.get("force"))
        total = {"added": 0, "thread": 0, "ai": False}
        uids = []
        if payload.get("uid"):
            uids = [int(payload["uid"])]
        else:
            rows = conn.execute(
                "SELECT DISTINCT source_uid FROM tasks "
                "WHERE project_id=? AND source_uid IS NOT NULL LIMIT 30",
                (pid,),
            ).fetchall()
            uids = [r["source_uid"] for r in rows]
        for uid in uids:
            res = _activity_from_thread(conn, int(uid), project_id=pid,
                                        analyze=True, force=force)
            _watch_add(conn, int(uid), project_id=pid)
            total["added"] += res.get("added", 0)
            total["thread"] += res.get("thread", 0)
            total["ai"] = total["ai"] or res.get("ai", False)
        return {**total, "items": _activity_rows(conn, project_id=pid)}
    finally:
        conn.close()


@app.get("/api/projects/{pid}/activity/watches")
def project_activity_watches(pid: int):
    """Ветки писем, наблюдаемые автоматически для проекта."""
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT id FROM projects WHERE id=?", (pid,)).fetchone()
        if not row:
            raise HTTPException(404, "Проект не найден")
        rows = conn.execute(
            "SELECT * FROM thread_watch WHERE project_id=? ORDER BY id", (pid,)
        ).fetchall()
        return {"watches": [_watch_item(conn, r) for r in rows],
                "monitor": dict(_monitor_state)}
    finally:
        conn.close()


@app.delete("/api/activity/{aid}")
def delete_activity(aid: int):
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM activity WHERE id = ?", (aid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ------------------------------------------------------------------ notes
@app.get("/api/notes")
def list_notes():
    conn = db.get_conn()
    try:
        return _rows(conn.execute("SELECT * FROM notes ORDER BY updated DESC"))
    finally:
        conn.close()


@app.post("/api/notes")
def create_note(payload: dict = Body(default={})):
    conn = db.get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO notes(title, content, created, updated) VALUES(?, ?, ?, ?)",
            (payload.get("title", "Без названия"), payload.get("content", ""), db.now_iso(), db.now_iso()),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM notes WHERE id=?", (cur.lastrowid,)).fetchone())
    finally:
        conn.close()


@app.patch("/api/notes/{nid}")
def update_note(nid: int, payload: dict = Body(...)):
    conn = db.get_conn()
    try:
        fields = {k: v for k, v in payload.items() if k in ("title", "content")}
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE notes SET {sets}, updated = ? WHERE id = ?", list(fields.values()) + [db.now_iso(), nid])
            conn.commit()
        row = conn.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
        if not row:
            raise HTTPException(404, "Заметка не найдена")
        return dict(row)
    finally:
        conn.close()


@app.delete("/api/notes/{nid}")
def delete_note(nid: int):
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM notes WHERE id = ?", (nid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ------------------------------------------------------------------ mail
@app.get("/api/mail/list")
def mail_list(
    tab: str = "recent",
    query: str = "",
    days: int = 0,
    sort: str = "newest",
    page: int = 1,
    page_size: int = 50,
):
    return mail.list_messages(
        tab=tab, query=query, days=days, sort=sort, page=page, page_size=page_size
    )


@app.get("/api/mail/counts")
def mail_counts():
    return mail.counts()


@app.post("/api/mail/sync")
def mail_sync(force: bool = False):
    return mail.sync_full(background=True, force_headers=force)


@app.get("/api/mail/sync-status")
def mail_sync_status():
    return mail.sync_status()


@app.get("/api/mail/message/{uid}")
def mail_message(uid: int):
    item = mail.get_message(uid)
    if not item:
        raise HTTPException(404, "Письмо не найдено")
    conn = db.get_conn()
    try:
        saved = _rows(conn.execute(
            "SELECT id, task_id, project_id, filename, path, size FROM attachments WHERE mail_uid = ?",
            (uid,),
        ))
    finally:
        conn.close()
    item["saved_attachments"] = saved
    return item


@app.post("/api/mail/{uid}/to-task")
def mail_to_task(uid: int, payload: dict = Body(default={})):
    item = mail.get_message(uid)
    subject = (item or {}).get("subject") or f"Письмо #{uid}"
    project_id = payload.get("project_id")
    conn = db.get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO tasks
               (title, description, project_id, status, priority, deadline,
                source_uid, source_subj, created, updated)
               VALUES (?, ?, ?, 'todo', ?, ?, ?, ?, ?, ?)""",
            (
                subject,
                (item or {}).get("body", "")[:4000],
                project_id,
                payload.get("priority", "normal"),
                payload.get("deadline"),
                uid,
                subject,
                db.now_iso(),
                db.now_iso(),
            ),
        )
        conn.commit()
        task_id = cur.lastrowid
        task = _fetch_task(conn, task_id)
        _add_activity(
            conn, task_id=task_id, project_id=task.get("project_id"),
            kind="create", source="system", title="Задача создана из письма",
            body=subject, mail_uid=uid,
        )
        log = {"added": 0, "thread": 0, "ai": False}
        try:
            log = _activity_from_thread(
                conn, uid, task_id=task_id, project_id=task.get("project_id"),
                analyze=ai.enabled(),
            )
            _watch_add(conn, uid, task_id=task_id,
                       project_id=task.get("project_id"), subject=subject)
        except Exception:
            pass
    finally:
        conn.close()

    saved = _save_mail_attachments(uid, task_id=task_id, project_id=project_id)
    task["saved_attachments"] = saved
    task["activity_log"] = log
    return task


@app.get("/api/mail/thread/{uid}")
def mail_thread(uid: int):
    """Ветка писем одной темы (для предпросмотра перед записью в ход действий)."""
    thread = mail.thread_messages(uid)
    if not thread:
        raise HTTPException(404, "Письмо не найдено")
    for m in thread.get("messages") or []:
        m["body"] = (m.get("body") or "")[:600]
    return thread


@app.post("/api/mail/{uid}/link")
def mail_link(uid: int, payload: dict = Body(...)):
    """Привязывает письмо к существующей задаче или проекту (+ вложения в папку).

    Заодно ведёт ход действий: письмо и вся ветка темы попадают в журнал
    задачи/проекта, ИИ составляет краткую заметку по каждому письму.
    """
    task_id = payload.get("task_id")
    project_id = payload.get("project_id")
    analyze = payload.get("analyze", True)
    conn = db.get_conn()
    try:
        project_folder = None
        if task_id:
            task = _fetch_task(conn, int(task_id))
            if not project_id:
                project_id = task.get("project_id")
        if project_id:
            row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Проект не найден")
            project_folder = row["folder"] or files.project_default_folder(row["name"])
        if not task_id and not project_id:
            raise HTTPException(400, "Укажите task_id или project_id")

        saved = _save_mail_attachments(
            uid,
            task_id=int(task_id) if task_id else None,
            project_id=int(project_id) if project_id else None,
            folder=project_folder,
        )
        log = {"added": 0, "thread": 0, "ai": False}
        try:
            log = _activity_from_thread(
                conn, uid,
                task_id=int(task_id) if task_id else None,
                project_id=int(project_id) if project_id else None,
                analyze=bool(analyze) and ai.enabled(),
            )
            _watch_add(
                conn, uid,
                task_id=int(task_id) if task_id else None,
                project_id=int(project_id) if project_id else None,
            )
        except Exception:
            pass
        return {"ok": True, "saved_attachments": saved, "folder": project_folder,
                "activity": log}
    finally:
        conn.close()


def _save_mail_attachments(uid, task_id=None, project_id=None, folder=None):
    """Скачивает вложения письма в папку проекта/задачи, сохраняет учёт в БД."""
    try:
        item = mail.get_message(uid, cache=False)
    except Exception:
        item = None
    if not item:
        return []

    att_list = item.get("attachments") or []
    if not att_list:
        return []

    if not folder:
        conn = db.get_conn()
        try:
            if task_id:
                t = conn.execute("SELECT title, project_id FROM tasks WHERE id=?", (task_id,)).fetchone()
                if t and t["project_id"]:
                    row = conn.execute("SELECT name, folder FROM projects WHERE id=?", (t["project_id"],)).fetchone()
                    if row:
                        folder = row["folder"] or files.project_default_folder(row["name"])
                elif t:
                    folder = files.task_folder(t["title"], task_id)
            if not folder and project_id:
                row = conn.execute("SELECT name, folder FROM projects WHERE id=?", (project_id,)).fetchone()
                if row:
                    folder = row["folder"] or files.project_default_folder(row["name"])
        finally:
            conn.close()

    if not folder:
        folder = files.project_default_folder("Без проекта")

    saved = []
    conn = db.get_conn()
    try:
        for att in att_list:
            payload, mime = mail.download_attachment(uid, att["filename"])
            if payload is None:
                continue
            path = files.save_bytes(folder, att["filename"], payload)
            if not path:
                continue
            cur = conn.execute(
                """INSERT INTO attachments(task_id, project_id, mail_uid, filename, path, mime, size, created)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
                (task_id, project_id, uid, att["filename"], path, mime or att.get("mime"),
                 att.get("size"), db.now_iso()),
            )
            conn.commit()
            saved.append({"id": cur.lastrowid, "filename": att["filename"], "path": path})
    finally:
        conn.close()
    return saved


# ------------------------------------------------------------------ analytics
@app.get("/api/stats")
def stats():
    conn = db.get_conn()
    try:
        by_status = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM tasks GROUP BY status")}
        by_priority = {r["priority"]: r["n"] for r in conn.execute(
            "SELECT priority, COUNT(*) AS n FROM tasks GROUP BY priority")}
        by_project = _rows(conn.execute(
            """SELECT COALESCE(p.name, 'Без проекта') AS name, COUNT(*) AS n
               FROM tasks t LEFT JOIN projects p ON p.id = t.project_id
               GROUP BY p.name ORDER BY n DESC"""))

        today = datetime.date.today().isoformat()
        overdue = conn.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE status IN ('todo','doing') "
            "AND deadline IS NOT NULL AND substr(deadline,1,10) < ?",
            (today,),
        ).fetchone()["n"]
        upcoming = conn.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE status IN ('todo','doing') "
            "AND deadline IS NOT NULL AND substr(deadline,1,10) >= ?",
            (today,),
        ).fetchone()["n"]

        done_by_day = _rows(conn.execute(
            """SELECT substr(completed_at,1,10) AS day, COUNT(*) AS n
               FROM tasks WHERE status='done' AND completed_at IS NOT NULL
               GROUP BY day ORDER BY day DESC LIMIT 14"""))
        return {
            "total": sum(by_status.values()),
            "by_status": by_status,
            "by_priority": by_priority,
            "by_project": by_project,
            "overdue": overdue,
            "upcoming": upcoming,
            "done_by_day": list(reversed(done_by_day)),
        }
    finally:
        conn.close()


@app.get("/api/gantt")
def gantt():
    conn = db.get_conn()
    try:
        rows = _rows(conn.execute(
            """SELECT id, title, start_date, deadline, status, progress, priority,
                      project_id, (SELECT name FROM projects WHERE id = tasks.project_id) AS project_name
               FROM tasks
               WHERE (deadline IS NOT NULL OR start_date IS NOT NULL)
               ORDER BY (start_date IS NULL), start_date, deadline"""))
        return rows
    finally:
        conn.close()


# ------------------------------------------------------------------ ai
@app.get("/api/ai/status")
def ai_status():
    cfg = config.ai_config()
    return {
        "enabled": bool(cfg.get("enabled") and cfg.get("api_key")),
        "provider": cfg.get("provider"),
        "model": cfg.get("model"),
        "key_set": bool(cfg.get("api_key")),
    }


@app.get("/api/ai/conversations")
def ai_conversations():
    conn = db.get_conn()
    try:
        return _rows(conn.execute(
            "SELECT * FROM ai_conversations ORDER BY updated DESC, id DESC"
        ))
    finally:
        conn.close()


@app.post("/api/ai/conversations")
def ai_create_conversation(payload: dict = Body(default={})):
    conn = db.get_conn()
    try:
        now = db.now_iso()
        cur = conn.execute(
            "INSERT INTO ai_conversations(name, kind, created, updated) VALUES(?,?,?,?)",
            (payload.get("name") or "Новый диалог", payload.get("kind", "assistant"), now, now),
        )
        conn.commit()
        return {"id": cur.lastrowid, "name": payload.get("name") or "Новый диалог"}
    finally:
        conn.close()


@app.delete("/api/ai/conversations/{cid}")
def ai_delete_conversation(cid: int):
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM ai_conversations WHERE id=?", (cid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/ai/conversations/{cid}/messages")
def ai_messages(cid: int):
    conn = db.get_conn()
    try:
        return _rows(conn.execute(
            "SELECT id, role, content, created FROM ai_messages "
            "WHERE conv_id=? ORDER BY id",
            (cid,),
        ))
    finally:
        conn.close()


@app.post("/api/ai/conversations/{cid}/messages")
def ai_send(cid: int, payload: dict = Body(...)):
    text = (payload.get("content") or "").strip()
    if not text:
        raise HTTPException(400, "Пустое сообщение")
    reply = ai.assistant_reply(cid, text)
    conn = db.get_conn()
    try:
        now = db.now_iso()
        conn.execute(
            "INSERT INTO ai_messages(conv_id, role, content, created) VALUES(?,?,?,?)",
            (cid, "user", text, now),
        )
        conn.execute(
            "INSERT INTO ai_messages(conv_id, role, content, created) VALUES(?,?,?,?)",
            (cid, "assistant", reply, now),
        )
        conn.execute("UPDATE ai_conversations SET updated=? WHERE id=?", (now, cid))
        conn.commit()
        return {"reply": reply}
    finally:
        conn.close()


@app.post("/api/ai/analyze-mail/{uid}")
def ai_analyze_mail(uid: int):
    return ai.analyze_mail(uid)


@app.get("/api/ai/secretary")
def ai_secretary():
    return ai.secretary_plan()


@app.post("/api/ai/analyze-file")
def ai_analyze_file(payload: dict = Body(...)):
    path = payload.get("path") or ""
    if not path or not os.path.isfile(path):
        raise HTTPException(404, "Файл не найден")
    # не даём выйти за пределы разрешённых директорий
    allowed = [db.BASE_DIR]
    try:
        allowed.append(config.projects_base())
    except Exception:
        pass
    real = os.path.realpath(path)
    if not any(real.lower().startswith(os.path.realpath(a).lower()) for a in allowed if a):
        raise HTTPException(403, "Файл вне рабочих папок приложения")
    return ai.analyze_file(real, payload.get("filename"))


@app.get("/api/attachments")
def attachments_list(task_id: int = None, project_id: int = None):
    conn = db.get_conn()
    try:
        sql = "SELECT * FROM attachments WHERE 1=1"
        params = []
        if task_id:
            sql += " AND task_id = ?"
            params.append(task_id)
        if project_id:
            sql += " AND project_id = ?"
            params.append(project_id)
        sql += " ORDER BY id DESC LIMIT 200"
        return _rows(conn.execute(sql, params))
    finally:
        conn.close()


# ------------------------------------------------------------------ frontend
@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
