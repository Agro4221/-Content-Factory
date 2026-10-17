"""Regression tests for restarting after auto-installing dependencies."""
from __future__ import annotations

import deps


def test_successful_dependency_install_restarts_after_reloading_env(monkeypatch) -> None:
    events: list[object] = []

    monkeypatch.setattr(deps, "is_frozen", lambda: False)
    monkeypatch.setattr(deps, "find_missing_packages", lambda: ["python-dotenv>=1.0"])
    monkeypatch.setattr(deps, "try_reexec_via_venv", lambda: False)
    monkeypatch.setattr(
        deps,
        "_pip_install",
        lambda python, packages, **kwargs: events.append(("install", python, packages)),
    )
    monkeypatch.setattr(deps, "_packages_ok", lambda python: True)
    monkeypatch.setattr(deps, "_reload_env_after_deps", lambda: events.append("reload-env"))
    monkeypatch.setattr(
        deps.os,
        "execv",
        lambda executable, args: events.append(("execv", executable, args)),
    )
    monkeypatch.setattr(deps.sys, "executable", "/python/current")
    monkeypatch.setattr(deps.sys, "argv", ["main.py", "--local"])

    deps.check_and_install_dependencies()

    assert events[0] == ("install", "/python/current", ["python-dotenv>=1.0"])
    assert events[1] == "reload-env"
    assert events[2] == ("execv", "/python/current", ["/python/current", "main.py", "--local"])


def test_existing_dependencies_do_not_force_restart(monkeypatch) -> None:
    events: list[str] = []

    monkeypatch.setattr(deps, "is_frozen", lambda: False)
    monkeypatch.setattr(deps, "find_missing_packages", lambda: [])
    monkeypatch.setattr(deps, "_reload_env_after_deps", lambda: events.append("reload-env"))
    monkeypatch.setattr(
        deps.os,
        "execv",
        lambda executable, args: events.append("execv"),
    )

    deps.check_and_install_dependencies()

    assert events == ["reload-env"]

def test_system_pip_oserror_falls_back_to_venv(monkeypatch) -> None:
    events: list[object] = []

    monkeypatch.setattr(deps, "is_frozen", lambda: False)
    monkeypatch.setattr(deps, "find_missing_packages", lambda: ["fastapi>=0.110"])
    monkeypatch.setattr(deps, "try_reexec_via_venv", lambda: False)
    monkeypatch.setattr(deps.sys, "executable", "/system/python")
    monkeypatch.setattr(deps.sys, "argv", ["main.py"])

    def fake_install(python, packages, *, use_requirements=False):
        events.append(("install", python, use_requirements))
        if python == "/system/python":
            raise OSError("system Python launcher is unavailable")

    monkeypatch.setattr(deps, "_pip_install", fake_install)
    monkeypatch.setattr(deps, "ensure_venv", lambda: "/project/.venv/bin/python")
    monkeypatch.setattr(deps, "_packages_ok", lambda _python: True)
    monkeypatch.setattr(deps, "_reload_env_after_deps", lambda: None)
    monkeypatch.setattr(
        deps.os,
        "execv",
        lambda executable, args: events.append(("execv", executable, args)),
    )

    deps.check_and_install_dependencies()

    assert events == [
        ("install", "/system/python", False),
        ("install", "/project/.venv/bin/python", True),
        ("execv", "/project/.venv/bin/python", ["/project/.venv/bin/python", "main.py"]),
    ]

def test_existing_venv_exec_oserror_falls_back_instead_of_crashing(monkeypatch) -> None:
    monkeypatch.setattr(deps, "venv_python_path", lambda: "/project/.venv/bin/python")
    monkeypatch.setattr(deps.os.path, "isfile", lambda _path: True)
    monkeypatch.setattr(deps.os.path, "realpath", lambda path: path)
    monkeypatch.setattr(deps, "_packages_ok", lambda _python: True)
    monkeypatch.setattr(deps.sys, "executable", "/system/python")
    monkeypatch.setattr(deps.sys, "argv", ["main.py"])

    def fail_exec(*_args) -> None:
        raise OSError("venv interpreter could not be started")

    monkeypatch.setattr(deps.os, "execv", fail_exec)

    assert deps.try_reexec_via_venv() is False

def test_old_aiogram_without_default_properties_is_marked_missing(monkeypatch) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(deps.importlib.util, "find_spec", lambda _mod: object())

    def fake_import_module(name: str):
        if name == "aiogram.client.default":
            return SimpleNamespace()
        return SimpleNamespace()

    monkeypatch.setattr(deps.importlib, "import_module", fake_import_module)

    assert "aiogram>=3.7.0" in deps.find_missing_packages()


def test_dependency_health_check_verifies_required_aiogram_api(monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_check_call(command, **_kwargs) -> None:
        commands.append(command)

    monkeypatch.setattr(deps.subprocess, "check_call", fake_check_call)

    assert deps._packages_ok("/python")

    script = commands[0][2]
    assert "from aiogram.client.default import DefaultBotProperties" in script
