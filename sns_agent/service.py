from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import os
import time

from .commands import CommandParseError, parse_command, to_image_request, to_video_request
from .gpu_tasks import GPUTaskLimiter
from .media import ImageGenerator, VideoGenerator
from .proxy_client import ProxyHttpClient
from .publisher import Publisher
from .responder import LLMResponder
from .schemas import AgentResponse, NotificationItem
from .state_store import StateStore
from .truthsocial import TruthSocialClient

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class NotificationRetryState:
    failures: int = 0
    next_attempt_at: float = 0.0
    exhausted: bool = False


class AgentService:
    def __init__(self):
        proxy_base_url = os.getenv("TS_HOOK_SERVER_BASE_URL", "http://127.0.0.1:8000")
        self._proxy = ProxyHttpClient(proxy_base_url)
        self._state = StateStore(os.getenv("STATE_DB_PATH", "history.db"))
        self._social = TruthSocialClient(self._proxy)
        self._publisher = Publisher(self._social)
        self._notification_semaphore = asyncio.Semaphore(int(os.getenv("NOTIFICATION_MAX_CONCURRENCY", "8")))
        self._notification_lock = asyncio.Lock()
        self._inflight_notification_ids: set[str] = set()
        self._notification_tasks: set[asyncio.Task[None]] = set()
        self._notification_retry_states: dict[str, NotificationRetryState] = {}
        self._gpu_task_limiter = GPUTaskLimiter(int(os.getenv("GPU_TASK_MAX_CONCURRENCY", "1")))
        self._image_generator = ImageGenerator(gpu_task_limiter=self._gpu_task_limiter)
        self._video_generator = VideoGenerator()
        self._responder = LLMResponder(self._image_generator, self._video_generator)
        self._poll_seconds = float(os.getenv("NOTIFICATION_POLL_SECONDS", "20"))
        self._notification_failure_max_retries = max(0, int(os.getenv("NOTIFICATION_FAILURE_MAX_RETRIES", "4")))
        self._notification_retry_base_seconds = max(
            0.0,
            float(os.getenv("NOTIFICATION_RETRY_BASE_SECONDS", "30.0")),
        )
        self._notification_retry_max_seconds = max(
            self._notification_retry_base_seconds,
            float(os.getenv("NOTIFICATION_RETRY_MAX_SECONDS", "600.0")),
        )

    async def aclose(self) -> None:
        tasks = list(self._notification_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._notification_tasks.clear()
        async with self._notification_lock:
            self._inflight_notification_ids.clear()
            self._notification_retry_states.clear()
        await self._image_generator.aclose()
        await self._video_generator.aclose()
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
            if self._is_notification_backing_off(notification.notification_id):
                continue
            await self._spawn_notification_task(notification)

    async def _spawn_notification_task(self, notification: NotificationItem) -> None:
        async with self._notification_lock:
            if notification.notification_id in self._inflight_notification_ids:
                return
            self._inflight_notification_ids.add(notification.notification_id)
        task = asyncio.create_task(
            self._run_notification_task(notification.notification_id, notification.post_id),
            name=f"notification:{notification.notification_id}",
        )
        self._notification_tasks.add(task)
        task.add_done_callback(self._notification_tasks.discard)

    async def _run_notification_task(self, notification_id: str, post_id: str) -> None:
        try:
            async with self._notification_semaphore:
                await self._handle_notification(notification_id, post_id)
            await self._clear_notification_retry_state(notification_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._record_notification_failure(notification_id)
            logger.exception("notification %s failed", notification_id)
        finally:
            async with self._notification_lock:
                self._inflight_notification_ids.discard(notification_id)

    async def _handle_notification(self, notification_id: str, post_id: str) -> None:
        target_post = await self._social.fetch_status(post_id)
        chain = await self._social.fetch_ancestor_chain(target_post)
        logger.info(
            "received prompt notification_id=%s post_id=%s author=@%s prompt=%r",
            notification_id,
            post_id,
            target_post.author_handle,
            target_post.command_text or target_post.llm_text,
        )

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
        except Exception:
            raise

    def _is_notification_backing_off(self, notification_id: str) -> bool:
        state = self._notification_retry_states.get(notification_id)
        if state is None:
            return False
        if state.exhausted:
            return True
        return state.next_attempt_at > time.monotonic()

    async def _clear_notification_retry_state(self, notification_id: str) -> None:
        async with self._notification_lock:
            self._notification_retry_states.pop(notification_id, None)

    async def _record_notification_failure(self, notification_id: str) -> None:
        async with self._notification_lock:
            state = self._notification_retry_states.setdefault(notification_id, NotificationRetryState())
            state.failures += 1
            if state.failures > self._notification_failure_max_retries:
                state.exhausted = True
                logger.error(
                    "notification %s exhausted retries failures=%d",
                    notification_id,
                    state.failures,
                )
                return
            delay = self._notification_backoff_delay(state.failures - 1)
            state.next_attempt_at = time.monotonic() + delay
            logger.warning(
                "notification %s backing off failures=%d retry_in=%.2fs",
                notification_id,
                state.failures,
                delay,
            )

    def _notification_backoff_delay(self, attempt: int) -> float:
        return min(self._notification_retry_max_seconds, self._notification_retry_base_seconds * (2 ** attempt))

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
