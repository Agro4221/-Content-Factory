# Сборка

## Требования

- Python 3.11+
- PyInstaller 6+
- Windows 10/11 для целевого .exe

## PyInstaller

~~~powershell
python -m PyInstaller Parser.spec --clean
~~~

Результат появляется в dist/.

Parser.spec содержит настройки анализа, hidden imports и включения admin.html.

Отдельного installer и подписанного executable пока нет. CI workflow для версионных релизов реализован в `.github/workflows/release-windows.yml`; однако зависимости не закреплены lock-файлом, поэтому release environment пока не гарантирует побитово воспроизводимую сборку.

## Runtime-файлы

При запуске используются .env, Telegram session, bot_storage.db и temp_media/. Они не должны попадать в Git.

## Проверка

~~~powershell
python tools/smoke_test.py
~~~

Smoke test проверяет импорты и наличие базовых ресурсов. Это не end-to-end тест Telegram, AI или публикации.
