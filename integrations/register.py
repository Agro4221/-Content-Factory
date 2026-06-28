"""Дополнительные HTTP-маршруты: онлайн-обновление лицензии и заготовка оплаты."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


class CheckoutBody(BaseModel):
    plan_id: str = Field(..., min_length=1)
    modules: list[str] = Field(default_factory=list)
    success_url: str = "http://127.0.0.1:8000/"
    cancel_url: str = "http://127.0.0.1:8000/"
    provider: str = "stub"


class LicenseStatus(BaseModel):
    """Заглушка статуса лицензии."""
    active: bool = True
    mode: str = "offline"
    message: Optional[str] = None
    hwid: Optional[str] = None
    server_configured: bool = False
    
    def to_public_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "mode": self.mode,
            "message": self.message or "",
        }


class LicenseClient:
    """Заглушка клиента лицензии."""
    
    @staticmethod
    def get_status(hwid: str) -> dict[str, Any]:
        return {
            "active": True,
            "mode": "offline",
            "message": None,
        }
    
    async def refresh_online_if_needed(self, hwid: str, force: bool = False) -> LicenseStatus:
        # Заглушка — всегда возвращаем активную лицензию в оффлайн-режиме
        return LicenseStatus(active=True, mode="offline")


def get_license_client() -> LicenseClient:
    """Получает заглушку клиента лицензии."""
    return LicenseClient()


class PaymentGatewayStub:
    """Заглушка шлюза платежей — всегда возвращает ошибку 503 (недоступно)."""
    
    async def create_checkout(
        self,
        plan_id: str,
        modules: list[str],
        hwid: str,
        success_url: str,
        cancel_url: str,
        provider: str = "stub",
    ) -> None:
        raise RuntimeError("Платежный шлюз недоступен (модуль удалён)")
    
    async def get_payment_status(self, session_id: str) -> dict[str, Any]:
        return {
            "status": "pending",  # Заглушка — всегда pending
            "session_id": session_id,
        }


def register_integrations(application: FastAPI) -> None:
    """Регистрирует маршруты лицензирования и платежей на существующем FastAPI app."""

    @application.post("/api/license/refresh")
    async def api_license_refresh() -> dict[str, Any]:
        hwid = get_hwid_from_db_or_default()  # type: ignore[name-defined]
        
        client = get_license_client()
        status = await client.refresh_online_if_needed(hwid)
        
        payload = status.to_public_dict() if hasattr(status, "to_public_dict") else dict(status)
        payload["hwid"] = hwid
        
        from config import LICENSE_SERVER_URL  # type: ignore[name-defined]
        payload["server_configured"] = bool(LICENSE_SERVER_URL or "")
        
        return payload

    @application.post("/api/payments/checkout")
    async def api_payments_checkout(body: CheckoutBody) -> dict[str, Any]:
        hwid = get_hwid_from_db_or_default()  # type: ignore[name-defined]
        
        gateway = PaymentGatewayStub()
        try:
            session = await gateway.create_checkout(
                plan_id=body.plan_id,
                modules=body.modules,
                hwid=hwid,
                success_url=body.success_url,
                cancel_url=body.cancel_url,
                provider=body.provider,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        
        return {
            "checkout_url": "",  # Заглушка — URL не генерируется
            "session_id": "",    # Заглушка
            "provider": body.provider,
            "plan_id": body.plan_id,
            "modules": body.modules,
        }

    @application.get("/api/payments/status/{session_id}")
    async def api_payments_status(session_id: str) -> dict[str, Any]:
        gateway = PaymentGatewayStub()
        try:
            return await gateway.get_payment_status(session_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc


def get_hwid_from_db_or_default() -> str:
    """Получает HWID из базы данных или возвращает дефолт."""
    try:
        from Auth import get_hwid as _get_hwid  # type: ignore[name-defined]
        return _get_hwid()
    except Exception:
        return "unknown-hwid"
