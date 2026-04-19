import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from sns_agent.schemas import AgentResponse, NotificationItem, NormalizedPost
from sns_agent.service import AgentService, NotificationRetryState


class ServiceConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._cleanup_temp_dir)

    async def _cleanup_temp_dir(self):
        self._temp_dir.cleanup()

    def _build_service(self) -> AgentService:
        env = {
            "STATE_DB_PATH": str(Path(self._temp_dir.name) / "state.db"),
            "NOTIFICATION_MAX_CONCURRENCY": "8",
            "GPU_TASK_MAX_CONCURRENCY": "1",
        }
        proxy = MagicMock()
        proxy.aclose = AsyncMock()
        responder = MagicMock()
        responder.aclose = AsyncMock()
        image_generator = MagicMock()
        image_generator.aclose = AsyncMock()
        video_generator = MagicMock()
        video_generator.aclose = AsyncMock()
        with (
            patch.dict("os.environ", env, clear=False),
            patch("sns_agent.service.ProxyHttpClient", return_value=proxy),
            patch("sns_agent.service.TruthSocialClient"),
            patch("sns_agent.service.Publisher"),
            patch("sns_agent.service.ImageGenerator", return_value=image_generator),
            patch("sns_agent.service.VideoGenerator", return_value=video_generator),
            patch("sns_agent.service.LLMResponder", return_value=responder),
        ):
            service = AgentService()
        service._proxy = proxy
        service._responder = responder
        service._image_generator = image_generator
        service._video_generator = video_generator
        return service

    async def test_poll_once_schedules_notifications_without_waiting_for_completion(self):
        service = self._build_service()
        service._social.fetch_notifications = AsyncMock(
            return_value=[
                NotificationItem("1", "p1", "mention", "alice", {}),
                NotificationItem("2", "p2", "mention", "bob", {}),
            ]
        )
        gate = asyncio.Event()

        async def handle(notification_id: str, post_id: str) -> None:
            await gate.wait()

        service._handle_notification = AsyncMock(side_effect=handle)

        started = time.perf_counter()
        await service.poll_once()
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.03)
        self.assertEqual(len(service._notification_tasks), 2)

        gate.set()
        await asyncio.gather(*list(service._notification_tasks))

    async def test_poll_once_skips_inflight_duplicate_notifications(self):
        service = self._build_service()
        service._social.fetch_notifications = AsyncMock(
            return_value=[
                NotificationItem("1", "p1", "mention", "alice", {}),
                NotificationItem("1", "p1", "mention", "alice", {}),
            ]
        )
        gate = asyncio.Event()

        async def handle(notification_id: str, post_id: str) -> None:
            await gate.wait()

        service._handle_notification = AsyncMock(side_effect=handle)

        await service.poll_once()
        self.assertEqual(len(service._notification_tasks), 1)

        gate.set()
        await asyncio.gather(*list(service._notification_tasks))

    async def test_aclose_cancels_inflight_notification_tasks(self):
        service = self._build_service()
        service._social.fetch_notifications = AsyncMock(
            return_value=[NotificationItem("1", "p1", "mention", "alice", {})]
        )

        async def handle(notification_id: str, post_id: str) -> None:
            await asyncio.sleep(10)

        service._handle_notification = AsyncMock(side_effect=handle)

        await service.poll_once()
        self.assertEqual(len(service._notification_tasks), 1)

        await service.aclose()

        self.assertEqual(len(service._notification_tasks), 0)
        self.assertEqual(service._inflight_notification_ids, set())
        service._proxy.aclose.assert_awaited_once()
        service._responder.aclose.assert_awaited_once()
        service._image_generator.aclose.assert_awaited_once()
        service._video_generator.aclose.assert_awaited_once()

    async def test_handle_notification_logs_received_prompt(self):
        service = self._build_service()
        target_post = NormalizedPost(
            post_id="p1",
            author_handle="alice",
            author_display_name="Alice",
            parent_post_id=None,
            raw_content="",
            plain_text="@bot hello",
            llm_text="hello",
            command_text="hello",
            leading_mentions=["bot"],
            inline_mentions=[],
            expanded_urls=[],
            media=[],
            created_at=None,
        )
        service._social.fetch_status = AsyncMock(return_value=target_post)
        service._social.fetch_ancestor_chain = AsyncMock(return_value=[target_post])
        service._responder.respond = AsyncMock(return_value=AgentResponse(text="reply"))
        service._publisher.publish = AsyncMock()

        with self.assertLogs("sns_agent.service", level="INFO") as captured:
            await service._handle_notification("n1", "p1")

        self.assertIn("received prompt", captured.output[0])
        self.assertIn("hello", captured.output[0])

    async def test_poll_once_skips_notifications_in_backoff(self):
        service = self._build_service()
        service._notification_retry_states["1"] = NotificationRetryState(
            failures=1,
            next_attempt_at=time.monotonic() + 60,
        )
        service._social.fetch_notifications = AsyncMock(
            return_value=[NotificationItem("1", "p1", "mention", "alice", {})]
        )
        service._spawn_notification_task = AsyncMock()

        await service.poll_once()

        service._spawn_notification_task.assert_not_awaited()

    async def test_run_notification_task_records_backoff_after_failure(self):
        service = self._build_service()
        service._handle_notification = AsyncMock(side_effect=RuntimeError("boom"))

        await service._run_notification_task("1", "p1")

        state = service._notification_retry_states["1"]
        self.assertEqual(state.failures, 1)
        self.assertGreater(state.next_attempt_at, time.monotonic())
