import argparse
import datetime
import os
import sys

import pm_core
import pm_mail

PRIORITIES_MARK = {"high": "🔴", "normal": "🟡", "low": "🔵"}


def save_report(config, filename, text):
    plan_path = config.get("desktop_plan_path")
    if not plan_path:
        return None
    out = os.path.join(os.path.dirname(plan_path), filename)
    try:
        pm_core.ensure_dir(os.path.dirname(out))
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        return out
    except OSError as e:
        print(f"⚠️ Не удалось сохранить {filename}: {e}")
        return None


def cmd_add(args):
    floor = datetime.datetime.now().replace(hour=9, minute=0, second=0, microsecond=0) - datetime.timedelta(days=3650)
    deadline = None
    if args.deadline:
        deadline = pm_core.parse_datetime(args.deadline)
        if deadline is None or deadline < floor:
            print(f"Ошибка: не могу разобрать дату дедлайна: {args.deadline}")
            sys.exit(1)
    task = pm_core.add_task(
        title=args.title,
        deadline=deadline.isoformat() if deadline else None,
        project=args.project,
        priority=args.priority,
        notes=args.notes,
    )
    dl = pm_core.format_relative(deadline) if deadline else "—"
    print(f"✅ Задача #{task['id']} добавлена: {task['title']} (дедлайн: {dl})")
    if args.send:
        text = f"Задача #{task['id']}: {task['title']}\nДедлайн: {dl}"
        ok, err = pm_core.send_telegram(pm_core.load_config(), text)
        if not ok:
            print(f"Telegram: {err}")


def cmd_list(args):
    db = pm_core.load_db()
    tasks = [t for t in db["tasks"] if t["status"] == pm_core.STATUS_OPEN]
    tasks.sort(key=lambda t: (t.get("deadline") or "9999", t["id"]))
    if not tasks:
        print("Нет открытых задач.")
        return
    for t in tasks:
        color = PRIORITIES_MARK.get(t.get("priority", "normal"), "⚪")
        dl = t.get("deadline")
        dl_s = pm_core.format_relative(datetime.datetime.fromisoformat(dl)) if dl else "—"
        project = f" [{t.get('project')}]" if t.get("project") else ""
        print(f"{color} #{t['id']} {t['title']}{project} — дедлайн: {dl_s}")


def cmd_done(args):
    db = pm_core.load_db()
    task = pm_core.find_task(db, args.id)
    if not task:
        print(f"Задача #{args.id} не найдена.")
        sys.exit(1)
    task["status"] = pm_core.STATUS_DONE
    pm_core.save_db(db)
    print(f"✅ Задача #{task['id']} выполнена: {task['title']}")


def cmd_cancel(args):
    db = pm_core.load_db()
    task = pm_core.find_task(db, args.id)
    if not task:
        print(f"Задача #{args.id} не найдена.")
        sys.exit(1)
    task["status"] = pm_core.STATUS_CANCELLED
    pm_core.save_db(db)
    print(f"⛔ Задача #{task['id']} отменена: {task['title']}")


def cmd_edit(args):
    db = pm_core.load_db()
    task = pm_core.find_task(db, args.id)
    if not task:
        print(f"Задача #{args.id} не найдена.")
        sys.exit(1)
    kwargs = {}
    if args.title:
        kwargs["title"] = args.title
    if args.project is not None:
        kwargs["project"] = args.project or None
    if args.priority:
        kwargs["priority"] = args.priority
    if args.notes is not None:
        kwargs["notes"] = args.notes
    if args.deadline:
        dt = pm_core.parse_datetime(args.deadline)
        if dt is None:
            print(f"Ошибка: не могу разобрать дату: {args.deadline}")
            sys.exit(1)
        kwargs["deadline"] = dt.isoformat()
        kwargs["reminded"] = False
    pm_core.update_fields(task, **kwargs)
    pm_core.save_db(db)
    print(f"✅ Задача #{task['id']} обновлена: {task['title']}")


