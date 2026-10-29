import json
import imaplib
import os
import sys
import time

import requests
from email.header import decode_header
from email.utils import parsedate_to_datetime
from email.message import Message
from email import policy
from email.parser import BytesParser

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
STATE_PATH = os.path.join(SCRIPT_DIR, "state.json")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def decode_header_value(value):
    if value is None:
        return "(без темы)"
    parts = decode_header(value)
    result = []
    for text, charset in parts:
        if isinstance(text, bytes):
            try:
                charset = charset or "utf-8"
                result.append(text.decode(charset, errors="replace"))
            except LookupError:
                result.append(text.decode("utf-8", errors="replace"))
        else:
            result.append(text)
    return "".join(result).strip() or "(без темы)"


def fetch_full_message(connection, uid):
    status, data = connection.uid("fetch", uid, "(RFC822)")
    if status != "OK" or not data or not data[0]:
        return None
    raw = data[0][1]
    return BytesParser(policy=policy.default).parsebytes(raw)


def get_new_inbox_messages(connection):
    connection.select("INBOX", readonly=True)
    message_ids = connection.search(None, "ALL")[1][0].split()
    if not message_ids:
        return []
    uids = [mid.decode() for mid in message_ids]
    return uids


def message_text(msg):
    if msg.is_multipart():
        text = None
        html = None
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain" and text is None:
                text = part.get_content()
            elif ctype == "text/html" and html is None:
                html = part.get_content()
        if text:
            return text
        return (html or "")[:1000]
    return msg.get_content() or ""


def matches_keywords(subject, body, keywords):
    for kw in keywords:
        kw_lower = kw.lower()
        if kw_lower in subject.lower() or kw_lower in body.lower():
            return True
    return False


def send_to_telegram(config, subject, sender, date, body_preview):
    token = config["telegram_bot_token"]
    chat_id = config["telegram_chat_id"]
    text = (
        f"📧 *Новое письмо*\n\n"
        f"*От:* {sender}\n"
        f"*Тема:* {subject}\n"
        f"*Дата:* {date}\n\n"
        f"```\n{body_preview}\n```"
    )
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    response = requests.post(
        TELEGRAM_API.format(token=token), json=payload, timeout=30
    )
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API: {result.get('description')}")


def process_mailbox(config, mailbox, state):
    name = mailbox["name"]
    print(f"[{name}] Подключение к {mailbox['imap_server']}...")
    connection = imaplib.IMAP4_SSL(mailbox["imap_server"], mailbox["imap_port"])
    connection.login(mailbox["email"], mailbox["password"])

    uids = get_new_inbox_messages(connection)
    processed_ids = set(state.get(name, {}).get("processed", []))

    sent = 0
    new_processed = list(processed_ids)
    for uid in uids:
        if uid in processed_ids:
            continue
        msg = fetch_full_message(connection, uid)
        if msg is None:
            continue
        msg_id = msg.get("Message-ID", uid)

        subject = decode_header_value(msg.get("Subject"))
        sender = decode_header_value(msg.get("From"))
        date_raw = msg.get("Date")
        try:
            date = parsedate_to_datetime(date_raw).strftime("%d.%m.%Y %H:%M")
        except Exception:
            date = date_raw or ""
        body = message_text(msg)

        should_send = True
        keywords = config.get("keywords", [])
        forward_all = config.get("forward_all", True)
        if not forward_all and keywords:
            should_send = matches_keywords(subject, body, keywords)
        elif not forward_all and not keywords:
            should_send = False

        if should_send:
            body_preview = body.replace("\\", " ").replace("`", "\u00B4")[:800]
            try:
                send_to_telegram(config, subject, sender, date, body_preview)
                print(f"[{name}] Отправлено письмо с UID {uid}: {subject}")
                sent += 1
            except Exception as exc:
                print(f"[{name}] Ошибка отправки письма UID {uid}: {exc}")
                connection.logout()
                return sent, new_processed, True

        if msg_id not in new_processed:
            new_processed.append(msg_id)
        # гарантируем, что даже без Message-ID письмо не будет повторяться
        if uid not in new_processed:
            new_processed.append(uid)

    connection.logout()
    print(f"[{name}] Готово, отправлено {sent} писем.")
    return sent, new_processed, False


def main():
    if not os.path.exists(CONFIG_PATH):
        print(f"Нет файла конфигурации: {CONFIG_PATH}")
        sys.exit(1)

    config = load_json(CONFIG_PATH, None)
    if not config:
        print("Ошибка чтения конфигурации.")
        sys.exit(1)

    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}

    token = config.get("telegram_bot_token", "")
    chat_id = config.get("telegram_chat_id", "")
    if "ВСТАВЬТЕ" in (token, chat_id) or not token or not chat_id:
        print("Сначала заполните telegram_bot_token и telegram_chat_id в config.json")
        sys.exit(1)

    errors_occurred = False
    for mailbox in config.get("mailboxes", []):
        mailbox_name = mailbox.get("name", "Mailbox")
        try:
            sent, processed_ids, had_error = process_mailbox(config, mailbox, state)
            state.setdefault(mailbox_name, {})["processed"] = processed_ids
            errors_occurred = errors_occurred or had_error
        except Exception as exc:
            print(f"[{mailbox_name}] Ошибка: {exc}")
            errors_occurred = True
            try:
                send_to_telegram(
                    config,
                    "Ошибка проверки почты",
                    mailbox_name,
                    time.strftime("%d.%m.%Y %H:%M"),
                    str(exc),
                )
            except Exception:
                pass

    save_json(STATE_PATH, state)


if __name__ == "__main__":
    main()