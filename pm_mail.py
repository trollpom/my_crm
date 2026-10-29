import datetime
import imaplib
import json
import os
import re
import ssl
from email.header import decode_header
from email.utils import parsedate_to_datetime
from email import policy
from email.parser import BytesParser

import pm_core

MAIL_CACHE_DIR = os.path.join(pm_core.SCRIPT_DIR, "mail_cache")
LAST_EMAIL_FILE = os.path.join(pm_core.SCRIPT_DIR, "last_email.json")

DEADLINE_KEYWORDS = [
    "дедлайн", "deadline", "срок", "к сроку", "до конца", "до понедельника",
    "до вторника", "до среды", "до четверга", "до пятницы", "до субботы", "до воскресенья",
    "нужно до", "необходимо до", "сдать", "сдать до", "предоставить до", "отчет", "отчёт",
    "встреча", "созвон", "совещание", "собрание", "напоминаю", "напоминание",
]


def ensure_cache():
    os.makedirs(MAIL_CACHE_DIR, exist_ok=True)


def decode_header_value(value):
    if value is None:
        return ""
    parts = decode_header(value)
    result = []
    for text, charset in parts:
        if isinstance(text, bytes):
            try:
                result.append(text.decode(charset or "utf-8", errors="replace"))
            except LookupError:
                result.append(text.decode("utf-8", errors="replace"))
        else:
            result.append(text)
    return "".join(result).strip()


