"""Работа с рабочей почтой по IMAP (только чтение).

Заголовки всех писем синхронизируются в локальный кэш (таблица mail_cache),
чтобы поиск, сортировка и пагинация работали мгновенно и по всем письмам,
включая старые за 3 месяца и ранее.
"""
import datetime
import email
import imaplib
import json
import os
import re
import ssl
import threading
from email import policy
from email.header import decode_header
from email.parser import BytesParser
from email.utils import parsedate_to_datetime

from . import db

CONFIG_PATH = os.path.join(db.BASE_DIR, "config.json")

# состояния фоновой синхронизации
_sync_lock = threading.Lock()
_sync_state = {"running": False, "last_sync": None, "total": 0, "error": None, "done": 0}

# uid папки Sent хранится со сдвигом, чтобы не конфликтовать с INBOX в mail_cache
SENT_OFFSET = 10_000_000


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def first_mailbox():
    boxes = load_config().get("mailboxes", [])
    return boxes[0] if boxes else None


def _connect(mailbox):
    ctx = ssl.create_default_context()
    conn = imaplib.IMAP4_SSL(mailbox["imap_server"], mailbox["imap_port"], ssl_context=ctx)
    conn.login(mailbox["email"], mailbox["password"])
    return conn


def sync_folders(conn):
    """INBOX + папки Отправленные (Sent/Отправленные и т.п.) с офсетом uid."""
    folders = [("INBOX", 0)]
    try:
        status, data = conn.list()
        if status != "OK":
            return folders
    except Exception:
        return folders
    for line in data or []:
        if not isinstance(line, bytes):
            continue
        m = re.search(rb'"\s+(.+?)\s*$', line)
        if not m:
            continue
        name = m.group(1).decode("utf-8", "replace").strip().strip('"')
        low = name.lower()
        if name == "INBOX" or "draft" in low or "черновик" in low or "junk" in low or "спам" in low:
            continue
        if "sent" in low or "отправлен" in low:
            folders.append((name, SENT_OFFSET))
    return folders


def _uid_loc(uid, folder=None):
    """(imap-папка, реальный uid) для чтения письма."""
    if uid < SENT_OFFSET:
        return "INBOX", uid
    if folder is None:
        conn = db.get_conn()
        try:
            row = conn.execute("SELECT folder FROM mail_cache WHERE uid=?", (uid,)).fetchone()
            folder = row["folder"] if row and row["folder"] else None
        finally:
            conn.close()
    return folder or "Sent", uid - SENT_OFFSET