def cmd_today(args):
    db = pm_core.load_db()
    today = pm_core.now().date()
    lines = []
    lines.append(f"📅 ПЛАН НА СЕГОДНЯ: {today.strftime('%d.%m.%Y (%A)')}")
    lines.append("")

    tasks = [t for t in db["tasks"] if t["status"] == pm_core.STATUS_OPEN]
    overdue = sorted([t for t in tasks if t.get("deadline") and datetime.datetime.fromisoformat(t["deadline"]).date() < today], key=lambda t: t["deadline"])
    today_tasks = sorted([t for t in tasks if t.get("deadline") and datetime.datetime.fromisoformat(t["deadline"]).date() == today], key=lambda t: t["deadline"])
    upcoming = sorted([t for t in tasks if t.get("deadline") and today < datetime.datetime.fromisoformat(t["deadline"]).date() <= today + datetime.timedelta(days=3)], key=lambda t: t["deadline"])

    if overdue:
        lines.append("⚠️ ПРОСРОЧЕННЫЕ:")
        for t in overdue:
            lines.append(f"  #{t['id']} {t['title']} (дедлайн был {pm_core.format_relative(datetime.datetime.fromisoformat(t['deadline']))})")
        lines.append("")
    if today_tasks:
        lines.append("🎯 СЕГОДНЯ:")
        for t in today_tasks:
            dl = datetime.datetime.fromisoformat(t["deadline"])
            time_s = dl.strftime("в %H:%M") if dl.strftime("%H:%M") != "09:00" else ""
            lines.append(f"  {PRIORITIES_MARK.get(t.get('priority'), '⚪')} #{t['id']} {t['title']} {time_s}")
        lines.append("")
    if upcoming:
        lines.append("🔜 БЛИЖАЙШИЕ (3 дня):")
        for t in upcoming:
            dl = datetime.datetime.fromisoformat(t["deadline"])
            lines.append(f"  #{t['id']} {t['title']} — {dl.strftime('%d.%m')} ({pm_core.format_relative(dl)})")
        lines.append("")
    ifnot_done = [t for t in db["tasks"] if t["status"] == pm_core.STATUS_DONE and t.get("deadline") and datetime.datetime.fromisoformat(t["deadline"]).date() == today]
    if not (overdue or today_tasks or upcoming):
        lines.append("На сегодня открытых задач нет. 🎉")
    lines.append("")

    # письма за сегодня
    messages = pm_mail.load_cached_messages(1)
    today_msgs = [m for m in messages if datetime.datetime.fromisoformat(m["date"]).date() == today]
    if today_msgs:
        lines.append("📬 ПИСЬМА ЗА СЕГОДНЯ:")
        for m in today_msgs[:15]:
            lines.append(f"  • {m['subject']} ({m['sender']})")
        lines.append("")

    text = "\n".join(lines)
    print(text)

    config = pm_core.load_config()
    plan_path = config.get("desktop_plan_path")
    if plan_path:
        try:
            pm_core.ensure_dir(os.path.dirname(plan_path))
            with open(plan_path, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"\n💾 План сохранён: {plan_path}")
        except OSError as e:
            print(f"\n⚠️ Не удалось сохранить план: {e}")

    if args.send:
        ok, err = pm_core.send_telegram(config, text)
        print(f"Telegram: {'отправлено' if ok else 'ошибка ' + err}")


def cmd_remind(args):
    db = pm_core.load_db()
    config = pm_core.load_config()
    now = pm_core.now()
    window_min = config.get("remind_minutes_before_deadline", 60)
    tasks = [t for t in db["tasks"] if t["status"] == pm_core.STATUS_OPEN and t.get("deadline")]
    due = []
    for t in tasks:
        dt = datetime.datetime.fromisoformat(t["deadline"])
        delta = (dt - now).total_seconds()
        if -3600 <= delta <= window_min * 60 and not t.get("reminded"):
            due.append((t, dt))
    for t, dt in due:
        when = pm_core.format_relative(dt)
        time_s = dt.strftime("в %H:%M")
        text = f"⏰ НАПОМИНАНИЕ о дедлайне!\n\n{t['title']}\nСрок: {when} {time_s}"
        ok, err = pm_core.send_telegram(config, text)
        if ok:
            t["reminded"] = True
            print(f"Уведомление отправлено по задаче #{t['id']}")
        else:
            print(f"Не удалось отправить по #{t['id']}: {err}")
    pm_core.save_db(db)
    if not due:
        print("Напоминаний на сейчас нет.")


