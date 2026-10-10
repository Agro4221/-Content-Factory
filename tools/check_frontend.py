"""Проверяет синтаксис встроенных JavaScript-блоков в admin.html."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "admin.html"
SCRIPT_TAG = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)


def main() -> int:
    if shutil.which("node") is None:
        print("Node.js не найден. Установите Node.js 20+ для проверки admin.html.", file=sys.stderr)
        return 2

    html = HTML_PATH.read_text(encoding="utf-8")
    scripts = [
        body
        for attrs, body in SCRIPT_TAG.findall(html)
        if not re.search(r"\bsrc\s*=", attrs, re.IGNORECASE) and body.strip()
    ]
    if not scripts:
        print(f"В {HTML_PATH.name} не найден встроенный JavaScript.", file=sys.stderr)
        return 1

    combined = "\\n".join(scripts)
    stale_calls = (
        "refreshBehaviorStatus()",
        "refreshReviewsStatus()",
        "syncBehaviorFormFromSettings(",
        "syncReviewsFormFromSettings(",
    )
    found_stale_calls = [call for call in stale_calls if call in combined]
    if found_stale_calls:
        print("Найдены вызовы удалённых модулей: " + ", ".join(found_stale_calls), file=sys.stderr)
        return 1

    for index, body in enumerate(scripts, start=1):
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
            handle.write(body)
            temp_path = Path(handle.name)
        try:
            result = subprocess.run(
                ["node", "--check", str(temp_path)],
                text=True,
                capture_output=True,
                check=False,
            )
        finally:
            temp_path.unlink(missing_ok=True)
        if result.returncode:
            print(f"JavaScript-блок #{index} не прошёл синтаксическую проверку:", file=sys.stderr)
            print(result.stderr or result.stdout, file=sys.stderr)
            return result.returncode
    print(f"OK: синтаксис {len(scripts)} встроенного JavaScript-блока(ов) корректен.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
