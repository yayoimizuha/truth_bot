from __future__ import annotations

from typing import Any

import httpx


class ProxyHttpClient:
    def __init__(self, base_url: str, timeout: float = 60.0):
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        content: bytes | None = None,
        files: Any = None,
    ) -> httpx.Response:
        response = await self._client.request(
            method,
            path,
            headers=headers,
            params=params,
            json=json_body,
            content=content,
            files=files,
        )
        response.raise_for_status()
        return response

    async def request_json(self, method: str, target_url: str, **kwargs: Any) -> Any:
        response = await self.request(method, target_url, **kwargs)
        if not response.content:
            return None
        return response.json()

    async def request_text(self, method: str, target_url: str, **kwargs: Any) -> str:
        response = await self.request(method, target_url, **kwargs)
        return response.text

    async def request_bytes(self, method: str, target_url: str, **kwargs: Any) -> bytes:
        response = await self.request(method, target_url, **kwargs)
        return response.content
