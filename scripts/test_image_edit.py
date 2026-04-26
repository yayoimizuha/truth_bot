from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import sys

from dotenv import find_dotenv, load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sns_agent.media import ImageGenerator
from sns_agent.schemas import ImageGenerationRequest, ReferenceImage


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a local image edit request against the configured backend.")
    parser.add_argument("prompt", help="Edit instruction prompt.")
    parser.add_argument("--model", default="qwen_image_edit", help="Image model name.")
    parser.add_argument("--size", default=None, help="Image size such as 1024x1024.")
    parser.add_argument("--steps", type=int, default=20, help="Sampling steps.")
    parser.add_argument("--cfg-scale", type=float, default=2.5, help="CFG scale.")
    parser.add_argument("--flow-shift", type=float, default=3.0, help="Flow shift.")
    parser.add_argument("--sampler", default="euler", help="Sampling method.")
    parser.add_argument("--count", type=int, default=1, help="Number of images to request.")
    parser.add_argument(
        "--output-dir",
        default="generated_test_images",
        help="Directory where generated files will be written.",
    )
    parser.add_argument(
        "--diffusion-model-path",
        default=str(Path.home() / "models/Qwen-Image-Edit-2511/qwen-image-edit-2511-Q4_K_M.gguf"),
        help="Path to the Qwen-Image-Edit diffusion model.",
    )
    parser.add_argument(
        "--llm-path",
        default=str(Path.home() / "models/Qwen-Image-Edit-2511/Qwen2.5-VL-7B-Instruct-UD-Q5_K_XL.gguf"),
        help="Path to the Qwen2.5-VL GGUF.",
    )
    parser.add_argument(
        "--vae-path",
        default=str(Path.home() / "models/Qwen-Image-Edit-2511/split_files/vae/qwen_image_vae.safetensors"),
        help="Path to the VAE safetensors.",
    )
    parser.add_argument(
        "--llm-vision-path",
        default=str(Path.home() / "models/Qwen-Image-Edit-2511/mmproj-F16.gguf"),
        help="Path to the LLM vision projector GGUF.",
    )
    return parser.parse_args()


async def main() -> None:
    load_dotenv(find_dotenv())
    args = parse_args()
    reference_image = Path.home() / "models/Qwen-Image-Edit-2511/cat.png"
    if not reference_image.is_file():
        raise SystemExit(f"reference image not found: {reference_image}")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    os.environ["IMAGE_BACKEND"] = "stable-diffusion-cpp"
    os.environ["IMAGE_MODEL"] = args.model
    os.environ["IMAGE_MODELS_DIR"] = str(Path(__file__).resolve().parents[1] / "image_models")
    os.environ["DIFFUSION_MODEL_PATH"] = str(Path(args.diffusion_model_path).expanduser())
    os.environ["LLM_PATH"] = str(Path(args.llm_path).expanduser())
    os.environ["VAE_PATH"] = str(Path(args.vae_path).expanduser())
    os.environ["LLM_VISION_PATH"] = str(Path(args.llm_vision_path).expanduser())

    generator = ImageGenerator()
    try:
        images = await generator.generate(
            ImageGenerationRequest(
                prompt=args.prompt,
                model=args.model,
                size=args.size,
                count=args.count,
                steps=args.steps,
                cfg_scale=args.cfg_scale,
                flow_shift=args.flow_shift,
                sampler=args.sampler,
                reference_images=[
                    ReferenceImage(
                        content=reference_image.read_bytes(),
                        filename=reference_image.name,
                        mime_type="image/png",
                        source_url=str(reference_image),
                    )
                ],
            )
        )
    finally:
        await generator.aclose()

    for index, image in enumerate(images, start=1):
        output_path = output_dir / image.filename
        output_path.write_bytes(image.content)
        print(f"[{index}] wrote {output_path} source={image.source} bytes={len(image.content)}")


if __name__ == "__main__":
    asyncio.run(main())
