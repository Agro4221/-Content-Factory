"""Безопасное хранение секретов: шифрование (DPAPI), обратная совместимость,
отсутствие утечек в журнал и в API.
"""
from __future__ import annotations

import pytest

import secrets_store
from config import SECRET_MASK, SECRET_SETTING_KEYS
from database import DB


def make_db(tmp_path) -> DB:
    return DB(str(tmp_path / "test.db"))


def raw_value(db: DB, key: str) -> str:
    """Значение ровно так, как оно лежит в файле базы (без расшифровки)."""
    cur = db.conn.execute("SELECT value FROM bot_settings WHERE key=?", (key,))
    row = cur.fetchone()
    return row[0] if row else ""


# ---------- Модуль DPAPI ----------

def test_dpapi_round_trip_preserves_value() -> None:
    if not secrets_store.supported():
        pytest.skip("DPAPI доступен только в Windows")
    secret = "123456789:AAExampleTokenValue"
    encrypted = secrets_store.encrypt(secret)
    assert secrets_store.is_encrypted(encrypted)
    assert secret not in encrypted
    assert secrets_store.decrypt(encrypted) == secret


def test_dpapi_keeps_legacy_plaintext_untouched() -> None:
    assert secrets_store.decrypt("legacy-plaintext") == "legacy-plaintext"
    assert not secrets_store.is_encrypted("legacy-plaintext")


def test_dpapi_rejects_corrupted_payload() -> None:
    with pytest.raises(secrets_store.SecretDecryptionError):
        secrets_store.decrypt(secrets_store.PREFIX + "bm90LXZhbGlk")


# ---------- Запись в базу ----------

def test_secret_is_stored_encrypted_and_read_back_decrypted(tmp_path) -> None:
    if not secrets_store.supported():
        pytest.skip("DPAPI доступен только в Windows")
    db = make_db(tmp_path)
    try:
        token = "123456:AAExampleTokenValue"
        db.set_setting("bot_token", token)

        stored = raw_value(db, "bot_token")
        assert stored != token
        assert secrets_store.is_encrypted(stored)
        assert token not in stored

        # Приложение читает значение как раньше — без ручного повторного ввода.
        assert db.get_setting("bot_token") == token
        assert db.get_all_settings()["bot_token"] == token
    finally:
        db.conn.close()


def test_non_secret_settings_stay_readable(tmp_path) -> None:
    db = make_db(tmp_path)
    try:
        db.set_setting("sources", "@channel_one\n@channel_two")
        assert raw_value(db, "sources") == "@channel_one\n@channel_two"
        assert db.get_setting("sources") == "@channel_one\n@channel_two"
    finally:
        db.conn.close()


def test_batch_save_encrypts_secrets(tmp_path) -> None:
    if not secrets_store.supported():
        pytest.skip("DPAPI доступен только в Windows")
    db = make_db(tmp_path)
    try:
        db.set_settings(
            {
                "bot_token": "123456:AAExampleTokenValue",
                "remote_api_key": "sk-example-key-value",
                "poll_delay": "90",
            }
        )
        assert secrets_store.is_encrypted(raw_value(db, "bot_token"))
        assert secrets_store.is_encrypted(raw_value(db, "remote_api_key"))
        assert raw_value(db, "poll_delay") == "90"
        assert db.get_setting("bot_token") == "123456:AAExampleTokenValue"
        assert db.get_setting("remote_api_key") == "sk-example-key-value"
    finally:
        db.conn.close()


# ---------- Обратная совместимость и миграция ----------

def test_legacy_plaintext_secret_is_readable_and_migrated(tmp_path) -> None:
    """База, созданная до включения шифрования, продолжает работать."""
    if not secrets_store.supported():
        pytest.skip("DPAPI доступен только в Windows")
    db = make_db(tmp_path)
    try:
        # Имитируем состояние до миграции: секрет лежит открытым текстом.
        db.conn.execute(
            "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
            ("bot_token", "123456:AAExampleTokenValue"),
        )
        assert raw_value(db, "bot_token") == "123456:AAExampleTokenValue"

        # Значение читается как раньше.
        assert db.get_setting("bot_token") == "123456:AAExampleTokenValue"

        # Миграция переводит его в зашифрованный вид.
        migrated = db.migrate_plaintext_secrets()
        assert migrated == 1
        assert secrets_store.is_encrypted(raw_value(db, "bot_token"))
        assert db.get_setting("bot_token") == "123456:AAExampleTokenValue"
    finally:
        db.conn.close()


def test_migration_is_idempotent(tmp_path) -> None:
    if not secrets_store.supported():
        pytest.skip("DPAPI доступен только в Windows")
    db = make_db(tmp_path)
    try:
        db.set_setting("bot_token", "123456:AAExampleTokenValue")
        assert db.migrate_plaintext_secrets() == 0
        assert db.get_setting("bot_token") == "123456:AAExampleTokenValue"
    finally:
        db.conn.close()


