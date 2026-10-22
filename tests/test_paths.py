"""Пути приложения: где лежат данные и ресурсы.

Главное — на macOS данные не должны попадать внутрь .app: бандл часто
лежит в /Applications, куда писать нельзя, и при обновлении приложения
всё внутри него пропало бы.
"""
from __future__ import annotations

import sys
from pathlib import Path

import paths


def test_macos_data_dir_is_in_application_support(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(paths.os.path, "expanduser", lambda p: str(tmp_path))
    monkeypatch.setattr(paths.os, "makedirs", lambda *a, **k: None)

    data_dir = paths._macos_data_dir()

    assert "Library" in data_dir
    assert "Application Support" in data_dir
    assert data_dir.endswith(paths.MACOS_APP_SUPPORT_NAME)


def test_macos_data_dir_is_not_inside_the_app_bundle(monkeypatch, tmp_path) -> None:
    """Каталог данных не должен совпадать с каталогом .app."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(paths.os.path, "expanduser", lambda p: str(tmp_path))
    monkeypatch.setattr(paths.os, "makedirs", lambda *a, **k: None)

    data_dir = paths.app_data_dir()

    assert str(tmp_path) in data_dir
    assert "Contents" not in data_dir


def test_windows_frozen_uses_exe_folder(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    fake_exe = tmp_path / "Parser.exe"
    fake_exe.write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(fake_exe))

    assert paths.app_data_dir() == str(tmp_path)


def test_source_run_uses_project_folder(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(paths, "is_frozen", lambda: False)

    data_dir = paths.app_data_dir()

    assert Path(data_dir).name == "ContentFactory-updated"
    assert Path(data_dir).is_dir()
