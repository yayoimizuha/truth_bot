import unittest

from sns_agent.normalizer import normalize_status


class NormalizerTests(unittest.TestCase):
    def test_html_is_normalized(self):
        payload = {
            "id": "1",
            "content": '<p><a class="mention" href="https://truthsocial.com/@bot">@bot</a> hello<br/>world <a href="https://example.com/x">x</a></p>',
            "account": {"acct": "alice", "display_name": "Alice"},
            "in_reply_to_id": None,
            "media_attachments": [],
            "created_at": "2026-04-18T00:00:00Z",
        }
        normalized = normalize_status(payload)
        self.assertEqual(normalized.leading_mentions, ["bot"])
        self.assertEqual(normalized.command_text, "hello\nworld x")
        self.assertIn("https://example.com/x", normalized.expanded_urls)


if __name__ == "__main__":
    unittest.main()
