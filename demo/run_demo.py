"""Isolated localhost demo: copies app code, never copies user config or data."""
import argparse
import datetime as dt
import json
from pathlib import Path
import shutil
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8777)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Use a port between 1024 and 65535")
    source = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="my-crm-demo-") as folder:
        root = Path(folder)
        shutil.copytree(source / "app", root / "app", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (root / "config.json").write_text(json.dumps({
            "app_name": "ДЕМО — синтетические данные", "mailboxes": [],
            "ai": {"enabled": False, "api_key": ""},
            "folders": {"projects_base": str(root / "projects")}
        }, ensure_ascii=False), encoding="utf-8")
        sys.path.insert(0, str(root))
        from app import db
        db.init_db()
        today = dt.date.today()
        now = db.now_iso()
        projects = [("ДЕМО · Запуск B2B-сервиса", "Вымышленный проект: исследование, прототип, обратная связь", "#5b8cff"),
                    ("ДЕМО · Клиентский портал", "Учебный сценарий без реальных клиентов", "#b18bfa"),
                    ("ДЕМО · Подготовка к хакатону", "Материалы, демонстрация и презентация", "#30b98c")]
        tasks = [("Провести интервью по сценарию", 1, "done", "high", -2, 100),
                 ("Собрать прототип кабинета", 1, "doing", "high", 2, 65),
                 ("Описать критерии приёмки", 1, "todo", "normal", 4, 0),
                 ("Проверить форму обратной связи", 2, "doing", "normal", 1, 40),
                 ("Подготовить синтетические примеры", 2, "done", "normal", -1, 100),
                 ("Записать демонстрацию продукта", 3, "todo", "high", 3, 0),
                 ("Собрать слайды с архитектурой", 3, "doing", "normal", 5, 30),
                 ("Проверить инструкцию запуска", 3, "todo", "low", 6, 0)]
        with db.get_conn() as conn:
            conn.executemany("INSERT INTO projects(name,description,color,created) VALUES(?,?,?,?)", [(*p, now) for p in projects])
            for title, pid, status, priority, offset, progress in tasks:
                conn.execute("INSERT INTO tasks(title,description,project_id,status,priority,deadline,start_date,progress,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (title, "Синтетическая задача для публичной демонстрации. Реальных компаний и персональных данных нет.", pid, status, priority,
                     (today + dt.timedelta(days=offset)).isoformat(), (today-dt.timedelta(days=4)).isoformat(), progress, now, now))
            conn.execute("INSERT INTO notes(title,content,created,updated) VALUES(?,?,?,?)", ("О демонстрации", "Все записи вымышлены. Почта и платный API отключены. Данные удаляются после остановки сервера.", now, now))
        import uvicorn
        print(f"ДЕМО: http://127.0.0.1:{args.port} — синтетические данные, без почты и LLM. Ctrl+C для выхода.")
        uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, workers=1)


if __name__ == "__main__":
    main()
