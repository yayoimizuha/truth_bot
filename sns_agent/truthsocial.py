from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .normalizer import normalize_status
from .proxy_client import ProxyHttpClient
from .schemas import MediaAttachment, NormalizedPost, NotificationItem, PublishResult


class TruthSocialClient:
    def __init__(self, proxy_client: ProxyHttpClient):
        self._proxy = proxy_client
        self._base_url = os.getenv("TRUTHSOCIAL_BASE_URL", "").rstrip("/")

    def _url(self, path: str) -> str:
        if self._base_url:
            return f"{self._base_url}/{path.lstrip('/')}"
        return f"/{path.lstrip('/')}"

    async def fetch_notifications(self) -> list[NotificationItem]:
        payload = await self._proxy.request_json(
            "GET",
            self._url("/api/v1/alerts?category=mentions&follow_mentions=false"),
        )
        notifications: list[NotificationItem] = []
        for item in payload or []:
            status = item.get("status")
            if not status:
                continue
            notifications.append(
                NotificationItem(
                    notification_id=str(item["id"]),
                    post_id=str(status["id"]),
                    reason=item.get("type") or "mention",
                    account_handle=item.get("account", {}).get("acct") or "",
                    payload=item,
                )
            )
        return notifications

    async def fetch_status(self, post_id: str) -> NormalizedPost:
        payload = await self._proxy.request_json("GET", self._url(f"/api/v1/statuses/{post_id}"))
        return normalize_status(payload)

    async def fetch_ancestor_chain(self, post: NormalizedPost) -> list[NormalizedPost]:
        if not post.parent_post_id:
            return [post]
        payload = await self._proxy.request_json(
            "GET",
            self._url(f"/api/v2/statuses/{post.post_id}/context/ancestors"),
        )
        ancestors = [normalize_status(item) for item in payload or []]
        return ancestors + [post]

    async def upload_media(self, filename: str, content: bytes, mime_type: str) -> MediaAttachment:
        response = await self._proxy.request_json(
            "POST",
            self._url("/api/v1/media"),
            files={"file": (filename, content, mime_type)},
        )
        return MediaAttachment(
            media_id=str(response["id"]),
            media_type=response.get("type") or "unknown",
            url=response.get("url") or response.get("preview_url") or "",
            preview_url=response.get("preview_url"),
            mime_type=mime_type,
        )

    async def download_media(self, media: MediaAttachment) -> bytes:
        if not media.url:
            raise RuntimeError(f"media {media.media_id} has no download URL")
        return await self._proxy.request_bytes("GET", media.url)

    @staticmethod
    def infer_media_filename(media: MediaAttachment) -> str:
        suffix = Path(media.url).suffix if media.url else ""
        if not suffix and media.mime_type:
            mime_map = {
                "image/png": ".png",
                "image/jpeg": ".jpg",
                "image/webp": ".webp",
                "image/gif": ".gif",
            }
            suffix = mime_map.get(media.mime_type, "")
        if not suffix:
            suffix = ".bin"
        return f"media-{media.media_id}{suffix}"

    async def publish_reply(
        self,
        *,
        text: str,
        in_reply_to_id: str,
        media_ids: list[str] | None = None,
    ) -> PublishResult:
        payload: dict[str, Any] = {
            "content_type": "text/plain",
            "in_reply_to_id": in_reply_to_id,
            "media_ids": media_ids or [],
            "poll": None,
            "published": True,
            "quote_id": None,
            "status": text,
            "title": "",
            "visibility": "public",
            "group_timeline_visible": True,
        }
        response = await self._proxy.request_json("POST", self._url("/api/v1/statuses"), json_body=payload)
        return PublishResult(status_id=str(response["id"]), raw_response=response)
