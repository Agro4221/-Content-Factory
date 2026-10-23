"""Guard startup logging against Windows console encoding failures."""
from __future__ import annotations

import io
import sys

import env_loader


def test_legacy_windows_code_page_cannot_crash_russian_startup_logs(monkeypatch) -> None:
    stdout_bytes = io.BytesIO()
    stderr_bytes = io.BytesIO()
    stdout = io.TextIOWrapper(stdout_bytes, encoding="cp1252", errors="strict")
    stderr = io.TextIOWrapper(stderr_bytes, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    env_loader._configure_stdio_errors()
    print("--> [СИСТЕМА] Создан файл .env", file=stdout)
    stdout.flush()

    # Поток переведён в UTF-8, поэтому кириллица пишется как есть,
    # а не как \uXXXX-escape-последовательности.
    assert stdout.errors == "replace"
    written = stdout_bytes.getvalue()
    assert written.decode("utf-8").strip() == "--> [СИСТЕМА] Создан файл .env"


def test_symbols_outside_target_encoding_do_not_crash(monkeypatch) -> None:
    """Символ, которого нет в целевой кодировке, не должен ронять вывод."""
    stdout_bytes = io.BytesIO()
    stdout = io.TextIOWrapper(stdout_bytes, encoding="ascii", errors="strict")
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stdout)

    env_loader._configure_stdio_errors()
    print("--> [СИСТЕМА] Проверка", file=stdout)
    stdout.flush()

    assert stdout_bytes.getvalue()  # вывод не пустой и исключения не было


def test_streams_without_reconfigure_are_safe(monkeypatch) -> None:
    class EmbeddedStream:
        pass

    monkeypatch.setattr(sys, "stdout", EmbeddedStream())
    monkeypatch.setattr(sys, "stderr", None)

    env_loader._configure_stdio_errors()



def test_closed_embedded_stream_does_not_abort_startup(monkeypatch) -> None:
    class ClosedStream:
        @staticmethod
        def reconfigure(**_kwargs) -> None:
            raise RuntimeError("stream is closed")

    monkeypatch.setattr(sys, "stdout", ClosedStream())
    monkeypatch.setattr(sys, "stderr", ClosedStream())

    env_loader._configure_stdio_errors()
