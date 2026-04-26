import unittest
from unittest.mock import patch

from pydantic_ai.messages import ImageUrl

from sns_agent.responder import LLMResponder
from sns_agent.schemas import MediaAttachment, NormalizedPost


class ResponderPromptTests(unittest.TestCase):
    def test_build_prompt_embeds_media_host_images_as_image_parts(self):
        post = NormalizedPost(
            post_id="1",
            author_handle="alice",
            author_display_name="Alice",
            parent_post_id=None,
            raw_content="",
            plain_text="see this",
            llm_text="see this",
            command_text="see this",
            leading_mentions=[],
            inline_mentions=[],
            expanded_urls=[],
            media=[
                MediaAttachment(
                    media_id="media-host:abc:1",
                    media_type="image",
                    url="http://media.example/media/abc/1-cat.png",
                    mime_type="image/png",
                    source="media_host",
                )
            ],
            created_at=None,
        )

        with patch.dict("os.environ", {"TRUTHSOCIAL_USERNAME": "bot"}, clear=False):
            prompt = LLMResponder._build_prompt([post], post)

        self.assertTrue(any(isinstance(part, ImageUrl) for part in prompt))
        image_part = next(part for part in prompt if isinstance(part, ImageUrl))
        self.assertEqual(image_part.url, "http://media.example/media/abc/1-cat.png")


if __name__ == "__main__":
    unittest.main()
