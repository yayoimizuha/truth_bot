import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from image_models import coerce_generated_images
from image_models._sd_server import GPUCandidate, SDServerImageGenerator, SDServerModelConfig, build_argument_list
from sns_agent.schemas import GeneratedImage, ImageGenerationRequest, ReferenceImage


class ImageModelsTests(unittest.TestCase):
    def test_coerce_generated_images_accepts_bytes(self):
        images = coerce_generated_images([b"a", b"b"], source="dummy")
        self.assertEqual(len(images), 2)
        self.assertEqual(images[0].filename, "dummy-1.png")
        self.assertEqual(images[0].source, "dummy")

    def test_sd_server_gpu_discovery_requires_pynvml(self):
        with patch("builtins.__import__") as import_mock:
            real_import = __import__

            def side_effect(name, *args, **kwargs):
                if name == "pynvml":
                    raise ImportError("pynvml not available")
                return real_import(name, *args, **kwargs)

            import_mock.side_effect = side_effect
            with self.assertRaisesRegex(RuntimeError, "pynvml is required"):
                SDServerImageGenerator._discover_gpus()

    def test_sd_server_gpu_availability_thresholds(self):
        busy = GPUCandidate(
            index=0,
            name="GPU-0",
            memory_total_mb=32 * 1024,
            memory_free_mb=1024,
            memory_used_mb=4096,
            gpu_utilization_percent=11,
            memory_utilization_percent=60,
        )
        free = GPUCandidate(
            index=1,
            name="GPU-1",
            memory_total_mb=32 * 1024,
            memory_free_mb=16 * 1024,
            memory_used_mb=512,
            gpu_utilization_percent=5,
            memory_utilization_percent=4,
        )
        self.assertFalse(SDServerImageGenerator._is_gpu_available(busy))
        self.assertTrue(SDServerImageGenerator._is_gpu_available(free))

    def test_sd_server_builds_prompt_embedded_extra_args_for_openai_route(self):
        generator = SDServerImageGenerator(SDServerModelConfig(model_name="sdxl", arguments=["--model", "dummy"]))
        payload = generator._build_openai_image_payload(
            ImageGenerationRequest(
                prompt="cat",
                size="1024x1024",
                count=2,
                negative="blurry",
                steps=30,
                cfg_scale=6.5,
                flow_shift=3.0,
                seed=123,
                sampler="euler",
            )
        )
        self.assertEqual(payload["model"], "sdxl")
        self.assertEqual(payload["n"], 2)
        self.assertEqual(payload["size"], "1024x1024")
        prompt = payload["prompt"]
        self.assertIn("<sd_cpp_extra_args>", prompt)
        self.assertIn('"negative_prompt": "blurry"', prompt)
        self.assertIn('"sample_steps": 30', prompt)
        self.assertIn('"txt_cfg": 6.5', prompt)
        self.assertIn('"flow_shift": 3.0', prompt)
        self.assertIn('"seed": 123', prompt)
        self.assertIn('"sample_method": "euler"', prompt)

    def test_sd_server_builds_openai_edit_payload(self):
        generator = SDServerImageGenerator(SDServerModelConfig(model_name="qwen_image_edit", arguments=["--model", "dummy"]))
        request = ImageGenerationRequest(
            prompt="add flowers",
            count=1,
            steps=8,
            cfg_scale=2.5,
            flow_shift=3.0,
            sampler="euler",
            reference_images=[
                ReferenceImage(
                    content=b"png-bytes",
                    filename="cat.png",
                    mime_type="image/png",
                )
            ],
        )
        data = generator._build_openai_edit_data(
            request
        )
        files = generator._build_reference_image_files(request)
        self.assertEqual(data["n"], "1")
        self.assertEqual(data["output_format"], "png")
        self.assertIn("<sd_cpp_extra_args>", data["prompt"])
        self.assertEqual(files[0][0], "image[]")
        self.assertEqual(files[0][1][0], "cat.png")
        self.assertEqual(files[0][1][1], b"png-bytes")

    def test_sd_server_requires_reference_images_once_for_edit_only_models(self):
        generator = SDServerImageGenerator(
            SDServerModelConfig(model_name="qwen_image_edit", arguments=["--model", "dummy"], requires_reference_images=True)
        )
        with self.assertRaisesRegex(RuntimeError, "requires reference images"):
            generator.generate(ImageGenerationRequest(prompt="add flowers"))

    def test_sd_server_decodes_generation_response_via_shared_path(self):
        generator = SDServerImageGenerator(SDServerModelConfig(model_name="sdxl", arguments=["--model", "dummy"]))
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"data": [{"b64_json": "aGVsbG8="}]}

        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.post.return_value = response

        with patch("image_models._sd_server.httpx.Client", return_value=client):
            images = generator._request_images(7860, ImageGenerationRequest(prompt="cat"))

        self.assertEqual(images[0].content, b"hello")
        self.assertEqual(images[0].filename, "sdxl-1.png")

    def test_sd_server_decodes_edit_response_via_shared_path(self):
        generator = SDServerImageGenerator(SDServerModelConfig(model_name="qwen_image_edit", arguments=["--model", "dummy"]))
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"data": [{"b64_json": "aGVsbG8="}]}

        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.post.return_value = response

        with patch("image_models._sd_server.httpx.Client", return_value=client):
            images = generator._request_images(
                7860,
                ImageGenerationRequest(
                    prompt="add flowers",
                    reference_images=[
                        ReferenceImage(
                            content=b"png-bytes",
                            filename="cat.png",
                            mime_type="image/png",
                        )
                    ],
                ),
            )

        self.assertEqual(images[0].content, b"hello")
        _, kwargs = client.post.call_args
        self.assertIn("data", kwargs)
        self.assertIn("files", kwargs)

    def test_sd_server_binary_search_prefers_known_paths(self):
        candidate = "/usr/local/bin/sd-server"
        with (
            patch("image_models._sd_server.Path.is_file", return_value=False),
            patch("image_models._sd_server.shutil.which", return_value=candidate),
        ):
            found = SDServerImageGenerator._find_sd_server_binary()
        self.assertEqual(found, Path(candidate))

    def test_build_argument_list_keeps_only_configured_paths(self):
        arguments = build_argument_list(
            full_model=Path("/models/sdxl.safetensors"),
            pairs=(
                ("--clip_l", Path("/models/clip_l.safetensors")),
                ("--clip_g", None),
                ("--vae", Path("/models/vae.safetensors")),
            ),
        )
        self.assertEqual(
            arguments,
            [
                "--model",
                "/models/sdxl.safetensors",
                "--clip_l",
                "/models/clip_l.safetensors",
                "--vae",
                "/models/vae.safetensors",
            ],
        )


if __name__ == "__main__":
    unittest.main()
