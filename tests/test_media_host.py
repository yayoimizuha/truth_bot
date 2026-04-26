import unittest
from unittest.mock import AsyncMock, patch

import httpx

from sns_agent.media_host import MediaHostClient
from sns_agent.schemas import GeneratedImage


class MediaHostClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_page_uses_basic_auth_when_password_is_configured(self):
        response = httpx.Response(
            200,
            json={"page_id": "abc", "public_url": "http://media.example/m/abc"},
            request=httpx.Request("POST", "http://media.example/media"),
        )

        with patch.dict(
            "os.environ",
            {"MEDIA_HOST_API_URL": "http://media.example", "MEDIA_HOST_UPLOAD_PASSWORD": "secret"},
            clear=False,
        ):
            client = MediaHostClient()
            client._client.post = AsyncMock(return_value=response)
            self.addAsyncCleanup(client.aclose)

            await client.create_page([GeneratedImage(content=b"png")])

        auth = client._client.post.await_args.kwargs["auth"]
        self.assertEqual(auth, ("upload", "secret"))


if __name__ == "__main__":
    unittest.main()
