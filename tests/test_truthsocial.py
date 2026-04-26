import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sns_agent.truthsocial import TruthSocialClient


class TruthSocialClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_status_expands_media_host_page_into_media_attachments(self):
        proxy = MagicMock()
        proxy.request_json = AsyncMock(
            side_effect=[
                {
                    "id": "1",
                    "content": '<p>look <a href="http://media.example/m/abc">media</a></p>',
                    "account": {"acct": "alice", "display_name": "Alice"},
                    "in_reply_to_id": None,
                    "media_attachments": [],
                    "created_at": "2026-04-18T00:00:00Z",
                },
                {
                    "page_id": "abc",
                    "public_url": "http://media.example/m/abc",
                    "og_image_url": "http://media.example/og/abc.png",
                    "items": [
                        {
                            "filename": "1-cat.png",
                            "mime_type": "image/png",
                            "kind": "image",
                            "url": "http://media.example/media/abc/1-cat.png",
                        }
                    ],
                },
            ]
        )

        with patch.dict("os.environ", {"MEDIA_HOST_API_URL": "http://media.example"}, clear=False):
            client = TruthSocialClient(proxy)
            post = await client.fetch_status("1")

        self.assertEqual(post.llm_text, "look media")
        self.assertEqual(len(post.media), 1)
        self.assertEqual(post.media[0].source, "media_host")
        self.assertEqual(post.media[0].url, "http://media.example/media/abc/1-cat.png")

    async def test_fetch_ancestor_chain_keeps_non_media_host_urls_in_text(self):
        proxy = MagicMock()
        proxy.request_json = AsyncMock(
            side_effect=[
                [
                    {
                        "id": "2",
                        "content": '<p>check <a href="https://example.com/x">this</a></p>',
                        "account": {"acct": "bob", "display_name": "Bob"},
                        "in_reply_to_id": None,
                        "media_attachments": [],
                        "created_at": "2026-04-18T00:00:00Z",
                    }
                ]
            ]
        )

        with patch.dict("os.environ", {"MEDIA_HOST_API_URL": "http://media.example"}, clear=False):
            client = TruthSocialClient(proxy)
            target = type(
                "Target",
                (),
                {
                    "post_id": "3",
                    "parent_post_id": "2",
                },
            )()
            chain = await client.fetch_ancestor_chain(target)

        self.assertEqual(chain[0].llm_text, "check this")
        self.assertEqual(chain[0].media, [])


if __name__ == "__main__":
    unittest.main()
