from __future__ import annotations

import base64
from collections.abc import Callable, Iterable
from dataclasses import dataclass
import json
import mimetypes
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time

import httpx

from image_models._model_env import load_model_env, optional_path
from sns_agent.schemas import GeneratedImage, ImageGenerationRequest


_GPU_CONDITION = threading.Condition()
_LEASED_GPUS: set[int] = set()

_HOST = "127.0.0.1"
_STARTUP_TIMEOUT_SECONDS = 120.0
_GPU_WAIT_TIMEOUT_SECONDS = 120.0
_GPU_POLL_INTERVAL_SECONDS = 1.0
_REQUEST_TIMEOUT_SECONDS = 300.0
_MAX_GPU_UTILIZATION_PERCENT = 10.0
_MAX_VRAM_USAGE_PERCENT = 5.0
_DEFAULT_STEPS = 20
_DEFAULT_CFG_SCALE = 7.0
_DEFAULT_OUTPUT_FORMAT = "png"
_SD_CPP_PROMPT_ARGS_OPEN = "<sd_cpp_extra_args>"
_SD_CPP_PROMPT_ARGS_CLOSE = "</sd_cpp_extra_args>"
_READINESS_POLL_SECONDS = 0.5


@dataclass(slots=True)
class GPUCandidate:
    index: int
    name: str
    memory_total_mb: float | None
    memory_free_mb: float | None
    memory_used_mb: float | None
    gpu_utilization_percent: float | None
    memory_utilization_percent: float | None


@dataclass(slots=True)
class SDServerModelConfig:
    model_name: str
    arguments: list[str]
    requires_reference_images: bool = False


@dataclass(slots=True)
class SDServerReservation:
    port: int
    process: subprocess.Popen[str]