def _decode(value):
    if value is None:
        return ""
    out = []
    for text, charset in decode_header(value):
        if isinstance(text, bytes):
            try:
                out.append(text.decode(charset or "utf-8", errors="replace"))
            except LookupError:
                out.append(text.decode("utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out).strip()


def _parse_date(raw):
    try:
        return parsedate_to_datetime(raw).astimezone().isoformat()
    except Exception:
        return None


def _parse_internaldate(meta):
    """INTERNALDATE из меты fetch-ответа — реальное время доставки на сервер."""
    if not isinstance(meta, bytes):
        return None
    m = re.search(rb'INTERNALDATE "([^"]+)"', meta)
    if not m:
        return None
    try:
        return parsedate_to_datetime(m.group(1).decode("ascii")).astimezone().isoformat()
    except Exception:
        try:
            s = m.group(1).decode("ascii")
            dt = datetime.datetime.strptime(s[:20], "%d-%b-%Y %H:%M:%S")
            off = s[21:]
            sign = 1 if off[0] == "+" else -1
            dt -= sign * datetime.timedelta(hours=int(off[1:3]), minutes=int(off[3:5]))
            return dt.astimezone().isoformat()
        except Exception:
            return None


def _extract_text(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                continue
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_content()
                except Exception:
                    continue
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    return part.get_content()
                except Exception:
                    continue
    try:
        return msg.get_content()
    except Exception:
        return ""


# ------------------------------------------------------------------ синхронизация
def sync_status():
    return dict(_sync_state)


def sync_full(background=False, force_headers=False):
    """Синхронизирует заголовки и флаги всех писем INBOX в локальный кэш.

    force_headers=True — перечитать заголовки всех писем заново (исправление
    старых записей, например писем без Date-заголовка).
    """
    if background:
        threading.Thread(target=_sync_worker, args=(force_headers,), daemon=True).start()
        return dict(_sync_state)
    _sync_worker(force_headers)
    return dict(_sync_state)


def _sync_worker(force_headers=False):
    if not _sync_lock.acquire(blocking=False):
        return
    try:
        _sync_state.update(running=True, error=None, done=0)
        mailbox = first_mailbox()
        if not mailbox:
            _sync_state.update(running=False, error="Почта не настроена")
            return
        conn = _connect(mailbox)
        try:
            folders = sync_folders(conn)
            conn_db = db.get_conn()
            try:
                _sync_state["total"] = 0
                for imap_folder, offset in folders:
                    try:
                        conn.select(imap_folder, readonly=True)
                        status, data = conn.uid("search", None, "ALL")
                        if status != "OK":
                            continue
                        uids = [int(u) + offset for u in data[0].split()]
                        _sync_state["total"] += len(uids)

                        if force_headers:
                            new_uids = list(uids)
                        else:
                            known = {r["uid"] for r in conn_db.execute(
                                "SELECT uid FROM mail_cache WHERE folder = ?", (imap_folder,)
                            )}
                            new_uids = [u for u in uids if u not in known]
                        _sync_state["done"] = _sync_state.get("done", 0) + len(uids) - len(new_uids)

                        _sync_headers(conn, conn_db, new_uids, batch=1000,
                                      folder=imap_folder, offset=offset)
                        _sync_flags(conn, conn_db, uids, offset=offset)
                    except Exception:
                        continue
                _sync_state.update(
                    last_sync=db.now_iso(), running=False,
                    done=_sync_state["total"], total=_sync_state["total"],
                )
            finally:
                conn_db.close()
        finally:
            try:
                conn.logout()
            except Exception:
                pass
    except Exception as e:
        _sync_state.update(running=False, error=str(e))
    finally:
        _sync_lock.release()


def _sync_headers(conn, conn_db, uids, batch=200, folder="INBOX", offset=0):
    """Скачивает заголовки новых писем пачками. uids — закодированные (с офсетом)."""
    for i in range(0, len(uids), batch):
        chunk = uids[i:i + batch]
        real = [u - offset for u in chunk]
        if len(real) == 1:
            spec = str(real[0])
        else:
            # чанки монотонны по UID, диапазон безопасен
            spec = f"{real[0]}:{real[-1]}"
        try:
            status, data = conn.uid("fetch", spec, "(UID INTERNALDATE RFC822.HEADER)")
        except Exception:
            status, data = "NO", []
        if status != "OK" or not data:
            continue
        items = []
        current_uid = None
        current_internal = None
        for part in data:
            if isinstance(part, tuple):
                meta = part[0]
                if isinstance(meta, bytes) and b"UID " in meta:
                    try:
                        current_uid = int(meta.split(b"UID ")[1].split()[0].rstrip(b")")) + offset
                    except Exception:
                        current_uid = None
                    current_internal = _parse_internaldate(meta)
                try:
                    msg = BytesParser(policy=policy.default).parsebytes(part[1])
                except Exception:
                    current_uid = None
                    continue
                if current_uid is None:
                    continue
                item = _header_item(msg, current_internal)
                item["uid"] = current_uid
                item["folder"] = folder
                item["body"] = ""
                item["seen"] = 0
                item["flagged"] = 0
                items.append(item)
        if items:
            _cache_messages(conn_db, items)
        _sync_state["done"] += len(chunk)


def _sync_flags(conn, conn_db, uids, offset=0):
    r"""Обновляет флаги \Seen и \Flagged для всех писем одним запросом."""
    try:
        status, data = conn.uid("fetch", "1:*", "(UID FLAGS)")
    except Exception:
        return
    if status != "OK":
        return
    updates = []
    for part in data:
        # imaplib отдаёт ответы без литералов как bytes, с литералом — как tuple
        if isinstance(part, tuple):
            meta = part[0] if part and isinstance(part[0], bytes) else None
        elif isinstance(part, bytes):
            meta = part
        else:
            meta = None
        if not meta or b"UID " not in meta or b"FLAGS" not in meta:
            continue
        try:
            uid = int(meta.split(b"UID ")[1].split()[0].split(b")")[0]) + offset
        except Exception:
            continue
        upper = meta.upper()
        seen = 1 if b"\\SEEN" in upper else 0
        flagged = 1 if b"\\FLAGGED" in upper else 0
        updates.append((seen, flagged, uid))
    if updates:
        conn_db.executemany(
            "UPDATE mail_cache SET seen=?, flagged=? WHERE uid=?", updates
        )
        conn_db.commit()


def _cache_messages(conn_db, items):
    for it in items:
        conn_db.execute(
            """INSERT INTO mail_cache(uid, subject, sender, date, body, folder, flagged, seen)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(uid) DO UPDATE SET
                 subject=excluded.subject, sender=excluded.sender,
                 date=excluded.date, folder=excluded.folder,
                 flagged=excluded.flagged, seen=excluded.seen""",
            (
                it["uid"],
                it.get("subject", ""),
                it.get("sender", ""),
                it.get("date", ""),
                it.get("body", ""),
                it.get("folder", "INBOX"),
                it.get("flagged", 0),
                it.get("seen", 1),
            ),
        )
    conn_db.commit()


# ------------------------------------------------------------------ локальный листинг
def _item_from_row(r):
    return {
        "uid": r["uid"],
        "subject": r["subject"] or "(без темы)",
        "sender": r["sender"] or "",
        "date": r["date"] or "",
        "seen": r["seen"] or 0,
        "flagged": r["flagged"] or 0,
        "folder": r["folder"] or "INBOX",
    }


def list_messages(tab="recent", query="", days=0, sort="newest", page=1, page_size=50):
    """Список писем из локального кэша с поиском/сортировкой/пагинацией."""
    where = ["1=1"]
    params = []
    if tab == "unread":
        where.append("seen = 0")
    elif tab == "starred":
        where.append("flagged = 1")
    elif tab == "sent":
        where.append("folder != 'INBOX'")
    if days:
        since = (datetime.datetime.now() - datetime.timedelta(days=days)).date().isoformat()
        where.append("date >= ?")
        params.append(since)
    if query:
        like = f"%{query}%"
        where.append("(subject LIKE ? OR sender LIKE ?)")
        params += [like, like]

    conn = db.get_conn()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM mail_cache WHERE {' AND '.join(where)}", params
        ).fetchone()["n"]
    finally:
        conn.close()

    if total == 0 and not sync_status().get("running"):
        # кэш пуст (первый запуск) — быстрая подгрузка свежих писем онлайн
        try:
            sync_recent(limit=150)
        except Exception:
            pass
        conn = db.get_conn()
        try:
            total = conn.execute(
                f"SELECT COUNT(*) AS n FROM mail_cache WHERE {' AND '.join(where)}", params
            ).fetchone()["n"]
        finally:
            conn.close()

    conn = db.get_conn()
    try:
        order = "date DESC" if sort != "oldest" else "date ASC"
        page = max(1, page)
        offset = (page - 1) * page_size
        rows = conn.execute(
            f"SELECT uid, subject, sender, date, seen, flagged, folder FROM mail_cache "
            f"WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()
        pages = max(1, (total + page_size - 1) // page_size)
        return {
            "items": [_item_from_row(r) for r in rows],
            "total": total,
            "page": page,
            "pages": pages,
            "sync": sync_status(),
        }
    finally:
        conn.close()


def counts():
    """Счётчики писем по вкладкам."""
    conn = db.get_conn()
    try:
        row = conn.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN seen=0 THEN 1 ELSE 0 END) AS unread,
                      SUM(CASE WHEN flagged=1 THEN 1 ELSE 0 END) AS starred,
                      SUM(CASE WHEN folder != 'INBOX' THEN 1 ELSE 0 END) AS sent
               FROM mail_cache"""
        ).fetchone()
        return {
            "total": row["total"] or 0,
            "unread": row["unread"] or 0,
            "starred": row["starred"] or 0,
            "sent": row["sent"] or 0,
            "sync": sync_status(),
        }
    finally:
        conn.close()


def cached_uids(page_uids):
    conn = db.get_conn()
    try:
        placeholders = ",".join("?" * len(page_uids))
        rows = conn.execute(
            f"SELECT uid, subject, sender, date, body, seen, flagged FROM mail_cache "
            f"WHERE uid IN ({placeholders})",
            list(page_uids),
        ).fetchall()
        return {r["uid"]: dict(r) for r in rows}
    finally:
        conn.close()


# ------------------------------------------------------------------ онлайн-операции
def get_message(uid, cache=True):
    """Тело письма по uid (с кэшированием в БД). Работает и для папки Sent."""
    imap_folder, real_uid = _uid_loc(uid)
    if cache:
        conn = db.get_conn()
        try:
            row = conn.execute("SELECT * FROM mail_cache WHERE uid=?", (uid,)).fetchone()
            if row and row["body"]:
                return dict(row)
        finally:
            conn.close()

    mailbox = first_mailbox()
    conn = _connect(mailbox)
    try:
        conn.select(imap_folder, readonly=True)
        stat, msg_data = conn.uid("fetch", str(real_uid), "(UID INTERNALDATE RFC822)")
        if stat != "OK" or not msg_data or not msg_data[0]:
            return None
        msg = BytesParser(policy=policy.default).parsebytes(msg_data[0][1])
        item = _header_item(msg, _parse_internaldate(msg_data[0][0]))
        item["uid"] = uid
        item["body"] = _extract_text(msg)[:20000]
        item["folder"] = imap_folder
        item["attachments"] = list_attachments(msg)
    finally:
        try:
            conn.logout()
        except Exception:
            pass

    conn_db = db.get_conn()
    try:
        _cache_messages(conn_db, [item])
    finally:
        conn_db.close()
    return item


def list_attachments(msg):
    """Список вложений письма (без скачивания)."""
    out = []
    if not msg.is_multipart():
        return out
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disp = part.get_content_disposition()
        if disp != "attachment" and not part.get_filename():
            continue
        payload = part.get_payload(decode=True) or b""
        out.append({
            "filename": _decode(part.get_filename()) or "вложение",
            "mime": part.get_content_type(),
            "size": len(payload),
        })
    return out


def download_attachment(uid, filename):
    """Скачивает одно вложение письма, возвращает bytes."""
    imap_folder, real_uid = _uid_loc(uid)
    mailbox = first_mailbox()
    conn = _connect(mailbox)
    try:
        conn.select(imap_folder, readonly=True)
        stat, msg_data = conn.uid("fetch", str(real_uid), "(RFC822)")
        if stat != "OK" or not msg_data or not msg_data[0]:
            return None, None
        msg = BytesParser(policy=policy.default).parsebytes(msg_data[0][1])
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            name = _decode(part.get_filename())
            if name == filename or (part.get_content_disposition() == "attachment" and not name):
                payload = part.get_payload(decode=True)
                return payload, part.get_content_type()
        # если имя не совпало — берём первое вложение с таким именем расширения
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            name = _decode(part.get_filename()) or ""
            if name and name.split("/")[-1].lower() == filename.lower():
                return part.get_payload(decode=True), part.get_content_type()
        return None, None
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _header_item(msg, internaldate=None):
    return {
        "subject": _decode(msg.get("Subject")),
        "sender": _decode(msg.get("From")),
        "date": _parse_date(msg.get("Date"))
        or internaldate
        or datetime.datetime.now().astimezone().isoformat(),
    }


def search_headers(criterion, limit=50):
    """Онлайн-поиск по критерию IMAP (резервный путь, без локального кэша)."""
    mailbox = first_mailbox()
    if not mailbox or not mailbox.get("email"):
        return [], "Почта не настроена"

    conn = _connect(mailbox)
    try:
        conn.select("INBOX", readonly=True)
        status, data = conn.uid("search", None, criterion)
        if status != "OK":
            return [], "Ошибка поиска"
        uids = [int(u.decode()) for u in data[0].split()]
        if limit and len(uids) > limit:
            uids = uids[-limit:]
        items = []
        for uid in uids:
            stat, msg_data = conn.uid("fetch", str(uid), "(UID INTERNALDATE RFC822.HEADER)")
            if stat != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = BytesParser(policy=policy.default).parsebytes(msg_data[0][1])
            item = _header_item(msg, _parse_internaldate(msg_data[0][0]))
            item["uid"] = uid
            items.append(item)
        return items, ""
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def sync_recent(limit=50):
    """Совместимый интерфейс: загружает свежие письма и кэширует."""
    items, err = search_headers("ALL", limit=limit)
    if err:
        return [], err
    conn = db.get_conn()
    try:
        _cache_messages(conn, [{**it, "body": ""} for it in items])
    finally:
        conn.close()
    return items, ""


# ------------------------------------------------------------------ ветки писем
_RE_PREFIX = re.compile(r"^\s*((re|fw|fwd|отв|пересл|vs)\s*(\[\d+\])?\s*:\s*)+", re.I)
_RE_TAG = re.compile(r"^\s*(\[[^\]]{0,30}\]|\(не спам\)|\[спам\])\s*", re.I)


def subject_base(subject):
    """Базовая тема без Re:/Fwd:/служебных приписок — ключ ветки переписки."""
    s = (subject or "").strip()
    for _ in range(4):
        new = _RE_TAG.sub("", _RE_PREFIX.sub("", s)).strip()
        if new == s:
            break
        s = new
    return re.sub(r"\s+", " ", s).strip()


def thread_messages(uid, limit=60):
    """Все письма одной темы (ветки) в хронологическом порядке."""
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT * FROM mail_cache WHERE uid=?", (uid,)).fetchone()
        if not row:
            return None
        base = subject_base(row["subject"])
        key = base[:40]
        if key:
            rows = conn.execute(
                "SELECT uid, subject, sender, date, body, seen FROM mail_cache "
                "WHERE subject LIKE ? ORDER BY date, uid",
                (f"%{key}%",),
            ).fetchall()
        else:
            rows = [row]
        items = []
        for r in rows:
            if subject_base(r["subject"]) != base:
                continue
            items.append({
                "uid": r["uid"],
                "subject": r["subject"] or "",
                "sender": r["sender"] or "",
                "date": r["date"] or "",
                "body": (r["body"] or "")[:4000],
                "seen": r["seen"] or 0,
            })
        return {"uid": uid, "subject": base, "count": len(items), "messages": items[-limit:]}
    finally:
        conn.close()


def thread_by_subject(subject, limit=60):
    """Ищет ветку по теме (без письма-якоря)."""
    base = subject_base(subject)
    key = base[:40]
    if not key:
        return None
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT uid FROM mail_cache WHERE subject LIKE ? ORDER BY date DESC LIMIT 5",
            (f"%{key}%",),
        ).fetchall()
    finally:
        conn.close()
    for r in rows:
        thread = thread_messages(r["uid"], limit=limit)
        if thread and thread.get("count"):
            return thread
    return None
