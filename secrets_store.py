"""Защищённое хранение секретов через Windows DPAPI.

Значения шифруются системным API и привязываются к учётной записи Windows
и текущему компьютеру. Значения без префикса относятся к старому формату:
они остаются читаемыми и переводятся на новый формат при записи.

Модуль не выводит значения секретов в журналы и консоль.
"""
from __future__ import annotations

import base64
import binascii
import ctypes
import sys
from ctypes import wintypes

#: Префикс зашифрованного значения в базе.
PREFIX = "dpapi:v1:"

#: Энтропия — дополнительная привязка блоба к приложению (сама по себе не секрет).
_ENTROPY = b"ContentFactory.settings.v1"

_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class SecretDecryptionError(RuntimeError):
    """Сохранённый секрет не удалось расшифровать.

    Обычно это значит, что база скопирована на другой компьютер или
    запущена под другой учётной записью Windows. Значение нужно ввести заново.
    """


class SecretEncryptionError(RuntimeError):
    """Не удалось зашифровать секрет."""


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def supported() -> bool:
    """Доступно ли системное шифрование секретов."""
    return sys.platform == "win32"


def is_encrypted(value: object) -> bool:
    """Проверяет, имеет ли значение формат DPAPI."""
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return False
    payload = value[len(PREFIX):]
    if not payload:
        return False
    try:
        base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return False
    return True


def _to_blob(data: bytes) -> tuple[_DATA_BLOB, object]:
    """Готовит DATA_BLOB с копией данных (буфер живёт, пока жив blob)."""
    if not data:
        buffer = ctypes.create_string_buffer(1)
        blob = _DATA_BLOB(0, ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        return blob, buffer
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = _DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    return blob, buffer


def _configure_protect_api() -> tuple[object, object]:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = crypt32.CryptProtectData.argtypes
    crypt32.CryptUnprotectData.restype = wintypes.BOOL

    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


def _protect(data: bytes) -> bytes:
    if not supported():
        raise SecretEncryptionError("Шифрование секретов доступно только в Windows.")
    crypt32, kernel32 = _configure_protect_api()
    blob, _buffer = _to_blob(data)
    entropy_blob, _entropy_buffer = _to_blob(_ENTROPY)
    out_blob = _DATA_BLOB()
    ok = crypt32.CryptProtectData(
        ctypes.byref(blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise SecretEncryptionError(
            f"CryptProtectData завершился с ошибкой {ctypes.get_last_error()}"
        )
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _unprotect(data: bytes) -> bytes:
    if not supported():
        raise SecretDecryptionError("Шифрование секретов доступно только в Windows.")
    crypt32, kernel32 = _configure_protect_api()
    blob, _buffer = _to_blob(data)
    entropy_blob, _entropy_buffer = _to_blob(_ENTROPY)
    out_blob = _DATA_BLOB()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise SecretDecryptionError(
            f"CryptUnprotectData завершился с ошибкой {ctypes.get_last_error()}"
        )
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def encrypt(value: str, account: str = "") -> str:
    """Шифрует строку. Пустая строка возвращается как есть.

    Параметр ``account`` сохранён для совместимости с вызывающим кодом.
    """
    if not isinstance(value, str) or value == "":
        return value
    if is_encrypted(value):
        return value

    if not supported():
        raise SecretEncryptionError(
            "Шифрование секретов доступно только в Windows."
        )
    blob = _protect(value.encode("utf-8"))
    return PREFIX + base64.b64encode(blob).decode("ascii")


def decrypt(value: str, account: str = "") -> str:
    """Расшифровывает значение.

    Значение без префикса — legacy-plaintext, возвращается без изменений.
    Ошибка расшифровки поднимается как `SecretDecryptionError`.
    """
    if not isinstance(value, str) or value == "":
        return value
    if not is_encrypted(value):
        return value

    if not supported():
        raise SecretDecryptionError("Шифрование секретов доступно только в Windows.")
    try:
        raw = base64.b64decode(value[len(PREFIX):], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SecretDecryptionError("Повреждённое зашифрованное значение") from exc
    try:
        return _unprotect(raw).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SecretDecryptionError("Зашифрованное значение повреждено") from exc
