import httpx
from typing import Any

from app.config import get_settings


class KeitaroError(Exception):
    """Ошибка ответа Admin API Keitaro."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class KeitaroClient:
    """Тонкий клиент Admin API Keitaro (эмулятора)."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: float = 30.0):
        settings = get_settings()
        self._http = httpx.Client(
            base_url=(base_url or settings.keitaro_api_url).rstrip("/"),
            headers={"Api-Key": api_key or settings.keitaro_api_token},
            timeout=timeout,
        )

    def _request(self, method: str, path: str, json: dict | None = None) -> Any:
        """Разобранный JSON ответа. Форма (список или объект) зависит от эндпоинта,
        поэтому транспорт возвращает Any — конкретные типы декларируют методы-обёртки."""
        response = self._http.request(method, path, json=json)
        if response.status_code >= 400:
            try:
                detail = response.json().get("error") or response.text
            except ValueError:
                detail = response.text
            raise KeitaroError(response.status_code, str(detail))
        return response.json()

    # --- справочники (в эмуляторе каталоги доступны только на чтение) ---

    def list_domains(self) -> list[dict]:
        return self._request("GET", "/domains")

    def list_groups(self) -> list[dict]:
        return self._request("GET", "/groups")

    def list_traffic_sources(self) -> list[dict]:
        return self._request("GET", "/traffic_sources")

    def list_offers(self) -> list[dict]:
        return self._request("GET", "/offers")

    # --- кампании ---

    def list_campaigns(self) -> list[dict]:
        return self._request("GET", "/campaigns")

    def get_campaign(self, campaign_id: int) -> dict:
        return self._request("GET", f"/campaigns/{campaign_id}")

    def create_campaign(self, **fields) -> dict:
        return self._request("POST", "/campaigns", json=fields)

    def update_campaign(self, campaign_id: int, **fields) -> dict:
        return self._request("PUT", f"/campaigns/{campaign_id}", json=fields)

    def delete_campaign(self, campaign_id: int) -> dict:
        return self._request("DELETE", f"/campaigns/{campaign_id}")

    # --- потоки ---

    def list_streams(self, campaign_id: int) -> list[dict]:
        return self._request("GET", f"/campaigns/{campaign_id}/streams")

    def create_stream(self, payload: dict) -> dict:
        return self._request("POST", "/streams", json=payload)

    def update_stream(self, stream_id: int, payload: dict) -> dict:
        return self._request("PUT", f"/streams/{stream_id}", json=payload)

    def delete_stream(self, stream_id: int) -> dict:
        return self._request("DELETE", f"/streams/{stream_id}")


def get_client() -> KeitaroClient:
    """Один клиент на процесс: httpx.Client держит пул соединений, плодить его на каждый
    запрос нельзя (утечка до GC). httpx.Client потокобезопасен для запросов."""
    global _shared_client
    if _shared_client is None:
        _shared_client = KeitaroClient()
    return _shared_client


_shared_client: KeitaroClient | None = None
