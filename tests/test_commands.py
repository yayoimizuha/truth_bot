import unittest

from sns_agent.commands import CommandParseError, parse_command, to_image_request


class CommandTests(unittest.TestCase):
    def test_parse_image_command(self):
        command = parse_command("/image\ncount: 2\nsize: 1024x1024\n\nhello")
        self.assertIsNotNone(command)
        request = to_image_request(command)
        self.assertEqual(request.count, 2)
        self.assertEqual(request.size, "1024x1024")
        self.assertEqual(request.prompt, "hello")

    def test_parse_invalid_header(self):
        with self.assertRaises(CommandParseError):
            parse_command("/image\ncount=2\n\nhello")

    def test_reject_too_many_images(self):
        command = parse_command("/image\ncount: 5\n\nhello")
        with self.assertRaises(CommandParseError):
            to_image_request(command)


if __name__ == "__main__":
    unittest.main()
