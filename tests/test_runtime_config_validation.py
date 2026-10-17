"""Protect runtime configuration from unsafe or malformed saved values."""
from __future__ import annotations

import pytest

import main
import Site


class FakeDB:
    def __init__(self, **settings) -> None:
        self.settings = settings

    def get_setting(self, key: str, default=None):
        return self.settings.get(key, default)


def test_invalid_persisted_ai_mode_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(main, "db", FakeDB(ai_mode="REMTOE"))

    with pytest.raises(ValueError, match="ai_mode"):
        main._load_config()


@pytest.mark.parametrize("port", ["abc", "0", "65536", "-1", "１２３４"])
def test_invalid_local_ai_port_is_rejected(monkeypatch, port: str) -> None:
    monkeypatch.setattr(main, "db", FakeDB(local_port=port))

    with pytest.raises(ValueError, match="local_port"):
        main._load_config()


def test_ipv6_local_ai_host_builds_valid_bracketed_url(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "db",
        FakeDB(local_host="::1", local_port="1234", ai_mode="LOCAL"),
    )

    cfg = main._load_config()

    assert cfg["local_url"] == "http://[::1]:1234/v1/chat/completions"


def test_empty_local_ai_host_uses_safe_default(monkeypatch) -> None:
    monkeypatch.setattr(main, "db", FakeDB(local_host="", ai_mode="LOCAL"))

    cfg = main._load_config()

    assert cfg["local_url"] == "http://127.0.0.1:1234/v1/chat/completions"

@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("", "127.0.0.1"),
        ("localhost", "localhost"),
        ("127.0.0.1", "127.0.0.1"),
        ("::1", "::1"),
        ("[::1]", "[::1]"),
    ],
)
def test_local_host_accepts_hosts_and_ipv6(host: str, expected: str) -> None:
    assert Site._normalize_validated_setting("local_host", host) == expected


@pytest.mark.parametrize(
    "host",
    ["http://localhost", "localhost/path", "bad host", "::not-ipv6", "[127.0.0.1]"],
)
def test_local_host_rejects_url_syntax_and_malformed_addresses(host: str) -> None:
    import pytest
    import Site

    with pytest.raises(Site.SettingsValidationError) as exc:
        Site._normalize_validated_setting("local_host", host)
    assert exc.value.field == "local_host"


@pytest.mark.parametrize(
    "url",
    ["ftp://example.com/api", "http://", "https://example.com:99999/chat", "not-a-url"],
)
def test_remote_url_rejects_invalid_or_non_http_endpoints(url: str) -> None:
    import Site

    with pytest.raises(Site.SettingsValidationError) as exc:
        Site._normalize_validated_setting("remote_url", url)
    assert exc.value.field == "remote_url"


def test_remote_url_accepts_http_and_https_endpoints() -> None:
    import Site

    assert Site._normalize_validated_setting(
        "remote_url", "https://api.example.com/v1/chat/completions"
    ) == "https://api.example.com/v1/chat/completions"


def test_empty_local_ai_port_uses_safe_default(monkeypatch) -> None:
    monkeypatch.setattr(main, "db", FakeDB(local_port="", ai_mode="LOCAL"))

    cfg = main._load_config()

    assert cfg["local_url"] == "http://127.0.0.1:1234/v1/chat/completions"

@pytest.mark.parametrize("raw", ["not-json", "[]", "null", "42"])
def test_corrupt_persisted_topic_routing_fails_closed(monkeypatch, raw: str) -> None:
    monkeypatch.setattr(main, "db", FakeDB(topic_channels=raw, ai_mode="LOCAL"))

    with pytest.raises(ValueError, match="topic_channels"):
        main._load_config()

def test_load_config_reads_one_consistent_settings_snapshot(monkeypatch) -> None:
    class SnapshotDB:
        def get_all_settings(self) -> dict[str, str]:
            return {
                "ai_mode": "LOCAL",
                "local_host": "127.0.0.1",
                "local_port": "1234",
                "topic_channels": '{"news":"@target"}',
                "sources": "@source",
                "local_model": "snapshot-model",
            }

        def get_setting(self, *_args, **_kwargs):
            raise AssertionError("live settings must not be read field-by-field")

    monkeypatch.setattr(main, "db", SnapshotDB())

    cfg = main._load_config()

    assert cfg["ai_mode"] == "LOCAL"
    assert cfg["local_model"] == "snapshot-model"
    assert cfg["topic_channels"] == {"news": "@target"}
    assert cfg["sources"] == ["@source"]

def test_source_links_are_normalized_and_duplicate_sources_removed(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "db",
        FakeDB(
            sources="https://t.me/News_Channel/123\n@NEWS_CHANNEL\nNews_Channel\n-100123456789",
            ai_mode="LOCAL",
        ),
    )

    cfg = main._load_config()

    assert cfg["sources"] == ["@news_channel", "-100123456789"]


def test_source_invite_link_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "db",
        FakeDB(sources="https://t.me/+privateinvite", ai_mode="LOCAL"),
    )

    with pytest.raises(ValueError, match="источник Telegram"):
        main._load_config()

@pytest.mark.parametrize("poll_delay", ["0", "4", "3601", "-1", "１２３", "not-a-number"])
def test_invalid_persisted_poll_delay_is_rejected(monkeypatch, poll_delay: str) -> None:
    monkeypatch.setattr(main, "db", FakeDB(poll_delay=poll_delay))

    with pytest.raises(ValueError, match="poll_delay"):
        main._load_config()


@pytest.mark.parametrize("poll_delay", ["5", "60", "3600"])
def test_valid_persisted_poll_delay_is_kept(monkeypatch, poll_delay: str) -> None:
    monkeypatch.setattr(main, "db", FakeDB(poll_delay=poll_delay))

    assert main._load_config()["poll_delay"] == int(poll_delay)


def test_empty_legacy_poll_delay_uses_default(monkeypatch) -> None:
    monkeypatch.setattr(main, "db", FakeDB(poll_delay=""))

    assert main._load_config()["poll_delay"] == main.POLL_DELAY_DEFAULT
