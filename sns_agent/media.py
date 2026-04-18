from __future__ import annotations

import asyncio
import base64
import gc
import importlib.util
import inspect
import os
import sys
from pathlib import Path
from typing import Any

import httpx

from .gpu_tasks import GPUTaskLimiter
from .schemas import GeneratedImage, GeneratedVideo, ImageGenerationRequest, VideoGenerationRequest


class ImageGenerator:
    def __init__(self, gpu_task_limiter: GPUTaskLimiter | None = None):
        configured_backend = os.getenv("IMAGE_BACKEND", "api").lower()
        self._backend = "api" if configured_backend == "openrouter" else configured_backend
        self._api_style = os.getenv("IMAGE_API_STYLE", "openai-images").lower()
        self._api_url = os.getenv("IMAGE_API_URL")
        self._api_key = os.getenv("IMAGE_API_KEY") or os.getenv("OPENROUTER_API_KEY")
        self._configured_models = self._parse_model_list(os.getenv("IMAGE_MODEL"))
        self._legacy_model = os.getenv("IMAGE_API_MODEL") or os.getenv(
            "IMAGE_OPENROUTER_MODEL",
            "google/gemini-2.5-flash-image",
        )
        self._image_models_dir = Path(os.getenv("IMAGE_MODELS_DIR", "image_models"))
        self._http_client = httpx.AsyncClient(timeout=120.0)
        self._gpu_task_limiter = gpu_task_limiter

    async def generate(self, request: ImageGenerationRequest) -> list[GeneratedImage]:
        if not self._is_enabled():
            raise RuntimeError("image generation is disabled; configure IMAGE_BACKEND, IMAGE_MODEL, and backend settings")
        if self._backend == "stable-diffusion-cpp":
            if self._gpu_task_limiter is None:
                return await self._generate_with_local_model_module(request)
            return await self._gpu_task_limiter.run(lambda: self._generate_with_local_model_module(request))
        if self._backend == "api" and self._api_key:
            images = await self._generate_with_api(request)
            if images:
                return images
        raise RuntimeError("image generation backend returned no images")

    def _is_enabled(self) -> bool:
        if not self._configured_models:
            return False
        if self._backend == "api":
            return bool(self._api_key)
        if self._backend == "stable-diffusion-cpp":
            return True
        return False

    async def _generate_with_api(self, request: ImageGenerationRequest) -> list[GeneratedImage]:
        if self._api_style == "openrouter-chat":
            return await self._generate_with_openrouter_chat(request)
        return await self._generate_with_openai_compatible_api(request)

    async def _generate_with_openai_compatible_api(self, request: ImageGenerationRequest) -> list[GeneratedImage]:
        api_url = self._api_url or "https://api.openai.com/v1/images/generations"
        model_name = self._resolve_api_model_name(request.model)
        payload: dict[str, Any] = {
            "model": model_name,
            "prompt": request.prompt,
            "n": request.count,
            "response_format": "b64_json",
        }
        if request.size:
            payload["size"] = request.size
        if request.negative:
            payload["negative_prompt"] = request.negative
        if request.seed is not None:
            payload["seed"] = request.seed
        response = await self._http_client.post(
            api_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        response_json = response.json()
        images: list[GeneratedImage] = []
        for index, item in enumerate(response_json.get("data") or [], start=1):
            if (item or {}).get("b64_json"):
                images.append(
                    GeneratedImage(
                        content=base64.b64decode(item["b64_json"]),
                        filename=f"image-{index}.png",
                        source=model_name,
                    )
                )
                continue
            if (item or {}).get("url"):
                binary_response = await self._http_client.get(item["url"])
                binary_response.raise_for_status()
                images.append(
                    GeneratedImage(
                        content=binary_response.content,
                        filename=f"image-{index}.png",
                        source=model_name,
                    )
                )
        return images[:4]

    async def _generate_with_openrouter_chat(self, request: ImageGenerationRequest) -> list[GeneratedImage]:
        api_url = self._api_url or "https://openrouter.ai/api/v1/chat/completions"
        model_name = self._resolve_api_model_name(request.model)
        payload: dict[str, Any] = {
            "model": model_name,
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
                api_url,
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
                    source=model_name,
                )
            )
        return images[:4]

    async def _generate_with_local_model_module(self, request: ImageGenerationRequest) -> list[GeneratedImage]:
        model_name = self._resolve_local_model_name(request.model)
        module_path = self._resolve_local_model_module_path(model_name)
        module = self._load_local_model_module(model_name, module_path)
        try:
            generated = await self._invoke_local_model_generate(module, request, model_name)
            if not generated:
                raise RuntimeError(f"local image model '{model_name}' produced no images")
            return generated[:4]
        finally:
            await self._cleanup_local_model_module(module)
            self._unload_local_model_module(module)

    def _resolve_api_model_name(self, requested_model: str | None) -> str:
        return self._resolve_model_name(requested_model, backend_label="api")

    def _resolve_local_model_name(self, requested_model: str | None) -> str:
        model_name = self._resolve_model_name(requested_model, backend_label="stable-diffusion.cpp")
        if not self._configured_models and requested_model is None:
            raise RuntimeError("IMAGE_MODEL is required for stable-diffusion.cpp backend")
        return model_name

    def _resolve_model_name(self, requested_model: str | None, *, backend_label: str) -> str:
        if requested_model:
            if self._configured_models and requested_model not in self._configured_models:
                raise RuntimeError(
                    f"model '{requested_model}' is not enabled for {backend_label}; "
                    f"allowed models: {', '.join(self._configured_models)}"
                )
            return requested_model
        if self._configured_models:
            return self._configured_models[0]
        if self._legacy_model:
            return self._legacy_model
        raise RuntimeError(f"IMAGE_MODEL is required for {backend_label} backend")

    def _resolve_local_model_module_path(self, model_name: str) -> Path:
        if not self._image_models_dir.exists():
            raise RuntimeError(f"image models directory does not exist: {self._image_models_dir}")
        module_path = self._image_models_dir / f"{model_name}.py"
        if not module_path.is_file():
            raise RuntimeError(
                f"local image model module '{model_name}.py' was not found under {self._image_models_dir}"
            )
        return module_path

    async def _invoke_local_model_generate(self, module: Any, request: ImageGenerationRequest, model_name: str) -> list[GeneratedImage]:
        generate = getattr(module, "generate", None)
        if generate is None:
            raise RuntimeError(f"local image model '{model_name}' must define generate(request)")
        if inspect.iscoroutinefunction(generate):
            result = await generate(request)
        else:
            result = await asyncio.to_thread(generate, request)
        return self._normalize_local_model_result(result, model_name)

    async def _cleanup_local_model_module(self, module: Any) -> None:
        cleanup = getattr(module, "cleanup", None)
        if cleanup is None:
            self._best_effort_release_vram()
            return
        if inspect.iscoroutinefunction(cleanup):
            await cleanup()
        else:
            await asyncio.to_thread(cleanup)
        self._best_effort_release_vram()

    def _load_local_model_module(self, model_name: str, module_path: Path) -> Any:
        qualified_name = f"image_models.{model_name}"
        spec = importlib.util.spec_from_file_location(qualified_name, module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"failed to load local image model module: {module_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualified_name] = module
        spec.loader.exec_module(module)
        return module

    def _unload_local_model_module(self, module: Any) -> None:
        module_name = getattr(module, "__name__", None)
        if module_name:
            sys.modules.pop(module_name, None)
        gc.collect()
        self._best_effort_release_vram()

    def _normalize_local_model_result(self, result: Any, model_name: str) -> list[GeneratedImage]:
        if not isinstance(result, list):
            raise RuntimeError(f"local image model '{model_name}' must return list[GeneratedImage]")
        normalized: list[GeneratedImage] = []
        for index, item in enumerate(result, start=1):
            if isinstance(item, GeneratedImage):
                if item.source == "unknown":
                    item.source = model_name
                normalized.append(item)
                continue
            if isinstance(item, bytes):
                normalized.append(
                    GeneratedImage(
                        content=item,
                        filename=f"{model_name}-{index}.png",
                        source=model_name,
                    )
                )
                continue
            raise RuntimeError(
                f"local image model '{model_name}' returned unsupported item type: {type(item).__name__}"
            )
        return normalized

    @staticmethod
    def _best_effort_release_vram() -> None:
        try:
            import torch  # type: ignore

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                if hasattr(torch.cuda, "ipc_collect"):
                    torch.cuda.ipc_collect()
        except Exception:
            pass

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

    @staticmethod
    def _parse_model_list(raw_models: str | None) -> list[str]:
        if not raw_models:
            return []
        return [model.strip() for model in raw_models.split(",") if model.strip()]


class VideoGenerator:
    def __init__(self):
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
