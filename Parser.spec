# -*- mode: python ; coding: utf-8 -*-
"""
.spec для PyInstaller — Project MaryJane (Parser.exe)

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
PROJECT_MODULES = [
    "config",
    "Auth",
    "Site",
    "database",
    "state",
]

# === Data-файлы (положить рядом с .exe) ===
# admin.html кладётся в корень bundle — SITE根相对路径
datas = [
    (str(PROJECT_DIR / "admin.html"), "."),
]

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
    "pydantic_settings",
    "python_dotenv",
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

# Если хочешь .app для macOS или onedir-сборку — раскомментируй ниже:
# coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="Parser")
