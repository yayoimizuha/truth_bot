from __future__ import annotations

from image_models import LocalImageModelBase, coerce_generated_images
from sns_agent.schemas import GeneratedImage, ImageGenerationRequest


class ExampleImageModel(LocalImageModelBase):
    def __init__(self):
        super().__init__(model_name="example")

    def generate(self, request: ImageGenerationRequest) -> list[GeneratedImage]:
        before = self.snapshot_gpu_stats()
        try:
            raise NotImplementedError(
                "Implement generate(request) with stable-diffusion-cpp-python "
                "or a local inference server. Use request.prompt / request.size / "
                "request.steps / request.seed as needed."
            )
        finally:
            after = self.snapshot_gpu_stats()
            if before or after:
                print(f"[{self.model_name}] GPU before: {self.format_gpu_stats(before)}")
                print(f"[{self.model_name}] GPU after: {self.format_gpu_stats(after)}")

    def cleanup(self) -> None:
        super().cleanup()


_MODEL = ExampleImageModel()


def generate(request: ImageGenerationRequest) -> list[GeneratedImage]:
    raw_images = _MODEL.generate(request)
    return coerce_generated_images(raw_images, source=_MODEL.model_name)


def cleanup() -> None:
    _MODEL.cleanup()
