from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

from dotenv import find_dotenv, load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sns_agent.media import ImageGenerator
from sns_agent.schemas import ImageGenerationRequest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate one or more images using the configured backend.")
    parser.add_argument("prompt", help="Prompt to send to the image backend.")
    parser.add_argument("--model", default=None, help="Image model name. Defaults to IMAGE_MODEL first entry.")
    parser.add_argument("--size", default=None, help="Image size such as 1024x1024.")
    parser.add_argument("--count", type=int, default=1, help="Number of images to request.")
    parser.add_argument("--negative", default=None, help="Negative prompt, if supported by the backend.")
    parser.add_argument(
        "--output-dir",
        default="generated_test_images",
        help="Directory where generated files will be written.",
    )
    return parser.parse_args()


async def main() -> None:
    load_dotenv(find_dotenv())
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    generator = ImageGenerator()
    try:
        images = await generator.generate(
            ImageGenerationRequest(
                prompt=args.prompt,
                model=args.model,
                size=args.size,
                count=args.count,
                negative=args.negative,
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
