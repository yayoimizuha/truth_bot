from __future__ import annotations

import argparse
import asyncio
from contextvars import ContextVar
import os
from pathlib import Path
import sys
import time
from typing import Any

from dotenv import find_dotenv, load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from image_models._sd_server import SDServerImageGenerator
from sns_agent.gpu_tasks import GPUTaskLimiter
from sns_agent.media import ImageGenerator
from sns_agent.schemas import ImageGenerationRequest


REQUEST_ID: ContextVar[str] = ContextVar("REQUEST_ID", default="unknown")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a concurrent local image-generation load test.")
    parser.add_argument(
        "--model-path",
        default=str(Path.home() / "models/unsloth/Z-Image-Turbo-GGUF/z-image-turbo-Q6_K.gguf"),
        help="Path to the temporary local model file.",
    )
    parser.add_argument(
        "--llm-path",
        default=str(Path.home() / "models/Z-Image-Turbo/Qwen3-4B-Instruct-2507-Q8_0.gguf"),
        help="Path to the local LLM GGUF file.",
    )
    parser.add_argument(
        "--vae-path",
        default=str(Path.home() / "models/Z-Image-Turbo/vae/diffusion_pytorch_model.safetensors"),
        help="Path to the local VAE weights.",
    )
    parser.add_argument("--requests", type=int, default=6, help="Number of concurrent requests to launch.")
    parser.add_argument("--gpu-limit", type=int, default=3, help="GPU task concurrency limit.")
    parser.add_argument("--prompt", default="test prompt", help="Prompt text to send to the model.")
    parser.add_argument("--size", default="1024x1024", help="Requested image size.")
    parser.add_argument("--count", type=int, default=1, help="Requested image count per call.")
    parser.add_argument("--steps", type=int, default=8, help="Sampling steps for Z-Image Turbo.")
    parser.add_argument("--cfg-scale", type=float, default=1.0, help="CFG scale for Z-Image Turbo.")
    return parser.parse_args()


class LoggingLimiter(GPUTaskLimiter):
    async def run(self, task_factory):  # type: ignore[override]
        request_id = REQUEST_ID.get()
        queued_at = time.monotonic()
        print(f"[{queued_at:.3f}] request={request_id} waiting_for_gpu_slot", flush=True)
        async with self._semaphore:  # type: ignore[attr-defined]
            acquired_at = time.monotonic()
            print(
                f"[{acquired_at:.3f}] request={request_id} acquired_gpu_slot "
                f"after={acquired_at - queued_at:.3f}s",
                flush=True,
            )
            try:
                return await task_factory()
            finally:
                released_at = time.monotonic()
                print(
                    f"[{released_at:.3f}] request={request_id} released_gpu_slot "
                    f"held_for={released_at - acquired_at:.3f}s",
                    flush=True,
                )


def _patch_sd_server_logging() -> tuple[Any, Any]:
    original_acquire = SDServerImageGenerator._acquire_gpu
    original_release = SDServerImageGenerator._release_gpu

    def wrapped_acquire(self):
        request_id = REQUEST_ID.get()
        queued_at = time.monotonic()
        print(
            f"[{queued_at:.3f}] request={request_id} waiting_for_physical_gpu",
            flush=True,
        )
        gpu_index = original_acquire(self)
        acquired_at = time.monotonic()
        print(
            f"[{acquired_at:.3f}] request={request_id} reserved_gpu={gpu_index} "
            f"after={acquired_at - queued_at:.3f}s",
            flush=True,
        )
        return gpu_index

    def wrapped_release(gpu_index: int) -> None:
        request_id = REQUEST_ID.get()
        released_at = time.monotonic()
        print(
            f"[{released_at:.3f}] request={request_id} releasing_gpu={gpu_index}",
            flush=True,
        )
        original_release(gpu_index)

    SDServerImageGenerator._acquire_gpu = wrapped_acquire  # type: ignore[method-assign]
    SDServerImageGenerator._release_gpu = staticmethod(wrapped_release)  # type: ignore[method-assign]
    return original_acquire, original_release


def _restore_sd_server_logging(original_acquire: Any, original_release: Any) -> None:
    SDServerImageGenerator._acquire_gpu = original_acquire  # type: ignore[method-assign]
    SDServerImageGenerator._release_gpu = staticmethod(original_release)  # type: ignore[method-assign]


async def _run_one(
    generator: ImageGenerator,
    index: int,
    prompt: str,
    size: str,
    count: int,
    steps: int,
    cfg_scale: float,
):
    request_id = f"job-{index}"
    token = REQUEST_ID.set(request_id)
    started_at = time.monotonic()
    print(f"[{started_at:.3f}] request={request_id} submit", flush=True)
    try:
        images = await generator.generate(
            ImageGenerationRequest(
                prompt=f"{prompt} [{request_id}]",
                size=size,
                count=count,
                steps=steps,
                cfg_scale=cfg_scale,
            )
        )
        finished_at = time.monotonic()
        print(
            f"[{finished_at:.3f}] request={request_id} done images={len(images)} "
            f"elapsed={finished_at - started_at:.3f}s",
            flush=True,
        )
        return request_id, images
    finally:
        REQUEST_ID.reset(token)


async def main() -> None:
    load_dotenv(find_dotenv())
    args = parse_args()
    model_path = Path(args.model_path).expanduser()
    llm_path = Path(args.llm_path).expanduser()
    vae_path = Path(args.vae_path).expanduser()
    if not model_path.is_file():
        raise SystemExit(f"model file not found: {model_path}")
    if not llm_path.is_file():
        raise SystemExit(f"llm file not found: {llm_path}")
    if not vae_path.is_file():
        raise SystemExit(f"vae file not found: {vae_path}")

    os.environ["IMAGE_BACKEND"] = "stable-diffusion-cpp"
    os.environ["IMAGE_MODEL"] = "zimage_turbo"
    os.environ["IMAGE_MODELS_DIR"] = str(Path(__file__).resolve().parents[1] / "image_models")
    os.environ["DIFFUSION_MODEL_PATH"] = str(model_path)
    os.environ["LLM_PATH"] = str(llm_path)
    os.environ["VAE_PATH"] = str(vae_path)

    original_acquire, original_release = _patch_sd_server_logging()
    limiter = LoggingLimiter(args.gpu_limit)
    generator = ImageGenerator(gpu_task_limiter=limiter)
    try:
        tasks = [
            asyncio.create_task(
                _run_one(
                    generator,
                    index + 1,
                    args.prompt,
                    args.size,
                    args.count,
                    args.steps,
                    args.cfg_scale,
                ),
                name=f"load-test-{index + 1}",
            )
            for index in range(args.requests)
        ]
        results = await asyncio.gather(*tasks)
        print("summary:", flush=True)
        for request_id, images in results:
            print(
                f"  {request_id}: {len(images)} image(s) "
                f"source={images[0].source if images else 'n/a'} "
                f"filename={images[0].filename if images else 'n/a'}",
                flush=True,
            )
    finally:
        await generator.aclose()
        _restore_sd_server_logging(original_acquire, original_release)


if __name__ == "__main__":
    asyncio.run(main())
