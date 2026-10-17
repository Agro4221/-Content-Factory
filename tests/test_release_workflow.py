"""Guard the Windows release workflow against accidental removal of release gates."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release-windows.yml"


def test_windows_release_workflow_is_tag_and_integration_branch_driven() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert '      - "v*"' in workflow
    assert "fix/local-bugfix-integration-2026-10-17" in workflow
    assert "workflow_dispatch:" in workflow
    assert "contents: write" in workflow


def test_release_validates_tests_before_packaging_or_publishing() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    tests = workflow.index("Run complete regression suite")
    build = workflow.index("Build Windows executable")
    verify = workflow.index("Verify executable and generate checksum")
    upload = workflow.index("Upload build artifact")
    release = workflow.index("Publish GitHub Release")

    assert tests < build < verify < upload < release
    assert "python -m pytest -q tests" in workflow
    assert "dist/Parser.exe.sha256" in workflow
    assert "gh release create" in workflow
    assert "--verify-tag" in workflow
    assert r"^v\d+\.\d+\.\d+(\.\d+)?" in workflow
    assert "$Matches[0].Length -ne $env:GITHUB_REF_NAME.Length" in workflow
