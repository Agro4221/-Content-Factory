"""Проверка и авто-установка зависимостей проекта в локальный .venv."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

from config import BASE_DIR, REQUIRED_PACKAGES
from paths import is_frozen

REQUIREMENTS_FILE = os.path.join(BASE_DIR, "requirements.txt")
VENV_DIR = os.path.join(BASE_DIR, ".venv")


def venv_python_path() -> str:
    if sys.platform == "win32":
        return os.path.join(VENV_DIR, "Scripts", "python.exe")
    return os.path.join(VENV_DIR, "bin", "python")


def find_missing_packages() -> list[str]:
    """Возвращает pip-имена пакетов, которых нет в текущем интерпретаторе."""
    missing: list[str] = []
    seen: set[str] = set()
    for mod, pip_name in REQUIRED_PACKAGES.items():
        if importlib.util.find_spec(mod) is None and pip_name not in seen:
            missing.append(pip_name)
            seen.add(pip_name)
    return missing


def _import_check_script() -> str:
    lines = [f"import {mod}" for mod in REQUIRED_PACKAGES]
    return "; ".join(lines)


def _pip_install(python: str, packages: list[str], *, use_requirements: bool = False) -> None:
    cmd = [python, "-m", "pip", "install", "--upgrade"]
    if use_requirements and os.path.isfile(REQUIREMENTS_FILE):
        cmd.extend(["-r", REQUIREMENTS_FILE])
    else:
        cmd.extend(packages)
    subprocess.check_call(cmd)


def ensure_venv() -> str:
    python = venv_python_path()
    if not os.path.isfile(python):
        subprocess.check_call([sys.executable, "-m", "venv", VENV_DIR])
    return python


def _packages_ok(python: str) -> bool:
    try:
        subprocess.check_call(
            [python, "-c", _import_check_script()],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except (subprocess.CalledProcessError, OSError):
        return False


def try_reexec_via_venv() -> bool:
    """Если .venv уже содержит зависимости — перезапускаем процесс через него."""
    venv_python = venv_python_path()
    if not os.path.isfile(venv_python):
        return False
    if os.path.realpath(sys.executable) == os.path.realpath(venv_python):
        return False
    if not _packages_ok(venv_python):
        return False
    print("--> [СИСТЕМА] Перезапуск через локальное окружение .venv ...")
    os.execv(venv_python, [venv_python, *sys.argv])


def check_and_install_dependencies() -> None:
    """
    Если не хватает библиотек — устанавливает их через pip в текущее окружение
    или создаёт .venv рядом с проектом и перезапускает процесс.
    """
    if is_frozen():
        missing = find_missing_packages()
        if missing:
            print(
                "!!! [ОШИБКА] В сборке .exe не хватает модулей: "
                + ", ".join(missing)
            )
            sys.exit(1)
        return

    missing = find_missing_packages()
    if not missing:
        _reload_env_after_deps()
        return

    if try_reexec_via_venv():
        return

    print(f"--> [СИСТЕМА] Не хватает библиотек: {', '.join(missing)}")
    print("--> [СИСТЕМА] Устанавливаю через pip...")
    try:
        _pip_install(sys.executable, missing)
        if not _packages_ok(sys.executable):
            raise subprocess.CalledProcessError(1, "pip")
        print("--> [СИСТЕМА] Готово.\n")
        _reload_env_after_deps()
        return
    except subprocess.CalledProcessError:
        pass

    print("--> [СИСТЕМА] Системный pip недоступен (PEP 668). Создаю .venv рядом с приложением...")
    try:
        venv_python = ensure_venv()
        _pip_install(venv_python, missing, use_requirements=True)
        if not _packages_ok(venv_python):
            _pip_install(venv_python, missing)
        print("--> [СИСТЕМА] Зависимости установлены в .venv. Перезапуск...")
        os.execv(venv_python, [venv_python, *sys.argv])
    except Exception as exc:
        print(f"!!! [ОШИБКА] Установка не удалась: {exc}")
        print("!!! Создайте окружение вручную: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt")
        sys.exit(1)


def _reload_env_after_deps() -> None:
    """Повторно читает .env после установки python-dotenv."""
    try:
        from env_loader import load_environment

        load_environment()
    except Exception:
        pass


ensure_dependencies = check_and_install_dependencies
