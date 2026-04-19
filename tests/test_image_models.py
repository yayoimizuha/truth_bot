import unittest
from unittest.mock import patch

from image_models import GPUDeviceStats, LocalImageModelBase, coerce_generated_images
from sns_agent.schemas import GeneratedImage, ImageGenerationRequest


class DummyModel(LocalImageModelBase):
    def __init__(self):
        super().__init__(model_name="dummy")

    def generate(self, request: ImageGenerationRequest):
        return [GeneratedImage(content=b"img")]


class ImageModelsTests(unittest.TestCase):
    def test_coerce_generated_images_accepts_bytes(self):
        images = coerce_generated_images([b"a", b"b"], source="dummy")
        self.assertEqual(len(images), 2)
        self.assertEqual(images[0].filename, "dummy-1.png")
        self.assertEqual(images[0].source, "dummy")

    def test_format_gpu_stats(self):
        model = DummyModel()
        text = model.format_gpu_stats(
            [
                GPUDeviceStats(
                    device_index=0,
                    name="GPU-0",
                    memory_total_bytes=1024 * 1024 * 100,
                    memory_used_bytes=1024 * 1024 * 25,
                    gpu_utilization_percent=50,
                    memory_utilization_percent=25,
                )
            ]
        )
        self.assertIn("GPU-0", text)
        self.assertIn("25/100 MiB", text)
        self.assertIn("gpu=50%", text)

    def test_snapshot_with_pynvml_returns_empty_when_unavailable(self):
        with patch("builtins.__import__") as import_mock:
            real_import = __import__

            def side_effect(name, *args, **kwargs):
                if name == "pynvml":
                    raise ImportError("pynvml not available")
                return real_import(name, *args, **kwargs)

            import_mock.side_effect = side_effect
            self.assertEqual(DummyModel._snapshot_with_pynvml(), [])


if __name__ == "__main__":
    unittest.main()