def test_migration_purges_plaintext_from_database_file(tmp_path) -> None:
    """После миграции открытого текста не остаётся даже в свободных страницах файла.

    SQLite не обнуляет освобождённые страницы, поэтому после перезаписи
    секрета его открытое значение могло бы остаться в файле. Миграция
    выполняет VACUUM, чтобы этого не произошло.
    """
    if not secrets_store.supported():
        pytest.skip("DPAPI доступен только в Windows")
    path = tmp_path / "purge.db"
    db = DB(str(path))
    secret = "123456:AAExampleTokenValue"
    try:
        db.conn.execute(
            "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
            ("bot_token", secret),
        )
    finally:
        db.conn.close()

    with open(path, "rb") as fh:
        assert secret.encode() in fh.read()

    reopened = DB(str(path))
    try:
        assert reopened.get_setting("bot_token") == secret
    finally:
        reopened.conn.close()

    with open(path, "rb") as fh:
        assert secret.encode() not in fh.read()


def test_database_startup_migrates_existing_plaintext(tmp_path) -> None:
    """При открытии базы старые plaintext-секреты шифруются автоматически."""
    if not secrets_store.supported():
        pytest.skip("DPAPI доступен только в Windows")
    path = tmp_path / "legacy.db"

    legacy = DB(str(path))
    legacy.conn.execute(
        "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
        ("bot_token", "123456:AAExampleTokenValue"),
    )
    legacy.conn.close()

    reopened = DB(str(path))
    try:
        stored = reopened.conn.execute(
            "SELECT value FROM bot_settings WHERE key='bot_token'"
        ).fetchone()[0]
        assert secrets_store.is_encrypted(stored)
        assert reopened.get_setting("bot_token") == "123456:AAExampleTokenValue"
    finally:
        reopened.conn.close()


# ---------- Отсутствие утечек ----------

def test_secret_never_appears_in_journal(tmp_path) -> None:
    """В журнал попадает факт проблемы, но не значение секрета."""
    db = make_db(tmp_path)
    try:
        secret = "123456:AAExampleTokenValue"
        db.set_setting("bot_token", secret)
        db.add_log("ERR-202", "Неверный формат токена бота.")

        logs = db.get_logs(limit=100)
        joined = " ".join(message for _, _, message in logs)
        assert secret not in joined
    finally:
        db.conn.close()


def test_decryption_failure_is_logged_without_value(tmp_path, monkeypatch) -> None:
    """Нечитаемый блоб даёт пустое значение и запись в журнале без секрета."""
    db = make_db(tmp_path)
    try:
        db.set_setting("bot_token", "123456:AAExampleTokenValue")

        def broken(_value: str, _account: str = "") -> str:
            raise secrets_store.SecretDecryptionError("CryptUnprotectData failed")

        monkeypatch.setattr("database._decrypt_secret", broken)
        assert db.get_setting("bot_token") == ""

        logs = db.get_logs(limit=100)
        assert any("bot_token" in message for _, _, message in logs)
        assert all("123456:AAExampleTokenValue" not in message for _, _, message in logs)
    finally:
        db.conn.close()


def test_masked_settings_never_expose_secret_values(tmp_path) -> None:
    db = make_db(tmp_path)
    try:
        db.set_setting("bot_token", "123456:AAExampleTokenValue")
        db.set_setting("remote_api_key", "sk-example-key-value")

        masked = db.get_masked_settings()
        assert masked["bot_token"] == SECRET_MASK
        assert masked["remote_api_key"] == SECRET_MASK
        assert "123456:AAExampleTokenValue" not in str(masked)
        assert "sk-example-key-value" not in str(masked)
    finally:
        db.conn.close()


def test_masked_settings_keep_empty_secret_empty(tmp_path) -> None:
    db = make_db(tmp_path)
    try:
        masked = db.get_masked_settings()
        assert masked.get("bot_token", "") == ""
    finally:
        db.conn.close()


def test_secret_storage_is_supported_only_on_windows(monkeypatch) -> None:
    """Системное шифрование секретов включено только для целевой платформы."""
    monkeypatch.setattr(secrets_store.sys, "platform", "win32")
    assert secrets_store.supported() is True
    monkeypatch.setattr(secrets_store.sys, "platform", "linux")
    assert secrets_store.supported() is False


def test_every_secret_key_is_covered() -> None:
    """Список секретных настроек содержит все ключи, которые нельзя показывать."""
    for key in ("bot_token", "tg_api_hash", "remote_api_key", "proxy_pass", "mtproto_key"):
        assert key in SECRET_SETTING_KEYS
    # tg_api_id — идентификатор, а не секрет: его можно показывать.
    assert "tg_api_id" not in SECRET_SETTING_KEYS
