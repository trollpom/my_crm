"""Запуск локального планировщика и открытие браузера."""
import threading
import time
import webbrowser

import uvicorn

from app import db

HOST = "127.0.0.1"
PORT = 8777


def open_browser():
    time.sleep(1.2)
    webbrowser.open(f"http://{HOST}:{PORT}/")


if __name__ == "__main__":
    db.init_db()
    threading.Thread(target=open_browser, daemon=True).start()
    print(f"Планировщик запущен: http://{HOST}:{PORT}/  (Ctrl+C — остановить)")
    uvicorn.run("app.main:app", host=HOST, port=PORT, log_level="warning")
