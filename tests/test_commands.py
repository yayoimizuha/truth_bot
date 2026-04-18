import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

from sns_agent.commands import CommandParseError, parse_command, to_image_request
from sns_agent.media import ImageGenerator
from sns_agent.schemas import ImageGenerationRequest


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


class ImageGeneratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_backend_uses_openai_compatible_images_api_by_default(self):
        with (
            patch.dict(
                "os.environ",
                {
                    "IMAGE_BACKEND": "api",
                    "IMAGE_API_KEY": "test-key",
                    "IMAGE_API_URL": "https://example.invalid/v1/images/generations",
                    "IMAGE_MODEL": "test-model,backup-model",
                },
                clear=True,
            ),
            patch("sns_agent.media.httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = mock_client_cls.return_value
            response = MagicMock()
            response.json.return_value = {"data": [{"b64_json": "aGVsbG8="}]}
            response.raise_for_status.return_value = None
            mock_client.post = AsyncMock(return_value=response)

            generator = ImageGenerator()
            images = await generator.generate(ImageGenerationRequest(prompt="hi"))

            self.assertEqual(len(images), 1)
            self.assertEqual(images[0].content, b"hello")
            mock_client.post.assert_awaited_once()
            _, kwargs = mock_client.post.await_args
            self.assertEqual(kwargs["json"]["response_format"], "b64_json")
            self.assertEqual(kwargs["json"]["model"], "test-model")

    async def test_openrouter_backend_alias_uses_api_backend(self):
        with (
            patch.dict(
                "os.environ",
                {
                    "IMAGE_BACKEND": "openrouter",
                    "IMAGE_API_STYLE": "openrouter-chat",
                    "IMAGE_API_KEY": "test-key",
                    "IMAGE_MODEL": "test-model,backup-model",
                },
                clear=True,
            ),
            patch("sns_agent.media.httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = mock_client_cls.return_value
            response = MagicMock()
            response.json.return_value = {
                "choices": [
                    {
                        "message": {
                            "images": [{"image_url": {"url": "data:image/png;base64,aGVsbG8="}}],
                        }
                    }
                ]
            }
            response.raise_for_status.return_value = None
            mock_client.post = AsyncMock(return_value=response)

            generator = ImageGenerator()
            images = await generator.generate(ImageGenerationRequest(prompt="hi"))

            self.assertEqual(generator._backend, "api")
            self.assertEqual(len(images), 1)
            self.assertEqual(images[0].content, b"hello")

    async def test_local_backend_uses_first_image_model_as_default_and_resolves_file(self):
        with TemporaryDirectory() as temp_dir:
            model_dir = Path(temp_dir)
            model_file = model_dir / "sdxl.py"
            model_file.write_text(
                "from sns_agent.schemas import GeneratedImage\n"
                "\n"
                "def generate(request):\n"
                "    return [GeneratedImage(content=b'img', filename='sdxl.png')]\n",
                encoding="utf-8",
            )

            with patch.dict(
                "os.environ",
                {
                    "IMAGE_BACKEND": "stable-diffusion-cpp",
                    "IMAGE_MODEL": "sdxl,anything-v5",
                    "IMAGE_MODELS_DIR": str(model_dir),
                    "STABLE_DIFFUSION_CPP_BINARY": "sd",
                },
                clear=True,
            ), patch("sns_agent.media.httpx.AsyncClient"):
                generator = ImageGenerator()
                self.assertEqual(generator._resolve_local_model_name(None), "sdxl")
                self.assertEqual(generator._resolve_local_model_module_path("sdxl"), model_file)

    async def test_local_backend_rejects_model_not_in_image_model_allowlist(self):
        with TemporaryDirectory() as temp_dir:
            model_dir = Path(temp_dir)
            (model_dir / "sdxl.py").write_text("def generate(request):\n    return []\n", encoding="utf-8")

            with patch.dict(
                "os.environ",
                {
                    "IMAGE_BACKEND": "stable-diffusion-cpp",
                    "IMAGE_MODEL": "sdxl",
                    "IMAGE_MODELS_DIR": str(model_dir),
                },
                clear=True,
            ), patch("sns_agent.media.httpx.AsyncClient"):
                generator = ImageGenerator()
                with self.assertRaises(RuntimeError):
                    generator._resolve_local_model_name("other-model")

    async def test_local_backend_loads_module_and_runs_cleanup(self):
        with TemporaryDirectory() as temp_dir:
            model_dir = Path(temp_dir)
            model_file = model_dir / "sdxl.py"
            marker_file = model_dir / "cleanup.txt"
            model_file.write_text(
                "from pathlib import Path\n"
                "from sns_agent.schemas import GeneratedImage\n"
                "\n"
                "def generate(request):\n"
                "    return [GeneratedImage(content=b'img', filename='sdxl.png')]\n"
                "\n"
                "def cleanup():\n"
                f"    Path(r'{marker_file}').write_text('done', encoding='utf-8')\n",
                encoding="utf-8",
            )

            with patch.dict(
                "os.environ",
                {
                    "IMAGE_BACKEND": "stable-diffusion-cpp",
                    "IMAGE_MODEL": "sdxl",
                    "IMAGE_MODELS_DIR": str(model_dir),
                },
                clear=True,
            ), patch("sns_agent.media.httpx.AsyncClient"):
                generator = ImageGenerator()
                images = await generator.generate(ImageGenerationRequest(prompt="hi"))

            self.assertEqual(len(images), 1)
            self.assertEqual(images[0].source, "sdxl")
            self.assertEqual(marker_file.read_text(encoding="utf-8"), "done")

    async def test_image_generation_is_disabled_without_image_model(self):
        with (
            patch.dict(
                "os.environ",
                {
                    "IMAGE_BACKEND": "api",
                    "IMAGE_API_KEY": "test-key",
                },
                clear=True,
            ),
            patch("sns_agent.media.httpx.AsyncClient"),
        ):
            generator = ImageGenerator()
            with self.assertRaises(RuntimeError):
                await generator.generate(ImageGenerationRequest(prompt="hi"))


if __name__ == "__main__":
    unittest.main()
