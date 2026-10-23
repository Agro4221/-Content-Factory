"""Guard the Windows release workflow against accidental removal or corruption."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release-windows.yml"

remove ci skip explanation@requires_ci
def test_windows_release_workflow_is_version_tag_and_manual_dispatch_driven() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert '      - "v*"' in workflow
    assert "workflow_dispatch:" in workflow
    assert "contents: write" in workflow


def test_release_validates_tests_before_packaging_or_publishing() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    tests = workflow.index("Run complete regression suite")
    build = workflow.index("Build Windows executable")
    smoke = workflow.index("Smoke-test built Windows executable")
    verify = workflow.index("Verify executable and generate checksum")
    upload = workflow.index("Upload build artifact")
    release = workflow.index("Publish GitHub Release")

    assert tests < build < smoke < verify < upload < release
    assert "python -m pytest -q tests" in workflow
    assert "dist/Parser.exe.sha256" in workflow
    assert "tools/smoke_test_windows.ps1" in workflow
    assert "gh release create" in workflow
    assert "--verify-tag" in workflow
    assert r"^v\d+\.\d+\.\d+(\.\d+)?$" in workflow


def test_release_workflow_has_no_corrupted_duplicate_fragments() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert workflow.count("- name: Set up Python") == 1
    assert workflow.count("- name: Set up Node.js") == 1
    assert "\n) {" not in workflow
    assert "Release tags must look like v1.2.3 or v1.2.3.4" in workflow


def test_release_workflow_is_valid_yaml_with_expected_job_structure() -> None:
    workflow = yaml.load(
        WORKFLOW.read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )

    assert workflow["name"] == "Validate and publish Windows release"
    assert "push" in workflow["on"]
    assert "tags" in workflow["on"]["push"]
    steps = workflow["jobs"]["windows-release"]["steps"]
    names = [step["name"] for step in steps]
    assert names.count("Set up Python") == 1
    assert names.count("Set up Node.js") == 1
    assert names.index("Run complete regression suite") < names.index("Build Windows executable")
    assert names.index("Smoke-test built Windows executable") < names.index("Publish GitHub Release")


def test_windows_smoke_script_checks_runtime_api_and_bundled_assets() -> None:
    script = (ROOT / "tools" / "smoke_test_windows.ps1").read_text(encoding="utf-8")

    assert "/api/bot/status" in script
    assert "Parser.exe smoke test passed" in script
    assert "favicon.png" in script
    assert "Stop-Process" in script

def test_release_and_regression_workflows_check_all_source_syntax() -> None:
    regression = (ROOT / ".github" / "workflows" / "local-bugfix-regression.yml").read_text(encoding="utf-8")
    release = WORKFLOW.read_text(encoding="utf-8")

    for workflow in (regression, release):
        assert "python -m compileall -q ." in workflow
        # Панель самодостаточна: её скрипт проверяет tools/check_frontend.py.
        assert "python tools/check_frontend.py" in workflow
        # Отдельного admin.js в проекте больше нет.
        assert "node --check script.js" not in workflow

