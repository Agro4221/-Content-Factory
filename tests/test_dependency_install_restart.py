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
