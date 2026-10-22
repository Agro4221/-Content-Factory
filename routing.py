"""Маршрутизация постов по тегам.

Модель возвращает хештеги. Каждый канал настроен на один или несколько
тегов. Пост уходит во все каналы, у которых есть хотя бы один из его
тегов: если «политика» стоит у трёх каналов, новость про политику придёт
во все три.

Особый тег «общее» (или «*») означает канал для постов, чьи теги не
подошли ни к одному маршруту, — и для постов вообще без тегов.
"""
from __future__ import annotations

import json
import re
from typing import Any

#: Тег, в который попадают посты без подходящего маршрута.
FALLBACK_TAG = "общее"

#: Символ «звёздочка» — ещё одно обозначение запасного тега.
FALLBACK_TAG_ALIASES = ("*", "любое", "все", "общее")

#: Хештег в тексте: решётка и слово без пробелов. Поддерживает кириллицу.
HASHTAG_RE = re.compile(r"#(?P<tag>[\w\-]+)", re.UNICODE)

#: Хештег вместе с пробелами перед ним — чтобы в тексте не оставалось
#: «про .» вместо «про.».
HASHTAG_WITH_SPACES_RE = re.compile(r"[ \t]*#(?P<tag>[\w\-]+)", re.UNICODE)

#: Тег в настройках: буквы, цифры, дефис и подчёркивание.
_TAG_RE = re.compile(r"^[^\W_][\w\-]*$", re.UNICODE)


def normalize_tag(raw: object) -> str:
    """Приводит тег к единому виду: без решётки, в нижнем регистре."""
    text = str(raw or "").strip().lstrip("#").strip()
    text = re.sub(r"\s+", " ", text)
    return text.lower()


def is_valid_tag(tag: str) -> bool:
    """Годится ли тег для маршрута."""
    return bool(tag) and bool(_TAG_RE.match(tag))


