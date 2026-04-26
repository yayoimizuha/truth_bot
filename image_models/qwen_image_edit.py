from __future__ import annotations

from image_models._sd_server import SDServerImageGenerator, SDServerModelConfig, load_sd_server_model_config
from sns_agent.schemas import GeneratedImage, ImageGenerationRequest


def _build_model_config() -> SDServerModelConfig:
    return load_sd_server_model_config(
        env_name="qwen_image_edit",
        model_name="qwen_image_edit",
        path_flags=(
            ("--diffusion-model", "DIFFUSION_MODEL_PATH", True),
            ("--llm", "LLM_PATH", True),
            ("--vae", "VAE_PATH", True),
            ("--llm_vision", "LLM_VISION_PATH", True),
        ),
        extra_arguments=("--diffusion-fa", "--qwen-image-zero-cond-t"),
        requires_reference_images=True,
    )


def generate(request: ImageGenerationRequest) -> list[GeneratedImage]:
    generator = SDServerImageGenerator(_build_model_config())
    return generator.generate(request)
