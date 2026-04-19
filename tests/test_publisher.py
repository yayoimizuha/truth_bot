import unittest
from unittest.mock import AsyncMock

from sns_agent.publisher import Publisher
from sns_agent.schemas import AgentResponse, NormalizedPost, PublishResult


class DummySocialClient:
    def __init__(self):
        self.publish_reply = AsyncMock(return_value=PublishResult(status_id="reply-1", raw_response={}))
        self.upload_media = AsyncMock()


class PublisherTests(unittest.TestCase):
    def test_mentions_are_prefixed_and_deduped(self):
        publisher = Publisher(DummySocialClient())
        post = NormalizedPost(
            post_id="1",
            author_handle="alice",
            author_display_name="Alice",
            parent_post_id=None,
            raw_content="",
            plain_text="@bot hi",
            llm_text="hi",
            command_text="hi",
            leading_mentions=["bot"],
            inline_mentions=[],
            expanded_urls=[],
            media=[],
            created_at=None,
        )
        response = AgentResponse(text="@bob hello", mentions=["bob", "bot"])
        self.assertEqual(publisher.build_status_text(response, post), "@bot @bob @bob hello")

    def test_publish_logs_outgoing_message(self):
        social = DummySocialClient()
        publisher = Publisher(social)
        post = NormalizedPost(
            post_id="1",
            author_handle="alice",
            author_display_name="Alice",
            parent_post_id=None,
            raw_content="",
            plain_text="@bot hi",
            llm_text="hi",
            command_text="hi",
            leading_mentions=["bot"],
            inline_mentions=[],
            expanded_urls=[],
            media=[],
            created_at=None,
        )
        response = AgentResponse(text="hello", mentions=[])

        async def run_test() -> None:
            with self.assertLogs("sns_agent.publisher", level="INFO") as captured:
                await publisher.publish(response, post)
            self.assertIn("sending message", captured.output[0])
            self.assertIn("@bot hello", captured.output[0])

        import asyncio

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
