"""One-time copy from source v4 into Parser v1.28.3.6 (does not modify source)."""
from __future__ import annotations

import os
import shutil

SRC = r"C:\Users\serg9\Downloads\Project Parser + Modules\v4\v4"
DST = r"C:\Users\serg9\Downloads\Parser v1.28.3.6"

SKIP_DIRS = {".venv", "__pycache__", "temp_media", ".git", "tools", "Server files"}
SKIP_FILES = {
    "bot_storage.db",
    "bootstrap_copy.py",
    "VERSION.txt",
}
SKIP_PREFIXES = ("ai_aggregator_session",)

# Never overwrite project-specific overlays when re-running bootstrap.
PROTECTED_REL_PATHS = frozenset({
    "Auth.py",
    "config.py",
    "main.py",
    "README.md",
    os.path.join("modules", "__init__.py"),
    os.path.join("modules", "behavior", "__init__.py"),
    os.path.join("modules", "behavior", "engine.py"),
    os.path.join("modules", "reviews", "__init__.py"),
    os.path.join("modules", "reviews", "engine.py"),
    os.path.join("licensing", "__init__.py"),
    os.path.join("licensing", "client.py"),
    os.path.join("licensing", "offline_key.py"),
    os.path.join("licensing", "payment_gateway.py"),
    os.path.join("integrations", "__init__.py"),
    os.path.join("integrations", "register.py"),
})


def should_skip(name: str, is_dir: bool) -> bool:
    if is_dir and name in SKIP_DIRS:
        return True
    if not is_dir:
        if name in SKIP_FILES:
            return True
        if any(name.startswith(p) for p in SKIP_PREFIXES):
            return True
    return False


def main() -> None:
    copied = 0
    for root, dirs, files in os.walk(SRC):
        dirs[:] = [d for d in dirs if not should_skip(d, True)]
        rel = os.path.relpath(root, SRC)
        dest_root = DST if rel == "." else os.path.join(DST, rel)
        os.makedirs(dest_root, exist_ok=True)
        for fname in files:
            if should_skip(fname, False):
                continue
            src_path = os.path.join(root, fname)
            dst_path = os.path.join(dest_root, fname)
            if os.path.abspath(src_path) == os.path.abspath(dst_path):
                continue
            rel_dst = os.path.relpath(dst_path, DST).replace("\\", "/")
            if rel_dst in PROTECTED_REL_PATHS and os.path.exists(dst_path):
                continue
            shutil.copy2(src_path, dst_path)
            copied += 1
    print(f"Copied {copied} files to {DST}")


if __name__ == "__main__":
    main()
