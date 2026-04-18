from __future__ import annotations

import asyncio
import logging
import os

from .commands import CommandParseError, parse_command, to_image_request, to_video_request
from .media import ImageGenerator, VideoGenerator
from .proxy_client import ProxyHttpClient
from .publisher import Publisher
from .responder import LLMResponder
from .schemas import AgentResponse
from .state_store import StateStore
from .truthsocial import TruthSocialClient

logger = logging.getLogger(__name__)


class AgentService:
    def __init__(self):
        proxy_base_url = os.getenv("TS_HOOK_SERVER_BASE_URL", "http://127.0.0.1:8000")
        self._proxy = ProxyHttpClient(proxy_base_url)
        self._state = StateStore(os.getenv("STATE_DB_PATH", "history.db"))
        self._social = TruthSocialClient(self._proxy)
        self._publisher = Publisher(self._social)
        self._image_generator = ImageGenerator(self._proxy)
        self._video_generator = VideoGenerator(self._proxy)
        self._responder = LLMResponder(self._image_generator, self._video_generator)
        self._poll_seconds = float(os.getenv("NOTIFICATION_POLL_SECONDS", "20"))

    async def aclose(self) -> None:
        await self._responder.aclose()
        await self._proxy.aclose()

    async def run_forever(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception as exc:
                logger.exception("poll failed: %s", exc)
            await asyncio.sleep(self._poll_seconds)

    async def poll_once(self) -> None:
        notifications = await self._social.fetch_notifications()
        for notification in notifications:
            if self._state.is_processed(notification.notification_id):
                continue
            await self._handle_notification(notification.notification_id, notification.post_id)

    async def _handle_notification(self, notification_id: str, post_id: str) -> None:
        target_post = await self._social.fetch_status(post_id)
        chain = await self._social.fetch_ancestor_chain(target_post)

        try:
            command = parse_command(target_post.command_text)
            if command is not None:
                response = await self._handle_command(command)
            else:
                response = await self._responder.respond(chain, target_post)
            await self._publisher.publish(response, target_post)
            self._state.mark_processed(notification_id)
        except CommandParseError as exc:
            error_response = AgentResponse(text=f"/{target_post.command_text.splitlines()[0].lstrip('/')} の解析に失敗しました: {exc}")
            await self._publisher.publish(error_response, target_post)
            self._state.mark_processed(notification_id)
        except Exception as exc:
            logger.exception("notification %s failed", notification_id)
            raise

    async def _handle_command(self, command) -> AgentResponse:
        if command.name == "image":
            request = to_image_request(command)
            images = await self._image_generator.generate(request)
            return AgentResponse(text="", images=images)
        if command.name == "video":
            request = to_video_request(command)
            video = await self._video_generator.generate(request)
            return AgentResponse(text="", video=video)
        raise CommandParseError(f"unsupported command: /{command.name}")