def cmd_fetch(args):
    config = pm_core.load_config()
    for mailbox in config.get("mailboxes", []):
        if not mailbox.get("email"):
            print(f"[{mailbox['name']}] не задан email — пропуск")
            continue
        msgs, err = pm_mail.fetch_new_messages(mailbox, limit=args.limit)
        if err:
            print(f"[{mailbox['name']}] {err}")
        else:
            print(f"[{mailbox['name']}] новых писем: {len(msgs)}")
            for m in msgs:
                print(f"  • {m['subject']}")


def cmd_unread(args):
    config = pm_core.load_config()
    lines = []
    for mailbox in config.get("mailboxes", []):
        if not mailbox.get("email"):
            continue
        msgs, err = pm_mail.fetch_unread_messages(mailbox, limit=args.limit)
        if err:
            print(f"[{mailbox['name']}] {err}")
            continue
        lines.append(f"[{mailbox['name']}] непрочитанных: {len(msgs)}")
        for m in msgs:
            dt = datetime.datetime.fromisoformat(m["date"])
            lines.append(f"  • {dt.strftime('%d.%m %H:%M')} | {m['sender']}")
            lines.append(f"      {m['subject']}")
    text = "\n".join(lines)
    print(text)
    saved = save_report(config, "НЕПРОЧИТАННЫЕ.txt", text)
    if saved:
        print(f"\n💾 Сохранено: {saved}")


def cmd_starred(args):
    config = pm_core.load_config()
    lines = []
    for mailbox in config.get("mailboxes", []):
        if not mailbox.get("email"):
            continue
        msgs, err = pm_mail.fetch_flagged_messages(mailbox, limit=args.limit)
        if err:
            print(f"[{mailbox['name']}] {err}")
            continue
        lines.append(f"[{mailbox['name']}] избранных: {len(msgs)}")
        for m in msgs:
            dt = datetime.datetime.fromisoformat(m["date"])
            lines.append(f"  ★ {dt.strftime('%d.%m %H:%M')} | {m['sender']}")
            lines.append(f"      {m['subject']}")
    text = "\n".join(lines)
    print(text)
    saved = save_report(config, "ИЗБРАННЫЕ.txt", text)
    if saved:
        print(f"\n💾 Сохранено: {saved}")


def cmd_scan(args):
    config = pm_core.load_config()
    hits = pm_mail.scan_messages(config, lookback_days=args.days)
    if not hits:
        print("Упоминаний сроков в письмах не найдено.")
        return
    for h in hits[:args.limit]:
        print(f"📧 {h['subject']} ({h['sender']}, {h['date'][:10]})")
        seen = set()
        for x in h["hits"]:
            if x["keyword"] in seen:
                continue
            seen.add(x["keyword"])
            date_s = pm_core.format_relative(datetime.datetime.fromisoformat(x["date"])) if x["date"] else "дата не указана"
            print(f"   [{x['keyword']}] → {date_s}")
            if args.verbose:
                print(f"      …{x['snippet']}…")


