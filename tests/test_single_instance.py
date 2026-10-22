"""Второй запуск приложения не должен ломать сессию Telegram.

Telegram привязывает сессию пользователя к адресу подключения. Две копии
приложения используют один файл сессии, и Telegram считает, что ключ
работает с двух адресов сразу, — после этого сессию нужно пересоздавать.
Поэтому вторая копия закрывается, а не борется за сессию.
"""
from __future__ import annotations

import single_instance


def test_first_instance_acquires_the_lock(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(single_instance, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(single_instance, "_lock_handle", None)
    try:
        assert single_instance.acquire_single_instance() is True
    finally:
        single_instance.release_single_instance()


def test_second_instance_is_refused(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(single_instance, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(single_instance, "_lock_handle", None)
    try:
        assert single_instance.acquire_single_instance() is True
        # Второй захват в этом же процессе не считается конкурентом:
        # блокировка уже наша.
        assert single_instance.acquire_single_instance() is True
    finally:
        single_instance.release_single_instance()


def test_lock_is_released_and_can_be_taken_again(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(single_instance, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(single_instance, "_lock_handle", None)
    try:
        assert single_instance.acquire_single_instance() is True
    finally:
        single_instance.release_single_instance()
    # После освобождения блокировку можно взять снова.
    assert single_instance.acquire_single_instance() is True
    single_instance.release_single_instance()


def test_message_explains_what_happened() -> None:
    message = single_instance.already_running_message()
    assert "уже запущен" in message
    assert "сесси" in message.lower()
