from __future__ import annotations

from pathlib import Path

from image_models._sd_server import SDServerImageGenerator, SDServerModelConfig, load_sd_server_model_config
from sns_agent.schemas import GeneratedImage, ImageGenerationRequest


def _validate_sdxl_paths(paths: dict[str, Path | None]) -> None:
    model = paths["MODEL_PATH"]
    clip_l = paths["CLIP_L_PATH"]
    clip_g = paths["CLIP_G_PATH"]
    if model is None and (clip_l is None or clip_g is None):
        raise RuntimeError("sdxl requires MODEL_PATH or both CLIP_L_PATH and CLIP_G_PATH")


def _build_model_config() -> SDServerModelConfig:
    config = load_sd_server_model_config(
        env_name="sdxl",
        model_name="sdxl",
        full_model_env="MODEL_PATH",
        path_flags=(
            ("--clip_l", "CLIP_L_PATH", False),
            ("--clip_g", "CLIP_G_PATH", False),
            ("--vae", "VAE_PATH", False),
        ),
        validate=_validate_sdxl_paths,
    )
    return config


def generate(request: ImageGenerationRequest) -> list[GeneratedImage]:
    generator = SDServerImageGenerator(_build_model_config())
    return generator.generate(request)
