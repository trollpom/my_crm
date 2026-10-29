import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import forward_mail

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")


def main():
    config = forward_mail.load_json(CONFIG_PATH, {})
    interval = config.get("check_interval_seconds", 60)
    print(f"Запуск бесконечного цикла проверки (каждые {interval} сек). Ctrl+C для остановки.")
    while True:
        try:
            forward_mail.main()
        except Exception as exc:
            print(f"Ошибка цикла: {exc}")
        time.sleep(interval)


if __name__ == "__main__":
    main()