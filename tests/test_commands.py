import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

from sns_agent.commands import CommandParseError, parse_command, to_image_request
from sns_agent.media import ImageGenerator
from sns_agent.schemas import ImageGenerationRequest


class CommandTests(unittest.TestCase):
    def test_parse_image_gen_command(self):
        command = parse_command("/image_gen\ncount: 2\nsize: 1024x1024\n\nhello")
        self.assertIsNotNone(command)
        request = to_image_request(command)
        self.assertEqual(request.count, 2)
        self.assertEqual(request.size, "1024x1024")
        self.assertEqual(request.prompt, "hello")

    def test_parse_image_gen_command_without_blank_line_before_prompt(self):
        command = parse_command("/image_gen\nmodel: gpt-image-1-mini\nhello")
        self.assertIsNotNone(command)
        request = to_image_request(command)
        self.assertEqual(request.model, "gpt-image-1-mini")
        self.assertEqual(request.prompt, "hello")

    def test_parse_image_edit_command(self):
        command = parse_command(
            "/image_edit\nmodel: qwen_image_edit\nflow_shift: 3\n\nadd flowers"
        )
        self.assertIsNotNone(command)
        request = to_image_request(command)
        self.assertEqual(request.model, "qwen_image_edit")
        self.assertEqual(request.flow_shift, 3.0)
        self.assertEqual(request.prompt, "add flowers")

    def test_unknown_header_is_still_rejected(self):
        command = parse_command("/image_gen\nfoo: 2\n\nhello")
        self.assertIsNotNone(command)
        with self.assertRaises(CommandParseError):
            to_image_request(command)

    def test_reject_too_many_images(self):
        command = parse_command("/image_gen\ncount: 5\n\nhello")
        with self.assertRaises(CommandParseError):
            to_image_request(command)


class ImageGeneratorTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _mock_async_client(mock_client_cls):
        mock_client = mock_client_cls.return_value
        mock_client.aclose = AsyncMock()
        return mock_client

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
            patch("sns_agent.media.AsyncOpenAI") as mock_openai_cls,
        ):
            self._mock_async_client(mock_client_cls)
            mock_openai = mock_openai_cls.return_value
            response = MagicMock()
            response.data = [MagicMock(b64_json="aGVsbG8=", url=None)]
            mock_openai.images.generate = AsyncMock(return_value=response)

            generator = ImageGenerator()
            images = await generator.generate(ImageGenerationRequest(prompt="hi"))
            await generator.aclose()

            self.assertEqual(len(images), 1)
            self.assertEqual(images[0].content, b"hello")
            mock_openai.images.generate.assert_awaited_once()
            _, kwargs = mock_openai.images.generate.await_args
            self.assertEqual(kwargs["model"], "test-model")
            self.assertNotIn("response_format", kwargs)
            self.assertEqual(kwargs["extra_body"], None)

    async def test_api_backend_surfaces_error_response_body(self):
        with (
            patch.dict(
                "os.environ",
                {
                    "IMAGE_BACKEND": "api",
                    "IMAGE_API_KEY": "test-key",
                    "IMAGE_API_URL": "https://example.invalid/v1/images/generations",
                    "IMAGE_MODEL": "test-model",
                },
                clear=True,
            ),
            patch("sns_agent.media.httpx.AsyncClient") as mock_client_cls,
            patch("sns_agent.media.AsyncOpenAI") as mock_openai_cls,
        ):
            self._mock_async_client(mock_client_cls)
            mock_openai = mock_openai_cls.return_value
            response = MagicMock()
            response.text = '{"error":{"message":"bad request"}}'

            class FakeStatusError(Exception):
                def __init__(self):
                    super().__init__("bad request")
                    self.status_code = 400
                    self.response = response

            error = FakeStatusError()
            mock_openai.images.generate = AsyncMock(side_effect=error)

            generator = ImageGenerator()
            with patch("sns_agent.media.ImageGenerator._is_retryable_openai_error", return_value=False):
                with self.assertRaisesRegex(RuntimeError, "400.*bad request"):
                    await generator.generate(ImageGenerationRequest(prompt="hi"))
            await generator.aclose()

    async def test_api_backend_retries_retryable_errors_with_backoff(self):
        with (
            patch.dict(
                "os.environ",
                {
                    "IMAGE_BACKEND": "api",
                    "IMAGE_API_KEY": "test-key",
                    "IMAGE_MODEL": "test-model",
                    "IMAGE_API_MAX_RETRIES": "2",
                    "IMAGE_API_RETRY_BASE_SECONDS": "0.5",
                    "IMAGE_API_RETRY_MAX_SECONDS": "1.0",
                },
                clear=True,
            ),
            patch("sns_agent.media.httpx.AsyncClient") as mock_client_cls,
            patch("sns_agent.media.AsyncOpenAI") as mock_openai_cls,
            patch("sns_agent.media.asyncio.sleep", new_callable=AsyncMock) as sleep_mock,
        ):
            self._mock_async_client(mock_client_cls)
            mock_openai = mock_openai_cls.return_value
            retryable_error = RuntimeError("transient")
            success = MagicMock()
            success.data = [MagicMock(b64_json="aGVsbG8=", url=None)]
            mock_openai.images.generate = AsyncMock(side_effect=[retryable_error, success])

            generator = ImageGenerator()
            with patch(
                "sns_agent.media.ImageGenerator._is_retryable_openai_error",
                side_effect=lambda exc: exc is retryable_error,
            ):
                images = await generator.generate(ImageGenerationRequest(prompt="hi"))
            await generator.aclose()

            self.assertEqual(len(images), 1)
            self.assertEqual(mock_openai.images.generate.await_count, 2)
            sleep_mock.assert_awaited_once_with(0.5)

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
            mock_client = self._mock_async_client(mock_client_cls)
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
            await generator.aclose()

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
            ), patch("sns_agent.media.httpx.AsyncClient") as mock_client_cls:
                self._mock_async_client(mock_client_cls)
                generator = ImageGenerator()
                self.assertEqual(generator._resolve_local_model_name(None), "sdxl")
                self.assertEqual(generator._resolve_local_model_module_path("sdxl"), model_file)
                await generator.aclose()

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
            ), patch("sns_agent.media.httpx.AsyncClient") as mock_client_cls:
                self._mock_async_client(mock_client_cls)
                generator = ImageGenerator()
                with self.assertRaises(RuntimeError):
                    generator._resolve_local_model_name("other-model")
                await generator.aclose()

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
            ), patch("sns_agent.media.httpx.AsyncClient") as mock_client_cls:
                self._mock_async_client(mock_client_cls)
                generator = ImageGenerator()
                images = await generator.generate(ImageGenerationRequest(prompt="hi"))
                await generator.aclose()

            self.assertEqual(len(images), 1)
            self.assertEqual(images[0].source, "sdxl")
            self.assertEqual(marker_file.read_text(encoding="utf-8"), "done")

    async def test_local_backend_teardown_runs_cleanup_and_unloads_module_once(self):
        with TemporaryDirectory() as temp_dir:
            model_dir = Path(temp_dir)
            model_file = model_dir / "sdxl.py"
            model_file.write_text(
                "from pathlib import Path\n"
                "COUNTER = 0\n"
                "def generate(request):\n"
                "    return [b'img']\n"
                "def cleanup():\n"
                "    global COUNTER\n"
                "    COUNTER += 1\n",
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
            ), patch("sns_agent.media.httpx.AsyncClient") as mock_client_cls:
                self._mock_async_client(mock_client_cls)
                generator = ImageGenerator()
                module = generator._load_local_model_module("sdxl", model_file)
                module_name = module.__name__
                with (
                    patch("sns_agent.media.gc.collect") as gc_collect,
                    patch.object(generator, "_best_effort_release_vram") as release_vram,
                ):
                    await generator._teardown_local_model_module(module)
                self.assertEqual(module.COUNTER, 1)
                self.assertNotIn(module_name, __import__("sys").modules)
                gc_collect.assert_called_once()
                release_vram.assert_called_once()
                await generator.aclose()

    async def test_local_backend_loads_same_model_module_with_unique_names(self):
        with TemporaryDirectory() as temp_dir:
            model_dir = Path(temp_dir)
            model_file = model_dir / "sdxl.py"
            model_file.write_text(
                "def generate(request):\n"
                "    return [b'img']\n",
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
            ), patch("sns_agent.media.httpx.AsyncClient") as mock_client_cls:
                self._mock_async_client(mock_client_cls)
                generator = ImageGenerator()
                module_one = generator._load_local_model_module("sdxl", model_file)
                module_two = generator._load_local_model_module("sdxl", model_file)
                try:
                    self.assertNotEqual(module_one.__name__, module_two.__name__)
                finally:
                    generator._unload_local_model_module(module_one)
                    generator._unload_local_model_module(module_two)
                    await generator.aclose()

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
            patch("sns_agent.media.httpx.AsyncClient") as mock_client_cls,
        ):
            self._mock_async_client(mock_client_cls)
            generator = ImageGenerator()
            with self.assertRaises(RuntimeError):
                await generator.generate(ImageGenerationRequest(prompt="hi"))
            await generator.aclose()


if __name__ == "__main__":
    unittest.main()
