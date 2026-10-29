"""ИИ-помощник на DeepSeek: разбор писем, чат-консультант, секретарь, вложения.

Совместим с OpenAI-совместным API (deepseek). Ключ берётся из config.json.
"""
import datetime
import json
import os
import urllib.error
import urllib.request

from . import config, db

TIMEOUT = 120


def enabled():
    cfg = config.ai_config()
    return bool(cfg.get("enabled")) and bool(cfg.get("api_key"))


def _call(messages, temperature=0.4, max_tokens=1800):
    cfg = config.ai_config()
    key = cfg.get("api_key")
    if not cfg.get("enabled"):
        return None, "ИИ отключён в настройках"
    if not key:
        return None, "Не задан ключ DeepSeek (config.json → ai.api_key)"

    base = (cfg.get("base_url") or "https://api.deepseek.com").rstrip("/")
    url = f"{base}/chat/completions"
    body = json.dumps(
        {
            "model": cfg.get("model") or "deepseek-chat",
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            detail = ""
        return None, f"Ошибка DeepSeek ({e.code}): {detail}"
    except Exception as e:
        return None, f"Ошибка обращения к ИИ: {e}"

    try:
        return data["choices"][0]["message"]["content"], None
    except (KeyError, IndexError):
        return None, "Пустой ответ от ИИ"


# ------------------------------------------------------------------ контекст для чата
def _tasks_context(limit=30):
    conn = db.get_conn()
    try:
        today = datetime.date.today().isoformat()
        rows = conn.execute(
            """SELECT t.id, t.title, t.status, t.priority, t.deadline, t.progress,
                      p.name AS project
               FROM tasks t LEFT JOIN projects p ON p.id = t.project_id
               WHERE t.status IN ('todo','doing')
               ORDER BY (t.deadline IS NULL), t.deadline, t.priority = 'high' DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        projects = conn.execute(
            """SELECT p.name, COUNT(t.id) AS total,
                      SUM(CASE WHEN t.status='done' THEN 1 ELSE 0 END) AS done
               FROM projects p LEFT JOIN tasks t ON t.project_id = p.id
               GROUP BY p.id ORDER BY p.name"""
        ).fetchall()
        ctx = {
            "сегодня": today,
            "задачи": [dict(r) for r in rows],
            "проекты": [dict(p) for p in projects],
        }
        return ctx
    finally:
        conn.close()


def _mail_context(limit=5):
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT subject, sender, date, seen FROM mail_cache "
            "ORDER BY date DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {"тема": r["subject"], "от": r["sender"], "дата": r["date"],
             "прочитано": bool(r["seen"])}
            for r in rows
        ]
    finally:
        conn.close()


# ------------------------------------------------------------------ функции ИИ
def analyze_mail(uid):
    """Разбор письма: суть, задачи, дедлайны, действия."""
    from . import mail as mail_mod

    item = mail_mod.get_message(uid)
    if not item:
        return {"ok": False, "error": "Письмо не найдено"}
    system = (
        "Ты — помощник-секретарь. Отвечай на русском, кратко и по делу, без вступлений. "
        "Формат:\nСУТЬ: ...\nЗАДАЧИ:\n- ...\nДЕДЛАЙНЫ:\n- ... (или «не указаны»)\n"
        "ДЕЙСТВИЯ:\n- ...\nРИСКИ: ... (или «нет»)"
    )
    user = (
        f"Тема: {item.get('subject','')}\n"
        f"От: {item.get('sender','')}\n"
        f"Дата: {item.get('date','')}\n\n"
        f"Текст:\n{(item.get('body') or '')[:6000]}"
    )
    text, err = _call(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2,
    )
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "analysis": text, "subject": item.get("subject", "")}


def assistant_reply(conv_id, user_text):
    """Чат-помощник: отвечает с учётом задач/проектов/почты и истории диалога."""
    history = []
    if conv_id:
        conn = db.get_conn()
        try:
            rows = conn.execute(
                "SELECT role, content FROM ai_messages WHERE conv_id=? "
                "ORDER BY id DESC LIMIT 20",
                (conv_id,),
            ).fetchall()
            history = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
        finally:
            conn.close()

    system = (
        "Ты — ИИ-помощник-консультант в локальном планировщике работы («Планировщик работы»). "
        "Отвечай на русском, по делу, короткими пунктами. Ты видишь текущие задачи, проекты "
        "и свежие письма пользователя — используй их, когда вопрос о работе, сроках, почте. "
        "Если просят что-то сделать (перенести срок, отметить готово) — скажи, что для этого "
        "нужно сделать в интерфейсе, сам ничего не меняй.\n\n"
        f"СЕЙЧАС: {datetime.datetime.now():%Y-%m-%d %H:%M}\n"
        f"КОНТЕКСТ (задачи и проекты):\n{json.dumps(_tasks_context(), ensure_ascii=False, default=str)[:6000]}\n\n"
        f"СВЕЖИЕ ПИСЬМА:\n{json.dumps(_mail_context(), ensure_ascii=False, default=str)[:2000]}"
    )
    messages = [{"role": "system", "content": system}] + history + [
        {"role": "user", "content": user_text}
    ]
    text, err = _call(messages, temperature=0.5, max_tokens=1200)
    if err:
        return err
    return text


def secretary_plan():
    """План дня: что просрочено, что на сегодня, что ближайшее."""
    system = (
        "Ты — секретарь-планировщик. Составь план на сегодня на русском: "
        "1) СРОЧНО (просроченные и на сегодня), 2) ВАЖНО (ближайшие 3 дня), "
        "3) ЧТО ОТЛОЖИТЬ. Короткие пункты с номерами задач, сроками и проектом. "
        "Без вступлений и без обсуждений формата."
    )
    user = (
        f"Дата: {datetime.date.today():%Y-%m-%d}\n"
        f"Контекст:\n{json.dumps(_tasks_context(50), ensure_ascii=False, default=str)[:8000]}"
    )
    text, err = _call(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.3,
    )
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "plan": text}


def summarize_text(filename, text, purpose="вложения"):
    """Выжимка произвольного текста (вложение, файл, выдержка)."""
    system = (
        "Ты — помощник-аналитик. По переданному тексту сделай выжимку: "
        "что это, главная суть, важные цифры/даты/сроки, возможные задачи. "
        "Кратко, на русском, списками."
    )
    user = f"Файл: {filename} ({purpose})\nТекст:\n{text[:8000]}"
    text_out, err = _call(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2,
    )
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "summary": text_out}


# ------------------------------------------------------------------ ход действий по переписке
def _thread_text(messages):
    parts = []
    for m in messages:
        parts.append(
            f"UID {m.get('uid')} | {m.get('date','')} | {m.get('sender','')}\n"
            f"{(m.get('body') or '')[:900]}"
        )
    return "\n\n".join(parts)


def summarize_thread(messages, target_title=""):
    """Разбор всей ветки письма: итог + хронология событий по каждому письму."""
    if not messages:
        return {"ok": False, "error": "Писем в ветке не найдено"}
    subject = messages[-1].get("subject") or ""
    system = (
        "Ты — помощник руководителя, ведёшь журнал хода работы по задаче. "
        "По ветке писем одной темы опиши хронологию событий. Отвечай на русском, "
        "кратко, без вступлений и пояснений.\n"
        "Формат строго такой:\n"
        "ИТОГ: одно предложение — чем закончилась переписка и что дальше\n"
        "UID <номер письма> | <короткое название события, 2-4 слова> | "
        "<что произошло: кто, что, сроки — 1-2 предложения>\n"
        "Повтори строку UID для КАЖДОГО письма, в хронологическом порядке."
    )
    user = (
        f"Задача/тема: {target_title or subject}\n"
        f"Писем в ветке: {len(messages)}\n\n"
        f"{_thread_text(messages)[:12000]}"
    )
    text, err = _call(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2,
        max_tokens=2200,
    )
    if err:
        return {"ok": False, "error": err}

    import re as _re

    note = ""
    entries = []
    for line in (text or "").splitlines():
        s = line.strip().lstrip("-•* ").strip()
        if s.upper().startswith("ИТОГ"):
            note = s.split(":", 1)[-1].strip()
            continue
        m = _re.match(r"^UID\s+(\d+)\s*\|\s*([^|]+?)\s*\|\s*(.+)$", s, _re.I)
        if m:
            entries.append({
                "uid": int(m.group(1)),
                "title": m.group(2).strip(),
                "body": m.group(3).strip(),
            })
    if not entries and not note:
        return {"ok": True, "note": (text or "").strip()[:600], "subject": subject, "entries": []}

    by_uid = {m["uid"]: m for m in messages}
    for e in entries:
        src = by_uid.get(e["uid"]) or {}
        e["date"] = src.get("date", "")
        e["sender"] = src.get("sender", "")
    return {"ok": True, "note": note, "subject": subject, "entries": entries}


# ------------------------------------------------------------------ вложения
def extract_text(path):
    """Извлекает текст из файла (txt/md/csv/json/code + docx/xlsx/pdf по возможности)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".txt", ".md", ".csv", ".json", ".log", ".py", ".js", ".html", ".css", ".xml", ".yaml", ".yml", ".ini", ".cfg"):
        for enc in ("utf-8", "cp1251", "latin-1"):
            try:
                with open(path, "r", encoding=enc) as f:
                    return f.read()[:60000], None
            except UnicodeDecodeError:
                continue
            except OSError as e:
                return None, str(e)
        return None, "Не удалось прочитать кодировку"

    if ext in (".docx", ".xlsx", ".pptx"):
        return _extract_office(path)

    if ext == ".pdf":
        return _extract_pdf(path)

    return None, f"Формат {ext} не поддерживается для извлечения текста"


def _extract_office(path):
    """Текст из OOXML-файлов (docx/xlsx/pptx) через zipfile."""
    import zipfile
    import xml.etree.ElementTree as ET

    try:
        with zipfile.ZipFile(path) as z:
            parts = []
            if path.lower().endswith(".docx"):
                names = [n for n in z.namelist() if n.startswith("word/") and n.endswith(".xml")]
            elif path.lower().endswith(".xlsx"):
                names = [n for n in z.namelist() if n in ("xl/sharedStrings.xml",) or n.startswith("xl/worksheets/")]
            else:
                names = [n for n in z.namelist() if n.startswith("ppt/slides/") and n.endswith(".xml")]
            for name in names:
                try:
                    root = ET.fromstring(z.read(name))
                except Exception:
                    continue
                for el in root.iter():
                    tag = el.tag.split("}")[-1]
                    if tag in ("t", "v") and el.text:
                        parts.append(el.text)
            text = "\n".join(parts)
            return (text[:60000], None) if text.strip() else (None, "Пустой файл")
    except zipfile.BadZipFile:
        return None, "Файл не читается (не zip/office)"
    except Exception as e:
        return None, str(e)


def _extract_pdf(pdf_path):
    """Попытка извлечь текста из PDF: pypdf → pdfminer → сырой поиск."""
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(pdf_path)
        text = "\n".join((p.extract_text() or "") for p in reader.pages[:40])
        return (text[:60000], None) if text.strip() else (None, "PDF без извлечаемого текста (скан?)")
    except ImportError:
        pass
    except Exception as e:
        return None, str(e)

    # запасной вариант: вытащить читаемые строки из стримов
    try:
        with open(pdf_path, "rb") as f:
            raw = f.read()
        chunks = []
        for marker in (b"BT", b"Tj"):
            start = 0
            while True:
                i = raw.find(b"(", start)
                if i == -1:
                    break
                j = raw.find(b")", i)
                if j == -1:
                    break
                piece = raw[i + 1:j]
                if len(piece) > 2:
                    try:
                        chunks.append(piece.decode("cp1251", "ignore"))
                    except Exception:
                        pass
                start = j + 1
                if sum(len(c) for c in chunks) > 60000:
                    break
        text = " ".join(chunks)
        return (text[:60000], None) if len(text.strip()) > 50 else (None, "Текст из PDF извлечь не удалось")
    except Exception as e:
        return None, str(e)


def analyze_file(path, filename=None):
    """Извлекает текст файла и делает его выжимку через ИИ."""
    filename = filename or os.path.basename(path)
    text, err = extract_text(path)
    if err:
        return {"ok": False, "error": err}
    if not text:
        return {"ok": False, "error": "Пустой файл"}
    return summarize_text(filename, text)
