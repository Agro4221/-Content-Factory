"""Маркировка медиафайлов очереди для привязки к исходному посту.

Метка в имени файла позволяет проверить принадлежность вложения посту перед
публикацией и не отправить медиа из соседней записи. Файлы без метки
поддерживаются для совместимости с ранее сохранёнными данными очереди.

Формат имени: ``p<источник>-<номер поста>_<время>_<номер файла><расширение>``.
"""
from __future__ import annotations

import os
import re

#: Метка поста в имени файла: p<источник>-<номер поста>.
MARKER_PREFIX = "p"

#: Разбирает метку из имени файла. Метка не содержит подчёркиваний,
#: поэтому первая часть имени до «_» — это метка целиком.
_MARKER_RE = re.compile(r"^p[0-9A-Za-z-]+$")

#: Полное имя файла с меткой: метка, время, номер, расширение.
_MARKED_NAME_RE = re.compile(r"^p[0-9A-Za-z-]+_\d+_\d+(\.\w+)?$", re.IGNORECASE)


def media_marker(source: str, post_id: str | int) -> str:
    """Метка поста для имени медиафайла."""
    safe_source = re.sub(r"[^0-9A-Za-z-]+", "-", str(source or "")).strip("-")
    safe_post = re.sub(r"[^0-9A-Za-z-]+", "-", str(post_id or "")).strip("-")
    return f"{MARKER_PREFIX}{safe_source or 'src'}-{safe_post or '0'}"


def media_file_name(marker: str, timestamp_ms: int, index: int, extension: str) -> str:
    """Имя файла очереди с меткой поста."""
    ext = extension if extension.startswith(".") else f".{extension}" if extension else ""
    return f"{marker}_{int(timestamp_ms)}_{int(index)}{ext}"


def file_marker(file_name: str) -> str:
    """Метка из имени файла. Пустая строка — метки нет (старый файл)."""
    name = str(file_name or "").strip()
    if not name:
        return ""
    marker = name.split("_", 1)[0]
    if not _MARKER_RE.match(marker):
        return ""
    return marker


def has_marker(file_name: str) -> bool:
    """Есть ли в имени файла метка поста."""
    return bool(file_marker(file_name))


def belongs_to_post(file_name: str, marker: str) -> bool:
    """Принадлежит ли файл посту с такой меткой.

    Файл без метки (созданный до появления маркировки) считается
    принадлежащим посту, в котором он перечислен: иначе накопленная
    очередь перестала бы публиковаться после обновления.
    """
    file_marker_value = file_marker(file_name)
    if not file_marker_value:
        return True
    return file_marker_value == marker


def looks_like_marked_name(file_name: str) -> bool:
    """Похоже ли имя на созданное по новым правилам."""
    return bool(_MARKED_NAME_RE.match(str(file_name or "").strip()))


def clean_filename(file_name: str) -> str:
    """Имя файла без маркировки — то, что увидит пользователь в канале.

    Метка нужна для внутреннего учёта: по ней видно, чьё это медиа, и
    она защищает от подмены. Но в Telegram имя файла видно пользователю,
    поэтому метка оттуда убирается.
    """
    name = os.path.basename(str(file_name or "").replace("\\", "/")).strip()
    if not name:
        return "media"
    marker = file_marker(name)
    if not marker:
        return name
    # Убираем метку и следующий за ней разделитель.
    remainder = name[len(marker):].lstrip("_")
    return remainder or "media"
