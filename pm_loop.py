import datetime
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pm_core
import pm_mail


def run_cycle():
    errors = []
    config = pm_core.load_config()

    # 1. Загрузка новых писем
    for mailbox in config.get("mailboxes", []):
        if not mailbox.get("email"):
            continue
        try:
            msgs, err = pm_mail.fetch_new_messages(mailbox)
            if err:
                errors.append(f"[{mailbox['name']}] {err}")
            elif msgs:
                print(f"{datetime.datetime.now():%H:%M:%S} Новых писем: {len(msgs)}")
                for m in msgs:
                    print(f"  • {m['subject']}")
        except Exception as exc:
            errors.append(f"[{mailbox['name']}] {exc}")

    # 2. Напоминания по дедлайнам
    try:
        _run_reminders()
    except Exception as exc:
        errors.append(f"remind: {exc}")

    # 3. Ежедневный план в заданное время
    try:
        _run_daily_digest()
    except Exception as exc:
        errors.append(f"digest: {exc}")

    return errors


def _run_reminders():
    db = pm_core.load_db()
    config = pm_core.load_config()
    now = pm_core.now()
    window_min = config.get("remind_minutes_before_deadline", 60)
    # тёплое окно: напоминаем, если до дедлайна <= window_min и не раньше чем на сутки вперёд
    start = -30 * 60  # до 30 минут после дедлайна тоже напоминаем (просроченное)
    tasks = [t for t in db["tasks"] if t["status"] == pm_core.STATUS_OPEN and t.get("deadline")]
    for t in tasks:
        dt = datetime.datetime.fromisoformat(t["deadline"])
        delta = (dt - now).total_seconds()
        if start <= delta <= window_min * 60 and not t.get("reminded"):
            when = pm_core.format_relative(dt)
            time_s = dt.strftime("в %H:%M")
            text = f"⏰ НАПОМИНАНИЕ о дедлайне!\n\n{t['title']}\nСрок: {when} {time_s}"
            ok, err = pm_core.send_telegram(text, config)
            if ok:
                t["reminded"] = True
                status = "выполнено"
            else:
                status = f"ошибка: {err}"
            print(f"{datetime.datetime.now():%H:%M:%S} Напоминание #{t['id']}: {status}")
    pm_core.save_db(db)


def _run_daily_digest():
    config = pm_core.load_config()
    digest_time = config.get("digest_time", "09:00")
    h, m = map(int, digest_time.split(":"))
    now = pm_core.now()
    if now.hour != h or now.minute != m:
        return
    digest_file = os.path.join(pm_core.SCRIPT_DIR, "last_digest.txt")
    today = now.strftime("%Y-%m-%d")
    last = ""
    if os.path.exists(digest_file):
        with open(digest_file, "r", encoding="utf-8") as f:
            last = f.read().strip()
    if last == today:
        return
    db = pm_core.load_db()
    today_date = now.date()

    # считаем фокус: просроченные + сегодня + ближайшие 3 дня
    tasks = [t for t in db["tasks"] if t["status"] == pm_core.STATUS_OPEN]
    lines = ["☀️ ДОБРОЕ УТРО! Ваш план на сегодня:", ""]
    overdue = [t for t in tasks if t.get("deadline") and datetime.datetime.fromisoformat(t["deadline"]).date() < today_date]
    due_today = [t for t in tasks if t.get("deadline") and datetime.datetime.fromisoformat(t["deadline"]).date() == today_date]
    upcoming = [t for t in tasks if t.get("deadline") and today_date < datetime.datetime.fromisoformat(t["deadline"]).date() <= today_date + datetime.timedelta(days=3)]

    if overdue:
        lines.append("⚠️ Просрочено:")
        for t in overdue:
            lines.append(f"  #{t['id']} {t['title']}")
        lines.append("")
    if due_today:
        lines.append("🎯 Сегодня:")
        for t in due_today:
            lines.append(f"  #{t['id']} {t['title']}")
        lines.append("")
    if upcoming:
        lines.append("🔜 В ближайшие 3 дня:")
        for t in upcoming:
            lines.append(f"  #{t['id']} {t['title']} — {pm_core.format_relative(datetime.datetime.fromisoformat(t['deadline']))}")
        lines.append("")

    # письма за прошлые сутки
    messages = pm_mail.load_cached_messages(2)
    recent = []
    keys = set()
    for m in messages:
        try:
            d = datetime.datetime.fromisoformat(m["date"])
        except Exception:
            continue
        if d.date() >= today_date - datetime.timedelta(days=1):
            key = m["subject"] + m["sender"]
            if key not in keys:
                keys.add(key)
                recent.append(m)
    if recent:
        lines.append(f"📬 Писем за 2 дня: {len(recent)}")
        for m in recent[:10]:
            lines.append(f"  • {m['subject']}")
        lines.append("")

    if not (overdue or due_today or upcoming):
        lines.append("Сегодня задач нет. Отдыхайте! 🎉")

    text = "\n".join(lines)
    ok, err = pm_core.send_telegram(text, config)
    if ok:
        with open(digest_file, "w", encoding="utf-8") as f:
            f.write(today)
        print(f"{datetime.datetime.now():%H:%M:%S} Ежедневный план отправлен")
    else:
        print(f"{datetime.datetime.now():%H:%M:%S} Ошибка отправки плана: {err}")


def main():
    config = pm_core.load_config()
    interval = config.get("check_interval_seconds", 60)
    print(f"Фоновый планировщик запущен (каждые {interval} сек). Ctrl+C — остановка.")
    while True:
        try:
            errors = run_cycle()
            for err in errors:
                print(f"ERROR: {err}")
        except Exception as exc:
            print(f"ERROR cycle: {exc}")
        time.sleep(interval)


if __name__ == "__main__":
    main()