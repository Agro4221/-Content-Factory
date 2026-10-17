"""Guard startup logging against Windows console encoding failures."""
from __future__ import annotations

import io

import env_loader


def test_legacy_windows_code_page_cannot_crash_russian_startup_logs(monkeypatch) -> None:
    stdout_bytes = io.BytesIO()
    stderr_bytes = io.BytesIO()
    stdout = io.TextIOWrapper(stdout_bytes, encoding="cp1252", errors="strict")
    stderr = io.TextIOWrapper(stderr_bytes, encoding="cp1252", errors="strict")
    monkeypatch.setattr(env_loader.sys, "stdout", stdout)
    monkeypatch.setattr(env_loader.sys, "stderr", stderr)

    env_loader._configure_stdio_errors()
    print("--> [СИСТЕМА] Создан файл .env", file=stdout)
    stdout.flush()

    assert stdout.errors == "backslashreplace"
    assert b"\\u0421" in stdout_bytes.getvalue()


def test_streams_without_reconfigure_are_safe(monkeypatch) -> None:
    class EmbeddedStream:
        pass

    monkeypatch.setattr(env_loader.sys, "stdout", EmbeddedStream())
    monkeypatch.setattr(env_loader.sys, "stderr", None)

    env_loader._configure_stdio_errors()



def test_closed_embedded_stream_does_not_abort_startup(monkeypatch) -> None:
    class ClosedStream:
        @staticmethod
        def reconfigure(**_kwargs) -> None:
            raise RuntimeError("stream is closed")

    monkeypatch.setattr(env_loader.sys, "stdout", ClosedStream())
    monkeypatch.setattr(env_loader.sys, "stderr", ClosedStream())

    env_loader._configure_stdio_errors()
