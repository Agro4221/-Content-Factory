"""Защита локального API панели управления.

Панель слушает 127.0.0.1 и работает без пароля: приложение запускается
двойным щелчком, пользователь не должен вводить логин при каждом запуске.
Никакой авторизации здесь нет и не предполагается — приложение полностью
локальное.

Но без дополнительных мер такой сервер уязвим:

* **DNS-rebinding.** Сайт злоумышленника отдаётся браузеру как 127.0.0.1,
  поэтому страница становится «своим» источником для панели и может читать
  настройки, журнал и тексты постов. Лечится проверкой заголовка Host.
* **CSRF.** Любой сайт, открытый в браузере пользователя, может отправить
  POST на 127.0.0.1:8000 — в том числе с ``Content-Type: text/plain``,
  для которого браузеру не нужен CORS-preflight. Лечится проверкой
  Origin / Sec-Fetch-Site и требованием служебного заголовка.
* **Кликджекинг.** Панель можно завернуть в ``iframe`` на чужом сайте и
  получить клики пользователя. Лечится ``frame-ancestors 'none'``.

Пользователь этого ничего не видит: панель открывается и работает как
раньше, просто запросы, пришедшие не от неё, отбрасываются.

Дополнительно — лимит размера тела запроса, чтобы огромный JSON не съел
память процесса.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse

#: Методы, которые меняют состояние и потому требуют защиты от CSRF.
STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Служебный заголовок, который панель добавляет ко всем запросам.
#: Браузер не может отправить произвольный заголовок в режиме no-cors:
#: для этого нужен CORS-preflight, на который сервер не отвечает.
CLIENT_HEADER = "X-Content-Factory"

#: Имена хостов, которые всегда считаются своими.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

#: Предельный размер тела запроса. Настройки панели — это текст и список
#: каналов; мегабайты сюда попасть не могут.
MAX_BODY_BYTES = 256 * 1024

#: Заголовки, которые добавляются к каждому ответу.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "font-src 'self' data:; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def allowed_hosts(api_host: str) -> set[str]:
    """Хосты, которым панель разрешает отвечать.

    По умолчанию панель доступна только с самой машины. Если пользователь
    осознанно выставил API_HOST на адрес в сети, этот адрес тоже
    разрешается — иначе панель просто перестанет открываться.
    """
    hosts = set(LOOPBACK_HOSTS)
    normalized = (api_host or "").strip().lower().strip("[]")
    if normalized and normalized not in hosts:
        hosts.add(normalized)
    return hosts


def _host_name(host_header: str) -> str:
    """Имя хоста из заголовка Host без порта и без квадратных скобок."""
    try:
        hostname = urlsplit(f"//{host_header}").hostname
    except ValueError:
        return ""
    return (hostname or "").strip().lower().strip("[]")


def is_allowed_host(host_header: str | None, permitted: set[str]) -> bool:
    """Проверка заголовка Host — защита от DNS-rebinding."""
    if not host_header:
        # HTTP/1.0 без Host: запрещаем, чтобы не открывать дорогу обходу.
        return False
    name = _host_name(host_header)
    if not name:
        return False
    return name in permitted


def _origin_matches_request(
    origin: str,
    host_header: str | None,
    request_scheme: str,
) -> bool:
    """Проверяет схему, имя хоста и порт в заголовке Origin."""
    try:
        origin_parts = urlsplit(origin)
        request_parts = urlsplit(f"//{host_header or ''}")
        origin_host = origin_parts.hostname
        request_host = request_parts.hostname
        origin_port = origin_parts.port
        request_port = request_parts.port
    except ValueError:
        return False

    scheme = request_scheme.strip().lower()
    origin_scheme = origin_parts.scheme.strip().lower()
    if (
        origin_scheme not in {"http", "https"}
        or origin_scheme != scheme
        or not origin_host
        or not request_host
        or origin_parts.username is not None
        or origin_parts.password is not None
        or origin_parts.path not in {"", "/"}
        or origin_parts.query
        or origin_parts.fragment
    ):
        return False

    origin_host = origin_host.strip().lower().strip("[]")
    request_host = request_host.strip().lower().strip("[]")
    default_port = 443 if scheme == "https" else 80
    return (
        origin_host == request_host
        and (origin_port or default_port) == (request_port or default_port)
    )


def is_trusted_write(request: Request, host_header: str | None) -> bool:
    """Доверять ли запросу, меняющему состояние.

    Запрос принимается, если выполнено хотя бы одно условие:

    * браузер сам подтвердил, что запрос «свой» (``Sec-Fetch-Site``);
    * заголовок Origin указывает на тот же хост, что и сам запрос;
    * присутствует служебный заголовок панели — его нельзя отправить
      из другого источника без CORS-preflight.

    Авторизации здесь нет: пользователь по-прежнему просто нажимает кнопки
    в панели. Проверка отсекает только запросы, пришедшие со стороны.
    """
    site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if site in {"same-origin", "none"}:
        return True

    origin = (request.headers.get("origin") or "").strip()
    if origin and _origin_matches_request(origin, host_header, request.url.scheme):
        return True

    return bool(request.headers.get(CLIENT_HEADER))


def body_too_large(content_length: str | None) -> bool:
    """Превышает ли заявленный размер тела допустимый."""
    if not content_length:
        return False
    try:
        return int(content_length) > MAX_BODY_BYTES
    except (TypeError, ValueError):
        return True


async def local_api_guard(request: Request, call_next):
    """Промежуточное ПО: хост, CSRF, размер тела и защитные заголовки.

    Подключается в Site.py через ``app.middleware("http")``.
    """
    from config import API_HOST

    permitted = allowed_hosts(API_HOST)
    host_header = request.headers.get("host")

    if not is_allowed_host(host_header, permitted):
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "msg": "Запрос отправлен с чужого адреса. Панель доступна только с этого компьютера.",
            },
        )

    if request.method in STATE_CHANGING_METHODS and not is_trusted_write(request, host_header):
        return JSONResponse(
            status_code=403,
            content={
                "status": "error",
                "msg": "Запрос отклонён: он пришёл не от панели. Обновите страницу и повторите.",
            },
        )

    if body_too_large(request.headers.get("content-length")):
        return JSONResponse(
            status_code=413,
            content={
                "status": "error",
                "msg": "Слишком большой запрос. Максимальный размер — 256 КБ.",
            },
        )

    response = await call_next(request)
    for header, value in SECURITY_HEADERS.items():
        response.headers[header] = value
    return response