class SDServerImageGenerator:
    def __init__(self, model_config: SDServerModelConfig):
        self._model_config = model_config

    def generate(self, request: ImageGenerationRequest) -> list[GeneratedImage]:
        if self._model_config.requires_reference_images and not request.reference_images:
            raise RuntimeError(f"local image model '{self._model_config.model_name}' requires reference images")
        gpu_index = self._acquire_gpu()
        reservation: SDServerReservation | None = None
        try:
            reservation = self._start_server(gpu_index)
            return self._request_images(reservation.port, request)
        finally:
            if reservation is not None:
                self._stop_server(reservation)
            self._release_gpu(gpu_index)

    def _acquire_gpu(self) -> int:
        deadline = time.monotonic() + _GPU_WAIT_TIMEOUT_SECONDS
        with _GPU_CONDITION:
            while True:
                for candidate in self._discover_gpus():
                    if candidate.index in _LEASED_GPUS:
                        continue
                    if not self._is_gpu_available(candidate):
                        continue
                    _LEASED_GPUS.add(candidate.index)
                    return candidate.index
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("no GPU available before timeout")
                _GPU_CONDITION.wait(timeout=min(_GPU_POLL_INTERVAL_SECONDS, remaining))

    @staticmethod
    def _discover_gpus() -> list[GPUCandidate]:
        try:
            import pynvml  # type: ignore
        except ImportError as exc:
            raise RuntimeError("pynvml is required for stable-diffusion.cpp GPU selection") from exc

        try:
            pynvml.nvmlInit()
        except pynvml.NVMLError as exc:  # type: ignore[attr-defined]
            raise RuntimeError(f"failed to initialize NVML: {exc}") from exc

        try:
            count = pynvml.nvmlDeviceGetCount()
            candidates: list[GPUCandidate] = []
            for index in range(count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(index)
                name = pynvml.nvmlDeviceGetName(handle)
                memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
                utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
                mib = 1024 * 1024
                candidates.append(
                    GPUCandidate(
                        index=index,
                        name=name.decode("utf-8", errors="replace") if isinstance(name, bytes) else str(name),
                        memory_total_mb=memory.total / mib,
                        memory_free_mb=memory.free / mib,
                        memory_used_mb=memory.used / mib,
                        gpu_utilization_percent=float(utilization.gpu),
                        memory_utilization_percent=float(utilization.memory),
                    )
                )
            return candidates
        except pynvml.NVMLError as exc:  # type: ignore[attr-defined]
            raise RuntimeError(f"failed to query GPU state via NVML: {exc}") from exc
        finally:
            pynvml.nvmlShutdown()

    @staticmethod
    def _is_gpu_available(candidate: GPUCandidate) -> bool:
        if (
            candidate.gpu_utilization_percent is not None
            and candidate.gpu_utilization_percent > _MAX_GPU_UTILIZATION_PERCENT
        ):
            return False
        if candidate.memory_total_mb and candidate.memory_used_mb is not None:
            vram_usage_percent = (candidate.memory_used_mb / candidate.memory_total_mb) * 100.0
            if vram_usage_percent >= _MAX_VRAM_USAGE_PERCENT:
                return False
        return True

    @staticmethod
    def _release_gpu(gpu_index: int) -> None:
        with _GPU_CONDITION:
            _LEASED_GPUS.discard(gpu_index)
            _GPU_CONDITION.notify_all()

    def _start_server(self, gpu_index: int) -> SDServerReservation:
        binary = self._find_sd_server_binary()
        port = self._find_free_port()
        command = [
            str(binary),
            "--listen-ip",
            _HOST,
            "--listen-port",
            str(port),
            *self._model_config.arguments,
        ]
        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = str(gpu_index)
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            text=True,
            env=env,
        )
        reservation = SDServerReservation(port=port, process=process)
        try:
            self._wait_until_ready(reservation)
            return reservation
        except Exception:
            self._stop_server(reservation)
            raise

    @staticmethod
    def _find_sd_server_binary() -> Path:
        candidates = [
            Path("/home/tomokazu/build/stable-diffusion.cpp/build/bin/sd-server"),
            Path.home() / "build/stable-diffusion.cpp/build/bin/sd-server",
            Path(__file__).resolve().parents[2] / "build/stable-diffusion.cpp/build/bin/sd-server",
        ]
        for candidate in candidates:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
        which_path = shutil.which("sd-server")
        if which_path:
            return Path(which_path)
        raise RuntimeError("sd-server binary was not found in expected locations")

    @staticmethod
    def _find_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((_HOST, 0))
            return int(sock.getsockname()[1])

    @staticmethod
    def _wait_until_ready(reservation: SDServerReservation) -> None:
        deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
        url = f"http://{_HOST}:{reservation.port}/v1/models"
        with httpx.Client(timeout=5.0) as client:
            while True:
                if reservation.process.poll() is not None:
                    raise RuntimeError("sd-server exited before it became ready")
                if time.monotonic() >= deadline:
                    raise RuntimeError("sd-server startup timed out")
                try:
                    response = client.get(url)
                except httpx.ConnectError:
                    time.sleep(_READINESS_POLL_SECONDS)
                    continue
                except httpx.TimeoutException as exc:
                    raise RuntimeError(f"sd-server readiness probe timed out: {exc}") from exc
                except httpx.HTTPError as exc:
                    raise RuntimeError(f"sd-server readiness probe failed: {exc}") from exc
                if response.status_code == 200:
                    return
                raise RuntimeError(
                    f"sd-server readiness probe returned {response.status_code}: {response.text.strip()}"
                )

    def _request_images(self, port: int, request: ImageGenerationRequest) -> list[GeneratedImage]:
        is_edit = bool(request.reference_images)
        route = "edits" if is_edit else "generations"
        error_label = "edit" if is_edit else "generation"
        request_kwargs: dict[str, object]
        if is_edit:
            request_kwargs = {
                "data": self._build_openai_edit_data(request),
                "files": self._build_reference_image_files(request),
            }
        else:
            request_kwargs = {"json": self._build_openai_image_payload(request)}
        url = f"http://{_HOST}:{port}/v1/images/{route}"
        with httpx.Client(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = client.post(url, **request_kwargs)
            if response.status_code >= 400:
                detail = self._response_detail(response)
                raise RuntimeError(
                    f"local image {error_label} failed for model '{self._model_config.model_name}': "
                    f"{response.status_code} {detail}"
                )
            return self._decode_generated_images(response.json())

    @staticmethod
    def _response_detail(response: httpx.Response) -> str:
        try:
            data = response.json()
        except ValueError:
            return response.text.strip() or "unknown error"
        if not isinstance(data, dict):
            return json.dumps(data, ensure_ascii=True)
        error = data.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)
        if error is not None:
            return str(error)
        message = data.get("message")
        if message is not None:
            return str(message)
        return json.dumps(data, ensure_ascii=True)

    @staticmethod
    def _stop_server(reservation: SDServerReservation) -> None:
        process = reservation.process
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def _build_openai_image_payload(self, request: ImageGenerationRequest) -> dict[str, object]:
        payload = self._build_openai_payload(request, count=request.count)
        payload["model"] = self._model_config.model_name
        return payload

    def _build_openai_edit_data(self, request: ImageGenerationRequest) -> dict[str, str]:
        return self._build_openai_payload(request, count=str(request.count))

    def _build_openai_payload(self, request: ImageGenerationRequest, *, count: int | str) -> dict[str, object]:
        payload: dict[str, object] = {
            "prompt": self._build_prompt_with_extra_args(request),
            "n": count,
            "output_format": _DEFAULT_OUTPUT_FORMAT,
        }
        if request.size:
            payload["size"] = request.size
        return payload

    @staticmethod
    def _build_reference_image_files(
        request: ImageGenerationRequest,
    ) -> list[tuple[str, tuple[str, bytes, str]]]:
        files: list[tuple[str, tuple[str, bytes, str]]] = []
        for index, reference_image in enumerate(request.reference_images, start=1):
            mime_type = (
                reference_image.mime_type
                or mimetypes.guess_type(reference_image.filename)[0]
                or "application/octet-stream"
            )
            filename = reference_image.filename or f"reference-{index}.bin"
            files.append(("image[]", (filename, reference_image.content, mime_type)))
        return files

    def _build_prompt_with_extra_args(self, request: ImageGenerationRequest) -> str:
        prompt = request.prompt
        extra_args = self._build_sd_cpp_extra_args(request)
        if extra_args:
            prompt = (
                f"{prompt}\n{_SD_CPP_PROMPT_ARGS_OPEN}"
                f"{json.dumps(extra_args, ensure_ascii=True)}"
                f"{_SD_CPP_PROMPT_ARGS_CLOSE}"
            )
        return prompt

    @staticmethod
    def _build_sd_cpp_extra_args(request: ImageGenerationRequest) -> dict[str, object]:
        sample_params: dict[str, object] = {
            "sample_steps": request.steps if request.steps is not None else _DEFAULT_STEPS,
            "guidance": {
                "txt_cfg": request.cfg_scale if request.cfg_scale is not None else _DEFAULT_CFG_SCALE,
            },
        }
        payload: dict[str, object] = {"sample_params": sample_params}
        if request.negative:
            payload["negative_prompt"] = request.negative
        if request.seed is not None:
            payload["seed"] = request.seed
        if request.flow_shift is not None:
            sample_params["flow_shift"] = request.flow_shift
        if request.sampler:
            sample_params["sample_method"] = request.sampler
        return payload

    def _decode_generated_images(self, payload: object) -> list[GeneratedImage]:
        data = payload if isinstance(payload, dict) else {}
        images: list[GeneratedImage] = []
        for index, item in enumerate(data.get("data") or [], start=1):
            encoded = item.get("b64_json")
            if not encoded:
                continue
            images.append(
                GeneratedImage(
                    content=base64.b64decode(encoded),
                    filename=f"{self._model_config.model_name}-{index}.png",
                    source=self._model_config.model_name,
                )
            )
        if not images:
            raise RuntimeError(f"local image model '{self._model_config.model_name}' produced no images")
        return images


