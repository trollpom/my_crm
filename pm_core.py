"""Ядро персонального менеджера: база задач/проектов, парсинг дат, Telegram."""
import datetime
import json
import os
import re
import sys
from urllib.parse import quote

try:
    import requests
except ImportError:
    requests = None

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
DB_PATH = os.path.join(SCRIPT_DIR, "tasks.json")
ICS_PATH = os.path.join(SCRIPT_DIR, "calendar.ics")
DESKTOP_PATH = os.path.join(os.environ.get("USERPROFILE", SCRIPT_DIR), "Desktop")

STATUS_OPEN = "open"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"
PRIORITIES = {"high": "🔴", "normal": "🟡", "low": "🔵"}

MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4, "май": 5, "июнь": 6,
    "июль": 7, "август": 8, "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
}
MONTHS_EN = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
MONTHS_RU_ACC = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
MONTHS_RU_NOM = {
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4, "май": 5, "июнь": 6,
    "июль": 7, "август": 8, "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
}
WEEKDAYS_RU = {
    "понедельник": 0, "вторник": 1, "среда": 2, "четверг": 3, "пятница": 4,
    "суббота": 5, "воскресенье": 6, "пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6,
}
WEEKDAYS_EN = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
    "saturday": 5, "sunday": 6, "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}


def now():
    return datetime.datetime.now()


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return default
    return default


def save_json(path, data):
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_config():
    return load_json(CONFIG_PATH, {})


def load_db():
    return load_json(DB_PATH, {"tasks": [], "projects": {}, "next_id": 1, "reminded": {}})


def save_db(db):
    save_json(DB_PATH, db)


def send_telegram(config, text):
    """Отправляет сообщение в Telegram. Возвращает (ok, error)."""
    token = config.get("telegram_bot_token", "")
    chat_id = config.get("telegram_chat_id")
    if not token or not chat_id:
        return False, "Telegram не настроен (нет токена/chat_id)"
    if requests is None:
        return False, "Нет библиотеки requests"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    try:
        r = requests.post(url, json=payload, timeout=20)
        data = r.json()
        if data.get("ok"):
            return True, ""
        return False, data.get("description", "ошибка API")
    except Exception as e:
        return False, str(e)


# ---------- Парсинг дат ----------

