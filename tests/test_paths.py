"""Проверки расположения пользовательских данных и ресурсов приложения."""
from __future__ import annotations

import sys
from pathlib import Path

import paths


def test_windows_frozen_uses_exe_folder(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    fake_exe = tmp_path / "Parser.exe"
    fake_exe.write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(fake_exe))

    assert paths.app_data_dir() == str(tmp_path)


def test_source_run_uses_project_folder(monkeypatch) -> None:
    monkeypatch.setattr(paths, "is_frozen", lambda: False)

    data_dir = paths.app_data_dir()

    assert Path(data_dir).is_dir()
    assert (Path(data_dir) / "paths.py").is_file()


def test_frozen_bundle_uses_pyinstaller_resource_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert paths.bundle_dir() == str(tmp_path)


def test_frozen_bundle_falls_back_to_app_data_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(paths, "app_data_dir", lambda: str(tmp_path))

    assert paths.bundle_dir() == str(tmp_path)
