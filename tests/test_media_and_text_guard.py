"""Тесты: пропуск постов без содержательного текста и конвертация видео."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import media_convert  # noqa: E402


# ---------- значимые слова ----------

def test_significant_words_ignores_emoji_and_short_words() -> None:
    assert main._significant_words("") == set()
    assert main._significant_words("☠️") == set()
    assert main._significant_words("🔥🔥🔥") == set()
    assert main._significant_words("... !!!") == set()
    # Слова короче 4 букв и числа не считаются значимыми.
    assert main._significant_words("да и на 2026") == set()


# ---------- пересказ должен быть про исходный пост ----------

def test_rewrite_about_original_is_kept() -> None:
    original = "В Омске готовят новый закон о выгуле собак"
    rewritten = "В Омске готовят новый закон о выгуле собак! Власти хотят "
    assert main._rewrite_is_about_original(original, rewritten) is True


def test_invented_rewrite_is_rejected() -> None:
    """Из аниме-подписи модель сочинила новость про шторм — так публиковать нельзя."""
    original = "One, two, three- Rossi has gone hunting"
    invented = (
        "Внимание! Вчерашний шторм обрушился на Приморский край с новой силой. "
        "По данным МЧС, затоплено более десяти населённых пунктов, эвакуированы "
        "тысячи людей. Сильнее всего ветер и ливни вызвали разрушения в домах."
    )
    assert main._rewrite_is_about_original(original, invented) is False


def test_paraphrase_without_shared_words_is_kept() -> None:
    """Пересказ сопоставимой длины — это перевод, а не выдумка."""
    original = "Storm warning issued for the Primorsky region"
    rewritten = "В регионе объявлено предупреждение о шторме"
    assert main._rewrite_is_about_original(original, rewritten) is True


def test_rewrite_much_longer_without_shared_words_is_rejected() -> None:
    """Во много раз длиннее оригинала и без общих слов — это выдумка."""
    original = "Заметка о погоде"
    rewritten = (
        "Внимание! Вчерашний шторм обрушился на Приморский край с новой силой. "
        "По данным МЧС, затоплено более десяти населённых пунктов, эвакуированы "
        "тысячи людей, повреждены дороги и линии электропередачи."
    )
    assert main._rewrite_is_about_original(original, rewritten) is False


def test_rewrite_check_can_be_disabled() -> None:
    import config

    original_config = config.REWRITE_MIN_SHARED_WORDS
    config.REWRITE_MIN_SHARED_WORDS = 0
    try:
        assert main._rewrite_is_about_original("One, two, three", "совсем другой текст") is True
    finally:
        config.REWRITE_MIN_SHARED_WORDS = original_config


def test_significant_words_finds_real_content() -> None:
    words = main._significant_words("Готовый текст для теста")
    assert "готовый" in words
    assert "текст" in words
    assert "теста" in words


def test_emoji_only_caption_has_no_significant_words() -> None:
    """Пост с подписью из одного эмодзи переписывать нечего."""
    assert not main._significant_words("☠️")


def test_meaningful_length_counts_letters_and_digits_only() -> None:
    assert main._meaningful_text_length("") == 0
    assert main._meaningful_text_length("☠️") == 0
    assert main._meaningful_text_length("🔥🔥🔥") == 0
    assert main._meaningful_text_length("...") == 0
    assert main._meaningful_text_length("Привет") == 6
    # «Привет» (6) + «мир» (3) + «2026» (4) = 13; эмодзи и знаки не считаются.
    assert main._meaningful_text_length("☠️ Привет, мир! 2026") == 13


# ---------- какие файлы требуют конвертации ----------

@pytest.mark.parametrize(
    "name, expected",
    [
        ("clip.mp4", False),
        ("clip.mov", True),
        ("clip.MOV", True),
        ("clip.mkv", True),
        ("clip.webm", True),
        ("clip.avi", True),
        ("loop.gif", True),
        ("photo.jpg", False),
        ("photo.png", False),
        ("paper.pdf", False),
        ("noext", False),
    ],
)
def test_needs_conversion(name: str, expected: bool) -> None:
    assert media_convert.needs_conversion(name) is expected


def test_image_extensions_are_recognised() -> None:
    assert "jpg" in media_convert.IMAGE_EXTENSIONS
    assert "png" in media_convert.IMAGE_EXTENSIONS
    assert "mp4" in media_convert.NATIVE_VIDEO_EXTENSIONS
    assert "mov" not in media_convert.NATIVE_VIDEO_EXTENSIONS


def test_convert_returns_none_for_mp4(tmp_path) -> None:
    source = tmp_path / "already.mp4"
    source.write_bytes(b"not a real video")
    assert media_convert.convert_to_mp4(str(source)) is None


def test_convert_without_ffmpeg_returns_none(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(media_convert, "find_ffmpeg", lambda: None)
    source = tmp_path / "clip.mov"
    source.write_bytes(b"not a real video")
    assert media_convert.convert_to_mp4(str(source)) is None


def test_convert_broken_file_returns_none(tmp_path) -> None:
    """Битый файл не должен оставлять после себя мусор."""
    source = tmp_path / "broken.mov"
    source.write_bytes(b"\x00\x01\x02 not a video")
    result = media_convert.convert_to_mp4(str(source))
    assert result is None
    assert not list(tmp_path.glob("*.converted.mp4"))


# ---------- альбомы ----------

def test_album_of_photos_is_built_as_photos(tmp_path) -> None:
    from aiogram.types import InputMediaPhoto

    paths = [str(tmp_path / "a.jpg"), str(tmp_path / "b.png")]
    media = main._build_album_media(paths, "подпись")
    assert all(isinstance(item, InputMediaPhoto) for item in media)
    # Подпись допустима только у первого элемента альбома.
    assert media[0].caption == "подпись"
    assert media[1].caption is None


def test_album_of_videos_is_built_as_videos(tmp_path) -> None:
    from aiogram.types import InputMediaVideo

    paths = [str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4")]
    media = main._build_album_media(paths, None)
    assert all(isinstance(item, InputMediaVideo) for item in media)


def test_mixed_album_falls_back_to_documents(tmp_path) -> None:
    from aiogram.types import InputMediaDocument

    paths = [str(tmp_path / "a.jpg"), str(tmp_path / "b.mp4")]
    media = main._build_album_media(paths, "подпись")
    assert all(isinstance(item, InputMediaDocument) for item in media)
