"""Версия приложения должна быть указана одинаково во всех местах.

config.APP_VERSION показывается в журнале при старте, VERSION.txt лежит рядом
с исходниками. Раньше в них стояли разные значения (1.29.0.0 и 1.29.0.3), и
пользователь видел в журнале устаревшую версию.
"""
from __future__ import annotations

import re
from pathlib import Path

import config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = PROJECT_ROOT / "VERSION.txt"
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(\.\d+)?$")


def test_version_file_matches_application_version() -> None:
    assert VERSION_FILE.is_file(), "Файл VERSION.txt обязателен в репозитории"
    recorded = VERSION_FILE.read_text(encoding="utf-8").strip()
    assert recorded == config.APP_VERSION, (
        f"VERSION.txt ({recorded}) и config.APP_VERSION ({config.APP_VERSION}) "
        "разошлись — версию надо поднять в обоих местах"
    )


def test_application_version_is_valid_semver() -> None:
    assert SEMVER_RE.match(config.APP_VERSION), (
        f"Версия {config.APP_VERSION!r} не соответствует semver"
    )
