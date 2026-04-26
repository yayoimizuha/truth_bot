from __future__ import annotations

import logging
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

from .normalizer import normalize_status
from .proxy_client import ProxyHttpClient
from .schemas import MediaAttachment, NormalizedPost, NotificationItem, PublishResult

BLANK_LINES_RE = re.compile(r"\n{3,}")
logger = logging.getLogger(__name__)


class TruthSocialClient:
    def __init__(self, proxy_client: ProxyHttpClient):
        self._proxy = proxy_client
        self._base_url = os.getenv("TRUTHSOCIAL_BASE_URL", "").rstrip("/")
        self._media_host_api_url = os.getenv("MEDIA_HOST_API_URL", "").rstrip("/")

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
        return await self._normalize_and_expand(payload)

    async def fetch_ancestor_chain(self, post: NormalizedPost) -> list[NormalizedPost]:
        if not post.parent_post_id:
            return [post]
        payload = await self._proxy.request_json(
            "GET",
            self._url(f"/api/v2/statuses/{post.post_id}/context/ancestors"),
        )
        ancestors = [await self._normalize_and_expand(item) for item in payload or []]
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

    async def _normalize_and_expand(self, payload: dict[str, Any]) -> NormalizedPost:
        post = normalize_status(payload)
        hosted_media, resolved_urls = await self._resolve_hosted_media(post.expanded_urls)
        if hosted_media:
            post.media.extend(hosted_media)
            post.llm_text = self._strip_resolved_urls(post.llm_text, resolved_urls)
        return post

    async def _resolve_hosted_media(self, urls: list[str]) -> tuple[list[MediaAttachment], list[str]]:
        if not self._media_host_api_url:
            return [], []

        media: list[MediaAttachment] = []
        resolved_urls: list[str] = []
        for url in urls:
            page_id = self._media_host_page_id(url)
            if page_id is None:
                continue
            try:
                payload = await self._proxy.request_json("GET", f"{self._media_host_api_url}/api/pages/{page_id}")
            except Exception as exc:
                logger.warning("failed to resolve hosted media url=%s error=%s", url, exc)
                continue
            items = payload.get("items") or []
            for index, item in enumerate(items, start=1):
                media.append(
                    MediaAttachment(
                        media_id=f"media-host:{page_id}:{index}",
                        media_type=item.get("kind") or "unknown",
                        url=item.get("url") or "",
                        preview_url=item.get("poster_url"),
                        mime_type=item.get("mime_type") or item.get("poster_mime_type"),
                        source="media_host",
                    )
                )
            resolved_urls.append(url)
        return media, resolved_urls

    def _media_host_page_id(self, url: str) -> str | None:
        if not self._media_host_api_url:
            return None
        target = urlsplit(url)
        base = urlsplit(self._media_host_api_url)
        if (target.scheme, target.netloc) != (base.scheme, base.netloc):
            return None
        base_parts = [part for part in base.path.split("/") if part]
        target_parts = [part for part in target.path.split("/") if part]
        if target_parts[: len(base_parts)] != base_parts:
            return None
        parts = target_parts[len(base_parts):]
        if len(parts) != 2 or parts[0] != "m" or not parts[1]:
            return None
        return parts[1]

    @staticmethod
    def _strip_resolved_urls(text: str, resolved_urls: list[str]) -> str:
        stripped = text
        for url in resolved_urls:
            stripped = stripped.replace(url, " ")
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in stripped.splitlines()]
        compact = "\n".join(line for line in lines if line)
        return BLANK_LINES_RE.sub("\n\n", compact).strip()

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