def load_sd_server_model_config(
    *,
    env_name: str,
    model_name: str,
    full_model_env: str | None = None,
    path_flags: Iterable[tuple[str, str, bool]],
    extra_arguments: Iterable[str] = (),
    requires_reference_images: bool = False,
    validate: Callable[[dict[str, Path | None]], None] | None = None,
) -> SDServerModelConfig:
    load_model_env(env_name)
    resolved_paths: dict[str, Path | None] = {}
    if full_model_env is not None:
        resolved_paths[full_model_env] = optional_path(full_model_env)
    for _, env_var, _ in path_flags:
        resolved_paths[env_var] = optional_path(env_var)
    if validate is not None:
        validate(resolved_paths)
    missing = [env_var for _, env_var, required in path_flags if required and resolved_paths[env_var] is None]
    if missing:
        raise RuntimeError(f"{model_name} requires: {', '.join(missing)}")
    arguments = build_argument_list(
        full_model=resolved_paths.get(full_model_env) if full_model_env is not None else None,
        pairs=((flag, resolved_paths[env_var]) for flag, env_var, _ in path_flags),
    )
    arguments.extend(extra_arguments)
    return SDServerModelConfig(
        model_name=model_name,
        arguments=arguments,
        requires_reference_images=requires_reference_images,
    )


def build_argument_list(*, full_model: Path | None = None, pairs: Iterable[tuple[str, Path | None]]) -> list[str]:
    arguments: list[str] = []
    if full_model is not None:
        arguments.extend(["--model", str(full_model)])
    for flag, path in pairs:
        if path is None:
            continue
        arguments.extend([flag, str(path)])
    return arguments
