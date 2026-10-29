"""Работа с файлами: создание папок проектов, сохранение вложений."""
import os
import re
import shutil
from datetime import datetime

from . import config

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(name, fallback="вложение"):
    """Убирает запрещённые символы для имени файла/папки в Windows."""
    name = (name or "").strip()
    name = _INVALID.sub("_", name)
    name = name.strip(" .")
    return name or fallback


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def project_default_folder(project_name):
    """Создаёт и возвращает папку проекта внутри базовой директории."""
    base = config.projects_base()
    folder = os.path.join(base, safe_name(project_name, fallback=f"проект_{datetime.now():%Y%m%d_%H%M%S}"))
    return ensure_dir(folder)


def task_folder(task_title, task_id):
    """Папка для задачи без проекта: <base>/_Задачи/<id>_<тема>."""
    base = config.projects_base()
    sub = f"{task_id}_{safe_name(task_title, fallback='задача')[:50]}"
    return ensure_dir(os.path.join(base, "_Задачи", sub))


def save_bytes(dst_dir, filename, data):
    """Сохраняет байты в файл (с уникальным именем), возвращает путь."""
    if not data:
        return None
    ensure_dir(dst_dir)
    fname = safe_name(os.path.basename(filename or ""))
    dst = os.path.join(dst_dir, fname)
    base, ext = os.path.splitext(fname)
    i = 1
    while os.path.exists(dst):
        dst = os.path.join(dst_dir, f"{base}_{i}{ext}")
        i += 1
    with open(dst, "wb") as f:
        f.write(data)
    return dst


def copy_attachment(src, dst_dir, filename=None):
    ensure_dir(dst_dir)
    if not os.path.exists(src):
        raise FileNotFoundError(src)
    with open(src, "rb") as f:
        data = f.read()
    return save_bytes(dst_dir, filename or os.path.basename(src), data)
