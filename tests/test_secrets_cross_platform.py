"""Хранилище секретов: Windows DPAPI и macOS Keychain.

На macOS системного хранилища DPAPI нет, поэтому секреты должны лежать
в Keychain, а в базе — только ссылка на них. Иначе токен бота и ключ ИИ
лежали бы в файле базы открытым текстом.
"""
from __future__ import annotations

import base64
import subprocess

import pytest

import secrets_store


def test_supported_on_windows_and_macos(monkeypatch) -> None:
    monkeypatch.setattr(secrets_store.sys, "platform", "win32")
    assert secrets_store.supported() is True
    monkeypatch.setattr(secrets_store.sys, "platform", "darwin")
    assert secrets_store.supported() is True
    monkeypatch.setattr(secrets_store.sys, "platform", "linux")
    assert secrets_store.supported() is False


def test_plain_value_that_looks_encrypted_is_not_treated_as_encrypted() -> None:
    """Секрет, начинающийся с «dpapi:v1:», не должен считаться зашифрованным."""
    lookalike = "dpapi:v1:не-base64-текст"
    assert secrets_store.is_encrypted(lookalike) is False
    # Настоящее зашифрованное значение base64 — распознаётся.
    real = "dpapi:v1:" + base64.b64encode(b"payload").decode()
    assert secrets_store.is_encrypted(real) is True


def test_keychain_prefix_is_recognised() -> None:
    value = "keychain:v1:" + base64.b64encode(b"payload").decode()
    assert secrets_store.is_encrypted(value) is True


def test_macos_secret_goes_to_keychain_not_to_the_database(monkeypatch) -> None:
    """На macOS секрет кладётся в Keychain, в базу попадает только ссылка."""
    monkeypatch.setattr(secrets_store.sys, "platform", "darwin")
    stored: dict[str, str] = {}

    def fake_run(cmd, **_kwargs):
        if cmd[1] == "add-generic-password":
            account = cmd[cmd.index("-a") + 1]
            stored[account] = cmd[cmd.index("-w") + 1]
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, stored.get(cmd[cmd.index("-a") + 1], "") + "\n", "")

    monkeypatch.setattr(secrets_store.subprocess, "run", fake_run)

    encrypted = secrets_store.encrypt("my-bot-token", "bot_token")

    assert encrypted.startswith(secrets_store.KEYCHAIN_PREFIX)
    assert "my-bot-token" not in encrypted
    assert stored["bot_token"] == "my-bot-token"
    # Секрет читается обратно и сверяется с записью в Keychain.
    assert secrets_store.decrypt(encrypted, "bot_token") == "my-bot-token"


def test_macos_missing_keychain_entry_is_reported(monkeypatch) -> None:
    monkeypatch.setattr(secrets_store.sys, "platform", "darwin")

    def fake_run(cmd, **_kwargs):
        return subprocess.CompletedProcess(cmd, 44, "", "SecKeychainSearchCopyNext: not found")

    monkeypatch.setattr(secrets_store.subprocess, "run", fake_run)

    encrypted = "keychain:v1:" + base64.b64encode(b"value").decode()
    with pytest.raises(secrets_store.SecretDecryptionError):
        secrets_store.decrypt(encrypted, "bot_token")


def test_keychain_value_must_match_the_database_pointer(monkeypatch) -> None:
    """Если содержимое Keychain не совпадает с базой — это подмена."""
    monkeypatch.setattr(secrets_store.sys, "platform", "darwin")

    def fake_run(cmd, **_kwargs):
        return subprocess.CompletedProcess(cmd, 0, "other-value\n", "")

    monkeypatch.setattr(secrets_store.subprocess, "run", fake_run)

    encrypted = "keychain:v1:" + base64.b64encode(b"value").decode()
    with pytest.raises(secrets_store.SecretDecryptionError):
        secrets_store.decrypt(encrypted, "bot_token")


def test_keychain_secret_without_account_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(secrets_store.sys, "platform", "darwin")
    with pytest.raises(secrets_store.SecretEncryptionError):
        secrets_store.encrypt("value", "")


def test_windows_secret_keeps_working(monkeypatch) -> None:
    """На Windows поведение не изменилось: DPAPI и прежний префикс."""
    monkeypatch.setattr(secrets_store.sys, "platform", "win32")
    monkeypatch.setattr(
        secrets_store, "_protect", lambda data: b"blob:" + data
    )
    encrypted = secrets_store.encrypt("secret", "bot_token")
    assert encrypted.startswith(secrets_store.PREFIX)
    assert "secret" not in encrypted
