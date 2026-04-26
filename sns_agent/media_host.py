from __future__ import annotations

import os

import httpx

from .schemas import GeneratedImage, HostedMediaPage


class MediaHostClient:
    def __init__(self, base_url: str | None = None, timeout: float = 60.0):
        configured_base_url = (base_url or os.getenv("MEDIA_HOST_API_URL") or "").rstrip("/")
        self._base_url = configured_base_url
        self._client = httpx.AsyncClient(base_url=configured_base_url, timeout=timeout) if configured_base_url else None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def create_page(self, images: list[GeneratedImage]) -> HostedMediaPage:
        if self._client is None:
            raise RuntimeError("media host is not configured")
        if not images:
            raise ValueError("at least one image is required")

        files = [
            ("files", (image.filename, image.content, image.mime_type))
            for image in images
        ]
        response = await self._client.post("/media", files=files)
        response.raise_for_status()
        payload = response.json()
        return HostedMediaPage(
            page_id=str(payload["page_id"]),
            public_url=str(payload["public_url"]),
        )
