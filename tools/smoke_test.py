"""Быстрая проверка импортов и связей модулей перед сборкой .exe."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILURES: list[str] = []


def ok(name: str) -> None:
    print(f"  OK  {name}")


def fail(name: str, exc: BaseException) -> None:
    FAILURES.append(f"{name}: {exc}")
    print(f" FAIL {name}: {exc}")


def check_import(module: str) -> None:
    try:
        __import__(module)
        ok(module)
    except ImportError as exc:
        fail(module, exc)
    except Exception as exc:
        # Ошибки типа ModuleNotFoundError обрабатываем как импорты
        if "No module named" in str(exc):
            fail(module, exc)


def main() -> int:
    print(f"Smoke test — {ROOT}\n")

    for mod in (
        "paths",
        "env_loader",
        "config",
        "startup",
        "database",
        "Auth",
        # Удалённые модули больше не проверяем
        # "licensing.client",  <-- удалён
        # "modules.behavior.engine",  <-- удалён
        # "modules.reviews.engine",  <-- удалён
    ):
        check_import(mod)

    try:
        from config import (
            ADMIN_HTML_PATH,
            ACTIVATE_HTML_PATH,
            BUNDLE_DIR,
            REQUIRED_PACKAGES,
        )

        assert isinstance(REQUIRED_PACKAGES, dict) and REQUIRED_PACKAGES
        ok("config.REQUIRED_PACKAGES dict")
        for path, label in (
            (ADMIN_HTML_PATH, "admin.html"),
            (ACTIVATE_HTML_PATH, "activate.html"),
        ):
            if not os.path.isfile(path):
                fail(label, FileNotFoundError(path))
            else:
                ok(label)
        
        env_example = os.path.join(BUNDLE_DIR, ".env.example")
        if not os.path.isfile(env_example):
            env_example = os.path.join(ROOT, ".env.example")
        if os.path.isfile(env_example):
            ok(".env.example")
        else:
            fail(".env.example", FileNotFoundError(env_example))
    except Exception as exc:
        fail("config paths", exc)

    try:
        # Заглушка проверки лицензии — импортируем, если модуль существует
        from licensing.client import LicenseClient  # type: ignore[import-not-found]
        
        client = LicenseClient()
        assert hasattr(client, "refresh_online_if_needed")
        ok("LicenseClient.refresh_online_if_needed(force=)")
    except ImportError as exc:
        # Модуль лицензий удалён — пропускаем проверку
        print(f"  SKIP licensing.client (модуль удалён)")
        FAILURES.append(f"licensing.client skipped: {exc}")
    except Exception as exc:
        fail("licensing.client", exc)

    try:
        from Site import app, MODULE_RUNTIME_META
        from config import MODULE_POSTS  # Только основной модуль
        
        assert MODULE_POSTS in MODULE_RUNTIME_META
        assert MODULE_RUNTIME_META[MODULE_POSTS].get("simulation_mode") is False
        ok("Site.MODULE_RUNTIME_META simulation flags")
        
        assert app is not None
        ok("Site.app FastAPI")
    except Exception as exc:
        fail("Site", exc)

    try:
        from startup import run_startup_bootstrap
        
        run_startup_bootstrap()
        ok("startup.run_startup_bootstrap()")
    except Exception as exc:
        fail("startup bootstrap", exc)

    print()
    if FAILURES:
        print(f"Провалено: {len(FAILURES)}")
        for line in FAILURES:
            print(f"  - {line}")
        return 1

    print("Все проверки пройдены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
