"""Regression tests for Unicode digit input in integer settings."""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import BackgroundTasks
from fastapi.responses import JSONResponse
from starlette.requests import Request

import Site


def make_json_request(payload: dict) -> Request:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    delivered = False

    async def receive() -> dict:
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/settings",
            "headers": [(b"content-type", b"application/json")],
            "query_string": b"",
        },
        receive,
    )


@pytest.mark.parametrize("key", ["poll_delay", "proxy_port", "tg_api_id", "local_port"])
def test_unicode_digit_returns_validation_error_not_server_error(key: str) -> None:
    response = asyncio.run(
        Site.api_post_settings(
            make_json_request({key: "²"}),
            BackgroundTasks(),
        )
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 422
    assert json.loads(response.body)["field"] == key


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("poll_delay", "60", "60"),
        ("proxy_port", "3128", "3128"),
        ("tg_api_id", "12345", "12345"),
        ("local_port", "1234", "1234"),
    ],
)
def test_ascii_integer_settings_remain_valid(
    key: str,
    value: str,
    expected: str,
) -> None:
    assert Site._normalize_validated_setting(key, value) == expected

@pytest.mark.parametrize("key", ["poll_delay", "proxy_port", "tg_api_id", "local_port"])
def test_oversized_integer_setting_returns_422_instead_of_500(key: str) -> None:
    response = asyncio.run(
        Site.api_post_settings(
            make_json_request({key: "9" * 5000}),
            BackgroundTasks(),
        )
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 422
    assert json.loads(response.body)["field"] == key
