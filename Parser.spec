# -*- mode: python ; coding: utf-8 -*-
"""
.spec для PyInstaller — Content Factory (Parser.exe)

Сборка:
    pyinstaller Parser.spec --clean

Выход:
    dist/Parser.exe — единый исполняемый файл с UI-админкой
    build/ — промежуточные артефакты
"""
import os
from pathlib import Path

# Корень проекта = папка, где лежит этот .spec
PROJECT_DIR = Path(SPECPATH).resolve()

# === Точка входа ===
ENTRY = str(PROJECT_DIR / "main.py")

# === Дополнительные исходники проекта (для явного включения в сборку) ===
# Перечисляются в hiddenimports ниже: тест tests/test_packaging_assets.py
# проверяет, что список полон и новый модуль нельзя забыть добавить.

# === Runtime resources bundled into the one-file executable ===
# env_loader.py searches BASE_DIR first, then BUNDLE_DIR for .env.example.
# Site.py serves the logo and favicon from BUNDLE_DIR (then BASE_DIR).
# Keep resources at the bundle root to match config.py and Site.py paths.
BUNDLE_ASSETS = (
    "admin.html",
    ".env.example",
    "favicon.png",
    "brand-logo.webp",
    "brand-logo.png",
    "brand-logo@2x.webp",
    "brand-logo@2x.png",
)
datas = [(str(PROJECT_DIR / filename), ".") for filename in BUNDLE_ASSETS]

# === Hidden imports (то, что PyInstaller не находит статическим анализом) ===
hiddenimports = [
    # aiohttp-сессии для aiogram + socks
    "aiohttp_socks",
    "aiohttp_socks.connector",
    # Telethon
    "telethon",
    "telethon.extensions",
    "telethon.crypto",
    "telethon.errors",
    "telethon.events",
    "telethon.tl",
    "telethon.tl.custom",
    "telethon.tl.custom.message",
    "telethon.utils",
    # aiogram
    "aiogram",
    "aiogram.client",
    "aiogram.client.default",
    "aiogram.client.session",
    "aiogram.client.session.aiohttp",
    "aiogram.dispatcher",
    "aiogram.dispatcher.event",
    "aiogram.dispatcher.middlewares",
    "aiogram.enums",
    "aiogram.filters",
    "aiogram.fsm",
    "aiogram.fsm.context",
    "aiogram.fsm.state",
    "aiogram.handlers",
    "aiogram.methods",
    "aiogram.types",
    # aiohttp
    "aiohttp",
    "aiohttp.client",
    "aiohttp.connector",
    "aiohttp.web",
    # Pydantic / fastapi
    "annotated_doc",
    "anyio",
    "anyio._backends",
    "anyio._backends._asyncio",
    "fastapi",
    "fastapi.encoders",
    "fastapi.exceptions",
    "fastapi.openapi",
    "fastapi.params",
    "fastapi.responses",
    "fastapi.routing",
    "fastapi.utils",
    "starlette",
    "starlette.applications",
    "starlette.middleware",
    "starlette.requests",
    "starlette.responses",
    "starlette.routing",
    # Прочее
    "click",
    "h11",
    "httpcore",
    "httpx",
    "pydantic",
    "pydantic.fields",
    "pydantic.main",
    "pydantic.networks",
    "pydantic.types",
    "pydantic_core",
    "dotenv",
    "rich",
    "sniffio",
    "typing_extensions",
    "uvicorn",
    "uvicorn.loops",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.logging",
    "websockets",
    # Внутренние модули: настройка кодировки консоли (кириллица в Windows)
    "console_setup",
    "media_convert",
    "media_marker",
    # Модули самого проекта: перечислены явно, чтобы новый модуль нельзя
    # было забыть добавить (проверяет tests/test_packaging_assets.py).
    "api_security",
    "config",
    "database",
    "deps",
    "env_loader",
    "main",
    "paths",
    "proxy_check",
    "routing",
    "secrets_store",
    "Site",
    "single_instance",
    "startup",
    "state",
    "text_format",
]

# === Исключения (то, что НЕ надо тащить) ===
excludes = [
    "tkinter",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "matplotlib",
    "numpy.tests",
    "pandas",
    "IPython",
    "notebook",
    "pytest",
    "sphinx",
    "mypy",
    "hypothesis",
]

# === Анализ ===
a = Analysis(
    [ENTRY],
    pathex=[str(PROJECT_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

# === Сборка PYZ (скомпилированный Python в одном архиве) ===
pyz = PYZ(a.pure)

# === EXE (один файл) ===
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="Parser",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                  # сжатие UPX (если установлен; иначе просто пропустит)
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,              # True = консольное окно (видно логи); False = без окна
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon=str(PROJECT_DIR / "icon.ico"),  # раскомментируй, если есть иконка
)

