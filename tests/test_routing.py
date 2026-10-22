"""Маршрутизация по тегам: разбор маршрутов, подбор каналов, очистка текста."""
from __future__ import annotations

import json

import pytest

import routing


# ---------- Разбор маршрутов ----------

def test_routes_are_channel_to_tags() -> None:
    routes = routing.parse_routes({
        "@channel1": ["политика", "срочное"],
        "@channel2": ["политика"],
    })
    assert routes["@channel1"] == ["политика", "срочное"]
    assert routes["@channel2"] == ["политика"]


def test_routes_accept_json_string() -> None:
    routes = routing.parse_routes('{"@a": ["политика"], "@b": "спорт, срочное"}')
    assert routes["@a"] == ["политика"]
    assert routes["@b"] == ["спорт", "срочное"]


def test_routes_accept_legacy_category_to_channel() -> None:
    """Старые настройки «категория → канал» не должны ломать работу."""
    routes = routing.parse_routes({"политика": "@channel1"})
    assert routes == {"@channel1": ["политика"]}


def test_routes_without_tags_are_dropped() -> None:
    routes = routing.parse_routes({"@a": [], "@b": ["политика"], "@c": ""})
    assert routes == {"@b": ["политика"]}


def test_broken_routes_are_reported() -> None:
    with pytest.raises(ValueError):
        routing.parse_routes("не json")


# ---------- Подбор каналов ----------

def test_post_goes_to_every_channel_with_matching_tag() -> None:
    routes = {
        "@a": ["политика", "срочное"],
        "@b": ["политика"],
        "@c": ["спорт"],
    }
    assert routing.resolve_channels(routes, ["политика"]) == ["@a", "@b"]


def test_channel_with_several_tags_receives_posts_by_any_of_them() -> None:
    routes = {"@a": ["политика", "срочное"]}
    assert routing.resolve_channels(routes, ["срочное"]) == ["@a"]
    assert routing.resolve_channels(routes, ["политика"]) == ["@a"]


def test_post_without_matching_tags_goes_to_fallback_channel() -> None:
    routes = {"@a": ["политика"], "@fallback": ["общее"]}
    assert routing.resolve_channels(routes, ["спорт"]) == ["@fallback"]


def test_post_without_tags_goes_to_fallback_channel() -> None:
    routes = {"@a": ["политика"], "@fallback": ["общее"]}
    assert routing.resolve_channels(routes, []) == ["@fallback"]


def test_star_is_also_a_fallback_tag() -> None:
    routes = {"@a": ["политика"], "@fallback": ["*"]}
    assert routing.resolve_channels(routes, ["спорт"]) == ["@fallback"]


def test_without_fallback_post_stays_unrouted() -> None:
    routes = {"@a": ["политика"]}
    assert routing.resolve_channels(routes, ["спорт"]) == []


def test_tag_matching_ignores_case_and_hash() -> None:
    routes = {"@a": ["Политика"]}
    assert routing.resolve_channels(routes, ["#ПОЛИТИКА"]) == ["@a"]


def test_channel_order_follows_settings() -> None:
    routes = {"@z": ["политика"], "@a": ["политика"]}
    assert routing.resolve_channels(routes, ["политика"]) == ["@z", "@a"]


# ---------- Хештеги в тексте ----------

def test_hashtags_are_extracted() -> None:
    assert routing.extract_tags("Новость про #Политику и #Срочное") == [
        "политику",
        "срочное",
    ]


def test_hashtags_are_stripped_from_text() -> None:
    cleaned = routing.strip_hashtags("Новость про #политику.\n\nВторая строка #тег")
    assert "#" not in cleaned
    assert "Новость про ." not in cleaned
    assert "Вторая строка" in cleaned


def test_stripping_keeps_plain_text_untouched() -> None:
    text = "Обычный текст без тегов.\nВторая строка."
    assert routing.strip_hashtags(text) == text


def test_hash_without_word_is_not_a_tag() -> None:
    """C# — это не хештег."""
    assert routing.extract_tags("Пишем на C# и F#") == []
    assert routing.strip_hashtags("Пишем на C# и F#") == "Пишем на C# и F#"


def test_tags_list_is_parsed_from_text_forms() -> None:
    assert routing.parse_tag_list("#политика, #срочное") == ["политика", "срочное"]
    assert routing.parse_tag_list("политика срочное") == ["политика", "срочное"]
    assert routing.parse_tag_list('["политика", "срочное"]') == ["политика", "срочное"]
    assert routing.parse_tag_list(["#политика"]) == ["политика"]


def test_all_tags_lists_every_tag_of_routes() -> None:
    routes = {"@a": ["политика", "срочное"], "@b": ["политика"]}
    assert routing.all_tags(routes) == ["политика", "срочное"]


def test_routes_round_trip_through_json() -> None:
    routes = {"@a": ["политика"], "@b": ["спорт", "срочное"]}
    encoded = routing.routes_to_json(routes)
    assert routing.parse_routes(encoded) == routes
    assert json.loads(encoded)["@b"] == ["спорт", "срочное"]
