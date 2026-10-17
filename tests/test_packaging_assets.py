"""Check that the one-file Windows bundle contains every runtime resource."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "Parser.spec"

REQUIRED_ASSETS = {
    "admin.html",
    "activate.html",
    ".env.example",
    "favicon.png",
    "brand-logo.webp",
    "brand-logo.png",
    "brand-logo@2x.webp",
    "brand-logo@2x.png",
}


def _assignments(tree: ast.Module) -> dict[str, ast.expr]:
    found: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = node.value
    return found


def test_parser_spec_bundles_all_runtime_assets() -> None:
    tree = ast.parse(SPEC_PATH.read_text(encoding="utf-8"), filename=str(SPEC_PATH))
    assignments = _assignments(tree)

    assets_node = assignments.get("BUNDLE_ASSETS")
    assert assets_node is not None, "Parser.spec must declare BUNDLE_ASSETS"
    assets = set(ast.literal_eval(assets_node))
    assert REQUIRED_ASSETS <= assets

    for filename in assets:
        assert (ROOT / filename).is_file(), f"Bundled file does not exist: {filename}"


def test_pyinstaller_datas_map_bundle_assets_to_bundle_root() -> None:
    tree = ast.parse(SPEC_PATH.read_text(encoding="utf-8"), filename=str(SPEC_PATH))
    assignments = _assignments(tree)
    datas = assignments.get("datas")

    assert isinstance(datas, ast.ListComp), "datas must be generated from BUNDLE_ASSETS"
    assert any(
        isinstance(generator.iter, ast.Name) and generator.iter.id == "BUNDLE_ASSETS"
        for generator in datas.generators
    ), "PyInstaller datas must iterate over BUNDLE_ASSETS"

    assert isinstance(datas.elt, ast.Tuple) and len(datas.elt.elts) == 2
    destination = datas.elt.elts[1]
    assert isinstance(destination, ast.Constant) and destination.value == "."



def test_pyinstaller_hidden_imports_use_real_installed_module_names() -> None:
    tree = ast.parse(SPEC_PATH.read_text(encoding="utf-8"), filename=str(SPEC_PATH))
    assignments = _assignments(tree)

    hiddenimports_node = assignments.get("hiddenimports")
    assert hiddenimports_node is not None
    hiddenimports = set(ast.literal_eval(hiddenimports_node))

    # python-dotenv is installed as the importable module "dotenv".
    assert "dotenv" in hiddenimports
    assert "python_dotenv" not in hiddenimports
    # This project does not declare or import pydantic-settings.
    assert "pydantic_settings" not in hiddenimports