def load_last_uids():
    try:
        with open(LAST_EMAIL_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_last_uids(data):
    with open(LAST_EMAIL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def mail_cache_path(mailbox_name, uid):
    safe = "".join(c if c.isalnum() or c in ".-" else "_" for c in mailbox_name)
    return os.path.join(MAIL_CACHE_DIR, f"{safe}__{uid}.json")


def fetch_new_messages(mailbox, limit=None):
    name = mailbox["name"]
    ensure_cache()
    last_uids = load_last_uids()
    last_uid = last_uids.get(name, 0)

    conn = imaplib.IMAP4_SSL(
        mailbox["imap_server"],
        mailbox["imap_port"],
        ssl_context=ssl.create_default_context(),
    )
    conn.login(mailbox["email"], mailbox["password"])
    conn.select("INBOX", readonly=True)

    status, data = conn.uid("search", None, "ALL")
    if status != "OK":
        conn.logout()
        return [], "Ошибка поиска писем"

    uids = sorted(int(u.decode()) for u in data[0].split())
    new_uids = [u for u in uids if u > last_uid]
    if limit and len(new_uids) > limit:
        new_uids = new_uids[-limit:]
    messages = []
    for uid in new_uids:
        stat, msg_data = conn.uid("fetch", str(uid), "(RFC822)")
        if stat != "OK" or not msg_data or not msg_data[0]:
            continue
        raw = msg_data[0][1]
        msg = BytesParser(policy=policy.default).parsebytes(raw)
        subject = decode_header_value(msg.get("Subject"))
        sender = decode_header_value(msg.get("From"))
        date_raw = msg.get("Date")
        try:
            date = parsedate_to_datetime(date_raw).astimezone()
        except Exception:
            date = datetime.datetime.now().astimezone()
        body = extract_text(msg)
        item = {
            "uid": uid,
            "subject": subject,
            "sender": sender,
            "date": date.isoformat(),
            "body": body[:6000],
        }
        with open(mail_cache_path(name, uid), "w", encoding="utf-8") as f:
            json.dump(item, f, ensure_ascii=False, indent=2)
        messages.append(item)

    if uids:
        last_uids[name] = max(uids)
        save_last_uids(last_uids)
    conn.logout()
    return messages, ""


def extract_text(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain":
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


def search_message_headers(mailbox, criterion, limit=None):
    ensure_cache()
    conn = imaplib.IMAP4_SSL(
        mailbox["imap_server"],
        mailbox["imap_port"],
        ssl_context=ssl.create_default_context(),
    )
    conn.login(mailbox["email"], mailbox["password"])
    conn.select("INBOX", readonly=True)
    status, data = conn.uid("search", None, criterion)
    if status != "OK":
        conn.logout()
        return [], "Ошибка поиска"
    uids = [int(u.decode()) for u in data[0].split()]
    if limit and len(uids) > limit:
        uids = uids[-limit:]
    messages = []
    for uid in uids:
        stat, msg_data = conn.uid("fetch", str(uid), "(RFC822.HEADER)")
        if stat != "OK" or not msg_data or not msg_data[0]:
            continue
        raw = msg_data[0][1]
        msg = BytesParser(policy=policy.default).parsebytes(raw)
        date_raw = msg.get("Date")
        try:
            date = parsedate_to_datetime(date_raw).astimezone()
        except Exception:
            date = datetime.datetime.now().astimezone()
        messages.append({
            "uid": uid,
            "subject": decode_header_value(msg.get("Subject")),
            "sender": decode_header_value(msg.get("From")),
            "date": date.isoformat(),
        })
    conn.logout()
    return messages, ""


def fetch_unread_messages(mailbox, limit=None):
    return search_message_headers(mailbox, "UNSEEN", limit)


def fetch_flagged_messages(mailbox, limit=None):
    return search_message_headers(mailbox, "FLAGGED", limit)


def load_cached_messages(lookback_days=None):
    ensure_cache()
    messages = []
    for fname in sorted(os.listdir(MAIL_CACHE_DIR)):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(MAIL_CACHE_DIR, fname), "r", encoding="utf-8") as f:
            try:
                msg = json.load(f)
            except json.JSONDecodeError:
                continue
        if lookback_days:
            try:
                d = datetime.datetime.fromisoformat(msg["date"])
                if d.date() < datetime.date.today() - datetime.timedelta(days=lookback_days):
                    continue
            except Exception:
                pass
        messages.append(msg)
    return messages


def scan_deadlines_in_text(text):
    """Ищет в тексте упоминания дат/сроков возле ключевых слов."""
    results = []
    lowered = text.lower()
    for keyword in DEADLINE_KEYWORDS:
        for m in re.finditer(re.escape(keyword), lowered):
            start = max(0, m.start() - 200)
            end = min(len(lowered), m.end() + 250)
            snippet = text[start:end].replace("\n", " ").strip()
            # ищем дату в сниппете и вокруг
            found_date = None
            for dm in re.finditer(r"(\d{1,2}[./-]\d{1,2}[./-]?\d{0,4})|(\d{1,2}\s+[а-яё]{3,12})|(завтра|послезавтра|сегодня|через\s+\d+\s+(?:дн\w*|час\w*|нед\w*))", snippet):
                candidate = dm.group(0)
                found_date = pm_core.parse_date(candidate)
                if found_date:
                    break
            if found_date is None:
                # если нет конкретной даты — пробуем дату из 'до <дата>' вокруг ключа
                around = snippet[keyword.find(keyword):] if False else snippet
                m2 = re.search(r"\b(до\s+)?(\d{1,2}[./-]\d{1,2}[./-]?\d{0,4}|\d{1,2}\s+[а-яё]{3,12}|завтра|послезавтра|через\s+\d+\s+\w+)", around)
                if m2:
                    found_date = pm_core.parse_date(m2.group(0))
            results.append({
                "keyword": keyword,
                "date": found_date.isoformat() if found_date else None,
                "snippet": snippet[:300],
            })
    return results


def scan_messages(mailbox_name, lookback_days=None):
    messages = load_cached_messages(lookback_days)
    results = []
    for msg in messages:
        text = f"{msg['subject']}\n{msg['body']}"
        hits = scan_deadlines_in_text(text)
        if hits:
            results.append({
                "subject": msg["subject"],
                "sender": msg["sender"],
                "date": msg["date"],
                "hits": hits,
            })
    return results