def parse_time(text):
    """Возвращает (час, минута) из строки или None. Игнорирует даты вида HH.MM → не путать с dd.mm."""
    text = text.strip()
    # HH:MM — всегда время
    m = re.search(r"(?<![\d.])(\d{1,2}):(\d{2})(?!\d)", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return h, mi
    # "в 15.30", "до 18.30", "at 15.30" — время с точкой, но только с предлогом
    m = re.search(r"(?:в|до|к|at)\s+(\d{1,2})\.(\d{2})(?!\d)", text.lower())
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59 and 0 <= mi <= 59:
            return h, mi
    return None


def parse_date(text, base=None):
    """Возвращает дату (datetime) из текста или None. 'завтра', '25.09.2026', '25 сентября', 'пятница'."""
    base = base or now()
    text = text.strip().lower()
    if not text:
        return None
    if text in ("сегодня", "today"):
        return base.replace(hour=9, minute=0, second=0, microsecond=0)
    if text in ("завтра", "tomorrow"):
        return (base + datetime.timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    if text in ("послезавтра",):
        return (base + datetime.timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
    if text.startswith("через"):
        m = re.search(r"(\d+)\s+(дн|день|дня|часов|час|часа|нед|нед\.|недели|недель|минут)", text)
        if m:
            n = int(m.group(1))
            unit = m.group(2)
            if unit.startswith("нед"):
                delta = datetime.timedelta(weeks=n)
            elif unit.startswith("час"):
                delta = datetime.timedelta(hours=n)
            elif unit.startswith("мин"):
                delta = datetime.timedelta(minutes=n)
            else:
                delta = datetime.timedelta(days=n)
            return (base + delta).replace(second=0, microsecond=0)
    if text in WEEKDAYS_RU:
        target = WEEKDAYS_RU[text]
        days_ahead = (target - base.weekday() + 7) % 7
        if days_ahead == 0:
            days_ahead = 7
        return (base + datetime.timedelta(days=days_ahead)).replace(hour=9, minute=0, second=0, microsecond=0)
    if text in WEEKDAYS_EN:
        target = WEEKDAYS_EN[text]
        days_ahead = (target - base.weekday() + 7) % 7
        if days_ahead == 0:
            days_ahead = 7
        return (base + datetime.timedelta(days=days_ahead)).replace(hour=9, minute=0, second=0, microsecond=0)

    # dd.mm.yyyy | dd-mm-yyyy | dd/mm/yyyy | dd.mm.yy
    m = re.match(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", text)
    if m:
        day, mon, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        try:
            return datetime.datetime(year, mon, day, 9, 0)
        except ValueError:
            return None

    # dd.MM (текущий год), например "25.09"
    m = re.match(r"(\d{1,2})[./-](\d{1,2})$", text)
    if m:
        day, mon = int(m.group(1)), int(m.group(2))
        try:
            for cand_year in (base.year, base.year + 1):
                dt = datetime.datetime(cand_year, mon, day, 9, 0)
                if dt >= base - datetime.timedelta(days=1):
                    return dt
        except ValueError:
            pass
        return None

    # "25 сентября" без года
    m = re.match(r"(\d{1,2})\s+([а-яё]{3,12})", text)
    if m:
        day, mon_name = int(m.group(1)), m.group(2)
        mon = MONTHS_RU_ACC.get(mon_name) or MONTHS_RU_NOM.get(mon_name)
        if mon:
            for cand_year in (base.year, base.year + 1):
                try:
                    dt = datetime.datetime(cand_year, mon, day, 9, 0)
                    if dt >= base - datetime.timedelta(days=1):
                        return dt
                except ValueError:
                    pass
            return None

    # "25 сентября 2026" с годом
    m = re.match(r"(\d{1,2})\s+([а-яё]{3,12})\s+(\d{4})", text)
    if m:
        day, mon_name, year = int(m.group(1)), m.group(2), int(m.group(3))
        mon = MONTHS_RU_ACC.get(mon_name) or MONTHS_RU_NOM.get(mon_name)
        if mon:
            try:
                return datetime.datetime(year, mon, day, 9, 0)
            except ValueError:
                return None
    return None


def parse_datetime(text, base=None):
    """Парсит строку с датой и временем: 'завтра 15:30' или '25.09.2026 18:00'."""
    base = base or now()
    time_part = parse_time(text)
    date_text = text
    # убираем найденное время HH:MM или 'в 15.30'
    date_text = re.sub(r"(?<![\d.])\d{1,2}:\d{2}(?!\d)", "", date_text)
    date_text = re.sub(r"(?:в|до|к|at)\s+\d{1,2}\.\d{2}(?!\d)", "", date_text, flags=re.I)
    dt = parse_date(date_text.strip(), base)
    if dt is None:
        return None
    if time_part:
        dt = dt.replace(hour=time_part[0], minute=time_part[1])
    return dt


def format_relative(dt, base=None):
    base = base or now()
    today = base.date()
    d = dt.date()
    if d < today:
        days = (today - d).days
        return f"просрочено на {days} дн."
    if d == today:
        if dt.time() >= base.time():
            return f"сегодня в {dt:%H:%M}"
        return "сегодня (прошло)"
    if d == today + datetime.timedelta(days=1):
        return f"завтра в {dt:%H:%M}"
    days = (d - today).days
    if days <= 7:
        return f"через {days} дн. ({dt:%d.%m} в {dt:%H:%M})"
    return f"{dt:%d.%m.%Y в %H:%M}"


def make_task_id(db):
    tid = db.get("next_id", 1)
    db["next_id"] = tid + 1
    return tid


def desktop_file(name):
    return os.path.join(DESKTOP_PATH, name)
