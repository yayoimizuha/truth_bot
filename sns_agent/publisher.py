from __future__ import annotations

import logging
import os
import re

from .media_host import MediaHostClient
from .schemas import AgentResponse, NormalizedPost, PublishResult
from .truthsocial import TruthSocialClient

MENTION_RE = re.compile(r"@([A-Za-z0-9_.-]+)")
logger = logging.getLogger(__name__)


class Publisher:
    def __init__(self, social_client: TruthSocialClient, media_host_client: MediaHostClient | None = None):
        self._social = social_client
        self._media_host = media_host_client or MediaHostClient()
        self._char_limit = int(os.getenv("SNS_MAX_POST_LENGTH", "5000"))

    @staticmethod
    def extract_mentions(text: str) -> list[str]:
        return [match.group(1) for match in MENTION_RE.finditer(text)]

    async def aclose(self) -> None:
        await self._media_host.aclose()

    def build_status_text(
        self,
        response: AgentResponse,
        target_post: NormalizedPost,
        hosted_media_url: str | None = None,
    ) -> str:
        mentions = list(dict.fromkeys(target_post.leading_mentions + response.mentions))
        mention_prefix = " ".join(f"@{handle}" for handle in mentions if handle and handle != target_post.author_handle)
        body = response.text.strip()
        if hosted_media_url:
            body = f"{body}\n{hosted_media_url}".strip() if body else hosted_media_url
        status = f"{mention_prefix} {body}".strip()
        if len(status) > self._char_limit:
            status = status[: self._char_limit - 1].rstrip() + "…"
        return status

    async def publish(self, response: AgentResponse, target_post: NormalizedPost) -> PublishResult:
        response.validate()
        media_ids: list[str] = []
        hosted_media_url: str | None = None
        if response.images and self._media_host.enabled:
            hosted_page = await self._media_host.create_page(response.images)
            hosted_media_url = hosted_page.public_url
        else:
            for image in response.images:
                uploaded = await self._social.upload_media(image.filename, image.content, image.mime_type)
                media_ids.append(uploaded.media_id)
        if response.video is not None:
            uploaded = await self._social.upload_media(
                response.video.filename,
                response.video.content,
                response.video.mime_type,
            )
            media_ids.append(uploaded.media_id)
        status_text = self.build_status_text(response, target_post, hosted_media_url=hosted_media_url)
        logger.info(
            "sending message in_reply_to_id=%s text=%r images=%d video=%s",
            target_post.post_id,
            status_text,
            len(response.images),
            response.video is not None,
        )
        return await self._social.publish_reply(
            text=status_text,
            in_reply_to_id=target_post.post_id,
            media_ids=media_ids,
        )
