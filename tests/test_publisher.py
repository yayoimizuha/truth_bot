import unittest

from sns_agent.publisher import Publisher
from sns_agent.schemas import AgentResponse, NormalizedPost


class DummySocialClient:
    pass


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


if __name__ == "__main__":
    unittest.main()