def cmd_ics(args):
    db = pm_core.load_db()
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//example-pm//RU//EN",
        "CALSCALE:GREGORIAN",
        "BEGIN:VTIMEZONE",
        "TZID:Europe/Moscow",
        "BEGIN:STANDARD",
        "DTSTART:19701025T030000",
        "TZOFFSETFROM:+0400",
        "TZOFFSETTO:+0300",
        "TZNAME:MSK",
        "END:STANDARD",
        "BEGIN:DAYLIGHT",
        "DTSTART:19700329T020000",
        "TZOFFSETFROM:+0300",
        "TZOFFSETTO:+0400",
        "TZNAME:MSD",
        "END:DAYLIGHT",
        "END:VTIMEZONE",
    ]
    for t in db["tasks"]:
        if not t.get("deadline"):
            continue
        dt = datetime.datetime.fromisoformat(t["deadline"])
        uid = f"task-{t['id']}@example-pm"
        dl_utc = dt.strftime("%Y%m%dT%H%M%S")
        summary = t["title"]
        if t.get("project"):
            summary = f"[{t['project']}] {summary}"
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{uid}")
        lines.append(f"DTSTAMP:{pm_core.now().strftime('%Y%m%dT%H%M%SZ')}")
        lines.append(f"DTSTART;TZID=Europe/Moscow:{dl_utc}")
        lines.append(f"DTEND;TZID=Europe/Moscow:{dl_utc}")
        lines.append(f"SUMMARY:{summary}")
        if t["status"] == pm_core.STATUS_DONE:
            lines.append("STATUS:COMPLETED")
        else:
            lines.append("STATUS:CONFIRMED")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    with open(pm_core.ICS_PATH, "w", encoding="utf-8") as f:
        f.write("\r\n".join(lines))
    print(f"Календарь сохранён: {pm_core.ICS_PATH}")


def build_parser():
    p = argparse.ArgumentParser(description="Персональный планировщик на базе рабочей почты")
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("add", help="Добавить задачу")
    a.add_argument("title")
    a.add_argument("--deadline", "-d", help="Срок: 25.09.2026, 25 сентября, завтра 15:30, через 3 дня")
    a.add_argument("--project", "-p", help="Проект/категория")
    a.add_argument("--priority", choices=["high", "normal", "low"], default="normal")
    a.add_argument("--notes", default="")
    a.add_argument("--send", action="store_true", help="Дублировать в Telegram")
    a.set_defaults(func=cmd_add)

    a = sub.add_parser("list")
    a.set_defaults(func=cmd_list)

    a = sub.add_parser("done")
    a.add_argument("id", type=int)
    a.set_defaults(func=cmd_done)

    a = sub.add_parser("cancel")
    a.add_argument("id", type=int)
    a.set_defaults(func=cmd_cancel)

    a = sub.add_parser("edit")
    a.add_argument("id", type=int)
    a.add_argument("--title")
    a.add_argument("--deadline", "-d")
    a.add_argument("--project", "-p")
    a.add_argument("--priority", choices=["high", "normal", "low"])
    a.add_argument("--notes")
    a.set_defaults(func=cmd_edit)

    a = sub.add_parser("today", help="Ежедневный план")
    a.add_argument("--send", action="store_true", help="Отправить в Telegram")
    a.set_defaults(func=cmd_today)

    a = sub.add_parser("remind", help="Отправить напоминания по дедлайнам")
    a.set_defaults(func=cmd_remind)

    a = sub.add_parser("fetch", help="Загрузить новые письма")
    a.add_argument("--limit", type=int, default=50, help="Не более N свежих писем при первом запуске")
    a.set_defaults(func=cmd_fetch)

    a = sub.add_parser("unread", help="Показать непрочитанные письма")
    a.add_argument("--limit", type=int, default=50)
    a.set_defaults(func=cmd_unread)

    a = sub.add_parser("starred", help="Показать избранные (помеченные флагом) письма")
    a.add_argument("--limit", type=int, default=100)
    a.set_defaults(func=cmd_starred)

    a = sub.add_parser("scan", help="Найти сроки в письмах")
    a.add_argument("--days", type=int, default=None, help="Глубина в днях (по умолчанию из конфига)")
    a.add_argument("--limit", type=int, default=20)
    a.add_argument("--verbose", action="store_true")
    a.set_defaults(func=cmd_scan)

    a = sub.add_parser("ics", help="Сгенерировать calendar.ics")
    a.set_defaults(func=cmd_ics)

    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()