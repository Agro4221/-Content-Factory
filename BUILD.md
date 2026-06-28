# Сборка .exe (Windows)

## Требования

- Python 3.11+
- Windows 10/11

## Быстрая сборка

```powershell
cd "C:\path\to\Parser v1.28.3.6"
.\build\build_exe.ps1
```

Результат: `dist\MaryJane.exe`

## Поведение .exe

| Что | Где |
|-----|-----|
| `.env`, БД, сессия Telegram | Рядом с `MaryJane.exe` (`BASE_DIR`) |
| HTML, модули, licensing | Внутри bundle (`_MEIPASS`) |
| Админка | Открывается в браузере через `BROWSER_OPEN_ON_START=1` |

При первом запуске:

1. Копируется `.env.example` → `.env` (если `.env` нет)
2. Создаётся `temp_media/`
3. Проверяется лицензия (`LICENSE_VERIFY_ON_STARTUP=1`)
4. Для онлайн-токена — принудительный запрос на сервер

## Защита кода

PyInstaller **не шифрует** исходники — это упаковка байткода и ресурсов. Это защита от случайного копирования, не от реверса.

Для усиления (опционально):

- **PyArmor** — обфускация перед PyInstaller
- **Nuitka** — компиляция в нативный код (другой пайплайн сборки)

Секреты:

- `LICENSE_SIGNING_SECRET` — только на сервере лицензий и в `.env` клиента, не в репозитории
- Не включайте `.env` в spec/datas

## Ручная сборка

```powershell
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pyinstaller --noconfirm --clean build\MaryJane.spec
```

## Проверка перед релизом

```powershell
.venv\Scripts\python tools\smoke_test.py
```
