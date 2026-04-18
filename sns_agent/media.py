from __future__ import annotations

import base64
import os
import subprocess
import tempfile
import textwrap
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw

from .proxy_client import ProxyHttpClient
from .schemas import GeneratedImage, GeneratedVideo, ImageGenerationRequest, VideoGenerationRequest


class ImageGenerator:
    def __init__(self, proxy_client: ProxyHttpClient):
        self._backend = os.getenv("IMAGE_BACKEND", "openrouter").lower()
        self._api_url = os.getenv("IMAGE_API_URL", "https://openrouter.ai/api/v1/chat/completions")
        self._api_key = os.getenv("IMAGE_API_KEY") or os.getenv("OPENROUTER_API_KEY")
        self._model = os.getenv("IMAGE_API_MODEL") or os.getenv("IMAGE_OPENROUTER_MODEL", "google/gemini-2.5-flash-image")
        self._sd_binary = os.getenv("STABLE_DIFFUSION_CPP_BINARY", "sd")
        self._sd_model = os.getenv("STABLE_DIFFUSION_CPP_MODEL")
        self._sd_workdir = os.getenv("STABLE_DIFFUSION_CPP_WORKDIR")
        self._http_client = httpx.AsyncClient(timeout=120.0)

    async def generate(self, request: ImageGenerationRequest) -> list[GeneratedImage]:
        if self._backend == "stable-diffusion-cpp":
            return self._generate_with_stable_diffusion_cpp(request)
        if self._backend == "openrouter" and self._api_key:
            images = await self._generate_with_openrouter(request)
            if images:
                return images
        return [self._placeholder(request.prompt, index + 1) for index in range(request.count)]

    async def _generate_with_openrouter(self, request: ImageGenerationRequest) -> list[GeneratedImage]:
        payload: dict[str, Any] = {
            "model": request.model or self._model,
            "messages": [{"role": "user", "content": request.prompt}],
            "modalities": ["image", "text"],
            "stream": False,
        }
        if request.size:
            payload["image_config"] = self._openrouter_image_config(request.size)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            **self._openrouter_headers(),
        }
        response = (
            await self._http_client.post(
                self._api_url,
                headers=headers,
                json=payload,
            )
        )
        response.raise_for_status()
        response_json = response.json()
        message = ((response_json.get("choices") or [{}])[0]).get("message") or {}
        images: list[GeneratedImage] = []
        for index, item in enumerate(message.get("images") or [], start=1):
            image_url = ((item or {}).get("image_url") or {}).get("url")
            if not image_url:
                continue
            images.append(
                GeneratedImage(
                    content=self._decode_image_url(image_url),
                    filename=f"image-{index}.png",
                    source=request.model or self._model,
                )
            )
        return images[:4]

    def _generate_with_stable_diffusion_cpp(self, request: ImageGenerationRequest) -> list[GeneratedImage]:
        if not self._sd_model and not request.model:
            raise RuntimeError("STABLE_DIFFUSION_CPP_MODEL is not configured")

        width, height = self._parse_size(request.size)
        with tempfile.TemporaryDirectory(prefix="sdcpp-") as temp_dir:
            output_prefix = Path(temp_dir) / "out"
            command = [self._sd_binary]
            if request.model or self._sd_model:
                command.extend(["-m", request.model or self._sd_model])
            command.extend(["-p", request.prompt, "-o", str(output_prefix)])
            if width:
                command.extend(["-W", str(width)])
            if height:
                command.extend(["-H", str(height)])
            if request.steps is not None:
                command.extend(["--steps", str(request.steps)])
            if request.cfg_scale is not None:
                command.extend(["--cfg-scale", str(request.cfg_scale)])
            if request.seed is not None:
                command.extend(["--seed", str(request.seed)])
            if request.sampler:
                command.extend(["--sampling-method", request.sampler])
            if request.negative:
                command.extend(["-n", request.negative])
            if request.count > 1:
                command.extend(["-b", str(request.count)])

            completed = subprocess.run(
                command,
                cwd=self._sd_workdir,
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    "stable-diffusion.cpp failed: "
                    + (completed.stderr.strip() or completed.stdout.strip() or f"exit={completed.returncode}")
                )

            generated: list[GeneratedImage] = []
            for index, image_path in enumerate(sorted(Path(temp_dir).glob("*.png")), start=1):
                generated.append(
                    GeneratedImage(
                        content=image_path.read_bytes(),
                        filename=image_path.name,
                        source="stable-diffusion.cpp",
                    )
                )
            if not generated:
                raise RuntimeError("stable-diffusion.cpp produced no PNG outputs")
            return generated[:4]

    @staticmethod
    def _placeholder(prompt: str, index: int) -> GeneratedImage:
        image = Image.new("RGB", (1024, 1024), color=(245, 243, 236))
        draw = ImageDraw.Draw(image)
        text = textwrap.fill(prompt[:300] or "(empty prompt)", width=28)
        draw.multiline_text((48, 48), f"placeholder image {index}\n\n{text}", fill=(30, 30, 30), spacing=10)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return GeneratedImage(
            content=buffer.getvalue(),
            filename=f"placeholder-{index}.png",
            source="local-placeholder",
        )

    @staticmethod
    def _decode_image_url(image_url: str) -> bytes:
        if image_url.startswith("data:"):
            _, encoded = image_url.split(",", 1)
            return base64.b64decode(encoded)
        raise RuntimeError("OpenRouter returned non-data-url image response")

    @staticmethod
    def _parse_size(size: str | None) -> tuple[int | None, int | None]:
        if not size:
            return None, None
        if "x" not in size.lower():
            return None, None
        width_text, height_text = size.lower().split("x", 1)
        return int(width_text), int(height_text)

    @staticmethod
    def _openrouter_image_config(size: str) -> dict[str, str]:
        config: dict[str, str] = {}
        normalized = size.lower()
        aspect_ratio_map = {
            "1024x1024": "1:1",
            "832x1248": "2:3",
            "1248x832": "3:2",
            "864x1184": "3:4",
            "1184x864": "4:3",
            "896x1152": "4:5",
            "1152x896": "5:4",
            "768x1344": "9:16",
            "1344x768": "16:9",
            "1536x672": "21:9",
        }
        if normalized in aspect_ratio_map:
            config["aspect_ratio"] = aspect_ratio_map[normalized]
        if normalized.startswith("4k"):
            config["image_size"] = "4K"
        elif normalized.startswith("2k"):
            config["image_size"] = "2K"
        elif normalized.startswith("1k"):
            config["image_size"] = "1K"
        return config

    @staticmethod
    def _openrouter_headers() -> dict[str, str]:
        headers: dict[str, str] = {}
        if os.getenv("LLM_HTTP_REFERER"):
            headers["HTTP-Referer"] = os.getenv("LLM_HTTP_REFERER", "")
        if os.getenv("LLM_X_TITLE"):
            headers["X-Title"] = os.getenv("LLM_X_TITLE", "")
        return headers


class VideoGenerator:
    def __init__(self, proxy_client: ProxyHttpClient):
        self._api_url = os.getenv("VIDEO_API_URL")
        self._api_key = os.getenv("VIDEO_API_KEY")
        self._http_client = httpx.AsyncClient(timeout=120.0)

    async def generate(self, request: VideoGenerationRequest) -> GeneratedVideo:
        if not self._api_url or not self._api_key:
            raise RuntimeError("video generation backend is not configured")
        response = await self._http_client.post(
            self._api_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": request.model or os.getenv("VIDEO_API_MODEL"),
                "prompt": request.prompt,
                "duration_seconds": request.duration_seconds,
                "size": request.size,
                "fps": request.fps,
                "seed": request.seed,
            },
        )
        response.raise_for_status()
        response_json = response.json()
        if response_json.get("b64_json"):
            return GeneratedVideo(content=base64.b64decode(response_json["b64_json"]), source=self._api_url)
        if response_json.get("url"):
            binary_response = await self._http_client.get(response_json["url"])
            binary_response.raise_for_status()
            return GeneratedVideo(content=binary_response.content, source=response_json["url"])
        raise RuntimeError("video generation backend returned no media")
