"""Единая точка чтения/записи config.json."""
import json
import os

from . import db

CONFIG_PATH = os.path.join(db.BASE_DIR, "config.json")

DEFAULTS = {
    "app_name": "Планировщик работы",
    "folders": {"projects_base": os.path.join(db.BASE_DIR, "projects")},
    "ai": {
        "enabled": True,
        "provider": "deepseek",
        "api_key": "",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
    },
}


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def ai_config():
    cfg = load_config()
    merged = dict(DEFAULTS["ai"])
    merged.update(cfg.get("ai") or {})
    return merged


def projects_base():
    cfg = load_config()
    return (cfg.get("folders") or {}).get("projects_base") or DEFAULTS["folders"]["projects_base"]


def app_name():
    return load_config().get("app_name") or DEFAULTS["app_name"]