def parse_tag_list(raw: object) -> list[str]:
    """Разбирает список тегов из строки, списка или JSON-массива.

    Принимает «#политика, #срочное», «политика срочное» и
    ``["политика", "срочное"]`` — формат зависит от того, откуда пришли
    настройки.
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        candidates: list[str] = []
        for item in raw:
            candidates.extend(_split_tag_text(str(item)))
    elif isinstance(raw, str):
        text = raw.strip()
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, list):
                return parse_tag_list(parsed)
        candidates = _split_tag_text(text)
    else:
        candidates = _split_tag_text(str(raw))

    tags: list[str] = []
    for candidate in candidates:
        tag = normalize_tag(candidate)
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def _split_tag_text(text: str) -> list[str]:
    """Делит строку с тегами на отдельные теги.

    Разделители: запятая, точка с запятой, пробел. Хештег без разделителя
    («#политика#срочное») тоже распознаётся.
    """
    if not text:
        return []
    parts = re.split(r"[,;]+|\s+", text)
    result: list[str] = []
    for part in parts:
        for piece in HASHTAG_RE.findall(part) if "#" in part else [part]:
            cleaned = str(piece).strip()
            if cleaned:
                result.append(cleaned)
    return result


def _looks_like_chat(value: str) -> bool:
    """Похоже ли значение на ссылку на канал."""
    text = str(value or "").strip()
    if not text:
        return False
    if text.startswith(("@", "-", "http://", "https://", "t.me/", "tg://")):
        return True
    return text.isdigit()


def parse_routes(raw: object) -> dict[str, list[str]]:
    """Разбирает маршруты из JSON: канал → список тегов.

    Поддерживается и старый формат «категория → канал»: он превращается
    в маршрут «канал → [категория]», чтобы старые настройки продолжали
    работать после обновления.
    """
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            raw = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Маршруты должны быть корректным JSON-объектом; настройки не применены."
            ) from exc
    if not isinstance(raw, dict):
        # Это не ошибка типа аргумента, а неверные данные из настроек:
        # вызывающий код ловит ValueError и сообщает пользователю.
        raise ValueError(  # noqa: TRY004
            "Маршруты должны быть JSON-объектом; настройки не применены."
        )

    routes: dict[str, list[str]] = {}
    for channel, tags in raw.items():
        channel_name = str(channel or "").strip()
        if not channel_name:
            continue

        # Старый формат «категория → канал»: значение похоже на канал,
        # а ключ на канал не похож. Разворачиваем такую пару.
        if isinstance(tags, str) and _looks_like_chat(tags) and not _looks_like_chat(channel_name):
            channel_name, tags = tags.strip(), channel_name

        tag_list = parse_tag_list(tags)
        if not tag_list:
            # Пустой список тегов у маршрута бессмыслен: такой канал
            # не получит ни одного поста.
            continue
        if channel_name in routes:
            for tag in tag_list:
                if tag not in routes[channel_name]:
                    routes[channel_name].append(tag)
        else:
            routes[channel_name] = tag_list
    return routes


def routes_to_json(routes: dict[str, list[str]]) -> str:
    """Готовит маршруты к сохранению в настройках."""
    payload = {
        str(channel): list(tags)
        for channel, tags in routes.items()
        if str(channel).strip() and tags
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def all_tags(routes: dict[str, list[str]]) -> list[str]:
    """Все теги, которые встречаются в маршрутах."""
    tags: list[str] = []
    for tag_list in routes.values():
        for tag in tag_list:
            if tag not in tags:
                tags.append(tag)
    return tags


def is_fallback_tag(tag: str) -> bool:
    """Является ли тег запасным."""
    return normalize_tag(tag) in FALLBACK_TAG_ALIASES


def match_channels(routes: dict[str, list[str]], tags: list[str] | str) -> list[str]:
    """Какие каналы получают пост с такими тегами.

    Совпадение по любому тегу: канал настроен на «политика» и «срочное» —
    пост с тегом «политика» уйдёт в него тоже. Каналы возвращаются в том
    же порядке, что и в настройках, повторов нет.
    """
    if isinstance(tags, str):
        tags = [tags]
    wanted = {normalize_tag(tag) for tag in tags if normalize_tag(tag)}
    wanted.discard("")

    matched: list[str] = []
    for channel, tag_list in routes.items():
        channel_tags = {normalize_tag(tag) for tag in tag_list}
        if channel_tags & wanted:
            matched.append(channel)
    return matched


def fallback_channels(routes: dict[str, list[str]]) -> list[str]:
    """Каналы, настроенные на запасной тег."""
    result: list[str] = []
    for channel, tag_list in routes.items():
        if any(is_fallback_tag(tag) for tag in tag_list):
            result.append(channel)
    return result


def resolve_channels(routes: dict[str, list[str]], tags: list[str] | str) -> list[str]:
    """Каналы для поста: по тегам, а если совпадений нет — запасные."""
    matched = match_channels(routes, tags)
    if matched:
        return matched
    return fallback_channels(routes)


def extract_tags(text: str) -> list[str]:
    """Собирает хештеги из текста."""
    tags: list[str] = []
    for match in HASHTAG_RE.finditer(text or ""):
        tag = normalize_tag(match.group("tag"))
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def strip_hashtags(text: str) -> str:
    """Убирает хештеги из текста.

    Теги нужны только для маршрутизации — в канале они лишние.
    """
    if not text:
        return ""
    cleaned = HASHTAG_WITH_SPACES_RE.sub("", text)
    # Убираем пустые строки, оставшиеся от удалённых тегов.
    lines = [line.rstrip() for line in cleaned.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    result = "\n".join(lines)
    # Двойные пустые строки подряд — след от удалённых тегов.
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def describe_routes(routes: dict[str, list[str]]) -> str:
    """Человекочитаемый список маршрутов для журнала."""
    if not routes:
        return "маршруты не настроены"
    return "; ".join(
        f"{channel} → {', '.join(tags)}" for channel, tags in routes.items()
    )


def routes_from_any(value: Any) -> dict[str, list[str]]:
    """Маршруты из любого источника: JSON-строка, словарь или список."""
    try:
        return parse_routes(value)
    except ValueError:
        return {}
