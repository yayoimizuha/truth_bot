from __future__ import annotations

import asyncio
import base64
import gc
import importlib.util
import inspect
import logging
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError

from image_models import coerce_generated_images

from .gpu_tasks import GPUTaskLimiter
from .schemas import GeneratedImage, GeneratedVideo, ImageGenerationRequest, VideoGenerationRequest

logger = logging.getLogger(__name__)


class ImageGenerator:
    def __init__(self, gpu_task_limiter: GPUTaskLimiter | None = None):
        configured_backend = os.getenv("IMAGE_BACKEND", "api").lower()
        self._backend = "api" if configured_backend == "openrouter" else configured_backend
        self._api_style = os.getenv("IMAGE_API_STYLE", "openai-images").lower()
        self._api_url = os.getenv("IMAGE_API_URL")
        self._api_key = self._resolve_api_key()
        self._configured_models = self._parse_model_list(os.getenv("IMAGE_MODEL"))
        self._legacy_model = os.getenv("IMAGE_API_MODEL") or os.getenv(
            "IMAGE_OPENROUTER_MODEL",
            "google/gemini-2.5-flash-image",
        )
        self._image_models_dir = Path(os.getenv("IMAGE_MODELS_DIR", "image_models"))
        self._http_client = httpx.AsyncClient(timeout=120.0)
        self._openai_client = self._build_openai_client()
        self._gpu_task_limiter = gpu_task_limiter
        self._api_retry_max_retries = max(0, int(os.getenv("IMAGE_API_MAX_RETRIES", "3")))
        self._api_retry_base_seconds = max(0.0, float(os.getenv("IMAGE_API_RETRY_BASE_SECONDS", "1.0")))
        self._api_retry_max_seconds = max(
            self._api_retry_base_seconds,
            float(os.getenv("IMAGE_API_RETRY_MAX_SECONDS", "8.0")),
        )

    async def aclose(self) -> None:
        await self._http_client.aclose()
        if self._openai_client is not None:
            maybe_awaitable = self._openai_client.close()
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable

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
        if self._openai_client is None:
            raise RuntimeError("OpenAI image client is not configured")
        model_name = self._resolve_api_model_name(request.model)
        payload: dict[str, Any] = {
            "model": model_name,
            "prompt": request.prompt,
            "n": request.count,
        }
        if request.size:
            payload["size"] = request.size
        extra_body: dict[str, Any] = {}
        if request.negative:
            extra_body["negative_prompt"] = request.negative
        if request.seed is not None:
            extra_body["seed"] = request.seed

        response = await self._call_openai_images_generate(model_name, payload, extra_body or None)
        images: list[GeneratedImage] = []
        for index, item in enumerate(response.data or [], start=1):
            if item.b64_json:
                images.append(
                    GeneratedImage(
                        content=base64.b64decode(item.b64_json),
                        filename=f"image-{index}.png",
                        source=model_name,
                    )
                )
                continue
            if item.url:
                binary_response = await self._http_client.get(item.url)
                binary_response.raise_for_status()
                images.append(
                    GeneratedImage(
                        content=binary_response.content,
                        filename=f"image-{index}.png",
                        source=model_name,
                    )
                )
        return images[:4]

    async def _call_openai_images_generate(
        self,
        model_name: str,
        payload: dict[str, Any],
        extra_body: dict[str, Any] | None,
    ):
        for attempt in range(self._api_retry_max_retries + 1):
            try:
                return await self._openai_client.images.generate(
                    **payload,
                    extra_body=extra_body,
                )
            except Exception as exc:
                if not self._is_retryable_openai_error(exc) or attempt >= self._api_retry_max_retries:
                    raise self._normalize_openai_error(exc, model_name) from exc
                delay = self._backoff_delay(
                    attempt,
                    base_seconds=self._api_retry_base_seconds,
                    max_seconds=self._api_retry_max_seconds,
                )
                logger.warning(
                    "image generation retrying model=%s attempt=%d delay=%.2fs error=%s",
                    model_name,
                    attempt + 1,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)

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
        self._raise_for_status_with_body(response, f"image generation failed for model '{model_name}'")
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
        try:
            return coerce_generated_images(result, source=model_name, filename_prefix=model_name)
        except TypeError as exc:
            raise RuntimeError(f"local image model '{model_name}' returned invalid images: {exc}") from exc

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

    def _build_openai_client(self) -> AsyncOpenAI | None:
        if self._api_style != "openai-images" or not self._api_key:
            return None
        base_url = self._resolve_openai_base_url(self._api_url)
        return AsyncOpenAI(
            api_key=self._api_key,
            base_url=base_url,
            timeout=120.0,
            max_retries=0,
        )

    def _resolve_api_key(self) -> str | None:
        if self._api_style == "openai-images":
            return os.getenv("IMAGE_API_KEY") or os.getenv("OPENAI_API_KEY")
        return os.getenv("IMAGE_API_KEY") or os.getenv("OPENROUTER_API_KEY")

    @staticmethod
    def _resolve_openai_base_url(api_url: str | None) -> str | None:
        if not api_url:
            return None
        suffix = "/images/generations"
        if api_url.endswith(suffix):
            return api_url[: -len(suffix)]
        return api_url

    @staticmethod
    def _is_retryable_openai_error(exc: Exception) -> bool:
        if isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError)):
            return True
        if isinstance(exc, APIStatusError):
            return exc.status_code in {408, 409, 429} or exc.status_code >= 500
        return False

    @staticmethod
    def _normalize_openai_error(exc: Exception, model_name: str) -> RuntimeError:
        status_code = getattr(exc, "status_code", None)
        response = getattr(exc, "response", None)
        if status_code is not None:
            body = response.text.strip() if response is not None and getattr(response, "text", None) else ""
            detail = body or str(exc)
            return RuntimeError(f"image generation failed for model '{model_name}': {status_code} {detail}")
        if isinstance(exc, APIStatusError):
            body = exc.response.text.strip() if exc.response is not None else ""
            detail = body or str(exc)
            return RuntimeError(f"image generation failed for model '{model_name}': {exc.status_code} {detail}")
        return RuntimeError(f"image generation failed for model '{model_name}': {exc}")

    @staticmethod
    def _backoff_delay(attempt: int, *, base_seconds: float, max_seconds: float) -> float:
        return min(max_seconds, base_seconds * (2 ** attempt))

    @staticmethod
    def _raise_for_status_with_body(response: httpx.Response, context: str) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = response.text.strip()
            detail = body if body else "<empty response body>"
            raise RuntimeError(f"{context}: {response.status_code} {detail}") from exc


class VideoGenerator:
    def __init__(self):
        self._api_url = os.getenv("VIDEO_API_URL")
        self._api_key = os.getenv("VIDEO_API_KEY")
        self._http_client = httpx.AsyncClient(timeout=120.0)

    async def aclose(self) -> None:
        await self._http_client.aclose()

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
