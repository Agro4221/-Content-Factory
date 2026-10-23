"""Подготовка видеофайлов к отправке в Telegram.

При необходимости конвертирует поддерживаемые форматы в MP4 с помощью
``ffmpeg``. Если инструмент недоступен или конвертация завершается ошибкой,
вызывающий код может отправить исходный файл как документ.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys

#: Расширения, которые нужно приводить к MP4 перед отправкой в Telegram.
CONVERTIBLE_VIDEO_EXTENSIONS = {
    "mov", "mkv", "webm", "avi", "m4v", "3gp", "3g2", "ogv", "flv", "wmv", "ts",
    "gif",
}

#: Расширения, которые Telegram принимает как видео без конвертации.
NATIVE_VIDEO_EXTENSIONS = {"mp4", "m4v"}

#: Расширения изображений, которые Telegram показывает как фото.
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff", "heic"}

#: Где искать ffmpeg, если его нет в PATH.
_FFMPEG_LOCATIONS = (
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    "/opt/homebrew/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    "/usr/bin/ffmpeg",
)

#: Таймаут одной конвертации, секунды.
CONVERT_TIMEOUT_S = 300


def file_extension(path: str) -> str:
    """Расширение файла в нижнем регистре без точки."""
    return os.path.splitext(path)[1].lstrip(".").lower()


def needs_conversion(path: str) -> bool:
    """Нужно ли конвертировать файл перед отправкой в Telegram."""
    ext = file_extension(path)
    if ext in NATIVE_VIDEO_EXTENSIONS:
        return False
    return ext in CONVERTIBLE_VIDEO_EXTENSIONS


def find_ffmpeg() -> str | None:
    """Путь к ffmpeg или None, если он не установлен."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in _FFMPEG_LOCATIONS:
        if os.path.isfile(candidate):
            return candidate
    return None


def _build_command(ffmpeg: str, source: str, target: str) -> list[str]:
    """Команда конвертации в MP4 (H.264 + AAC), совместимая с Telegram."""
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-i", source,
    ]
    if file_extension(source) == "gif":
        # Анимированный GIF: кадры в видео с частотой 25 fps.
        command += [
            "-filter_complex",
            "fps=25,scale=trunc(iw/2)*2:trunc(ih/2)*2:flags=lanczos",
        ]
    command += [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
    ]
    if file_extension(source) != "gif":
        command += ["-c:a", "aac", "-b:a", "128k"]
    else:
        command += ["-an"]
    command += [target]
    return command


def convert_to_mp4(source: str, ffmpeg: str | None = None) -> str | None:
    """Конвертирует файл в MP4 рядом с ним.

    Возвращает путь к готовому MP4 или None, если конвертация невозможна
    (нет ffmpeg, ffmpeg не справился, исходник не читается). Исходный файл
    при этом не удаляется.
    """
    if not os.path.isfile(source):
        return None
    if not needs_conversion(source):
        return None

    tool = ffmpeg or find_ffmpeg()
    if not tool:
        return None

    stem = os.path.splitext(source)[0]
    target = f"{stem}.converted.mp4"
    try:
        result = subprocess.run(
            _build_command(tool, source, target),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=CONVERT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        _remove_quietly(target)
        return None

    if result.returncode != 0 or not os.path.isfile(target) or os.path.getsize(target) == 0:
        _remove_quietly(target)
        return None
    return target


def _remove_quietly(path: str) -> None:
    with contextlib.suppress(OSError):
        os.remove(path)


def ffmpeg_version() -> str:
    """Версия ffmpeg для диагностики (пустая строка, если его нет)."""
    tool = find_ffmpeg()
    if not tool:
        return ""
    try:
        result = subprocess.run(
            [tool, "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    first_line = result.stdout.decode("utf-8", errors="replace").splitlines()
    return first_line[0].strip() if first_line else ""


if __name__ == "__main__":  # pragma: no cover - ручной диагностический запуск
    print("ffmpeg:", find_ffmpeg() or "не найден")
    print("версия:", ffmpeg_version() or "-")
    print("python:", sys.version.split()[0])
