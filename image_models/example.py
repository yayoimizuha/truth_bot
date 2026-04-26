from __future__ import annotations

import gc

from image_models import coerce_generated_images
from sns_agent.schemas import GeneratedImage, ImageGenerationRequest


def generate(request: ImageGenerationRequest) -> list[GeneratedImage]:
    raise NotImplementedError(
        "Implement generate(request) and return list[GeneratedImage] or list[bytes]. "
        "Use request.prompt / request.size / request.steps / request.seed as needed."
    )


def cleanup() -> None:
    gc.collect()
