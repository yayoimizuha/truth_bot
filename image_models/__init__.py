from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import gc

from sns_agent.schemas import GeneratedImage, ImageGenerationRequest


@dataclass(slots=True)
class GPUDeviceStats:
    device_index: int
    name: str
    memory_total_bytes: int | None = None
    memory_used_bytes: int | None = None
    memory_free_bytes: int | None = None
    gpu_utilization_percent: float | None = None
    memory_utilization_percent: float | None = None

    @property
    def memory_used_mib(self) -> float | None:
        if self.memory_used_bytes is None:
            return None
        return self.memory_used_bytes / (1024 * 1024)

    @property
    def memory_total_mib(self) -> float | None:
        if self.memory_total_bytes is None:
            return None
        return self.memory_total_bytes / (1024 * 1024)


class LocalImageModelBase(ABC):
    """Base class for local image model modules.

    Modules under image_models/ can keep a singleton instance of a subclass and
    expose module-level generate(request) / cleanup() functions that delegate to it.
    """

    def __init__(self, model_name: str):
        self.model_name = model_name

    @abstractmethod
    def generate(self, request: ImageGenerationRequest) -> list[GeneratedImage]:
        """Run one image generation request."""

    def cleanup(self) -> None:
        """Release local resources after generation."""
        self.release_resources()

    def release_resources(self) -> None:
        """Best-effort local cleanup for RAM."""
        gc.collect()

    def snapshot_gpu_stats(self) -> list[GPUDeviceStats]:
        return self._snapshot_with_pynvml()

    def format_gpu_stats(self, stats: list[GPUDeviceStats] | None = None) -> str:
        snapshot = stats if stats is not None else self.snapshot_gpu_stats()
        if not snapshot:
            return "GPU stats unavailable"
        parts: list[str] = []
        for device in snapshot:
            memory_text = "memory=n/a"
            if device.memory_used_mib is not None and device.memory_total_mib is not None:
                memory_text = f"memory={device.memory_used_mib:.0f}/{device.memory_total_mib:.0f} MiB"
            gpu_text = "gpu=n/a"
            if device.gpu_utilization_percent is not None:
                gpu_text = f"gpu={device.gpu_utilization_percent:.0f}%"
            mem_util_text = "mem-util=n/a"
            if device.memory_utilization_percent is not None:
                mem_util_text = f"mem-util={device.memory_utilization_percent:.0f}%"
            parts.append(f"[{device.device_index}] {device.name}: {memory_text}, {gpu_text}, {mem_util_text}")
        return " | ".join(parts)

    @staticmethod
    def _snapshot_with_pynvml() -> list[GPUDeviceStats]:
        try:
            import pynvml  # type: ignore

            pynvml.nvmlInit()
            try:
                count = pynvml.nvmlDeviceGetCount()
                devices: list[GPUDeviceStats] = []
                for index in range(count):
                    handle = pynvml.nvmlDeviceGetHandleByIndex(index)
                    name = pynvml.nvmlDeviceGetName(handle)
                    if isinstance(name, bytes):
                        name = name.decode("utf-8", errors="replace")
                    memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
                    devices.append(
                        GPUDeviceStats(
                            device_index=index,
                            name=name,
                            memory_total_bytes=getattr(memory, "total", None),
                            memory_used_bytes=getattr(memory, "used", None),
                            memory_free_bytes=getattr(memory, "free", None),
                            gpu_utilization_percent=float(getattr(utilization, "gpu", 0)),
                            memory_utilization_percent=float(getattr(utilization, "memory", 0)),
                        )
                    )
                return devices
            finally:
                try:
                    pynvml.nvmlShutdown()
                except Exception:
                    pass
        except Exception:
            return []


def coerce_generated_images(
    items: list[GeneratedImage] | list[bytes],
    *,
    source: str,
    filename_prefix: str | None = None,
) -> list[GeneratedImage]:
    if not isinstance(items, list):
        raise TypeError(f"Generated images must be returned as a list, got {type(items).__name__}")
    normalized: list[GeneratedImage] = []
    for index, item in enumerate(items, start=1):
        if isinstance(item, GeneratedImage):
            if item.source == "unknown":
                item.source = source
            normalized.append(item)
            continue
        if isinstance(item, bytes):
            prefix = filename_prefix or source
            normalized.append(
                GeneratedImage(
                    content=item,
                    filename=f"{prefix}-{index}.png",
                    source=source,
                )
            )
            continue
        raise TypeError(f"Unsupported generated image item: {type(item).__name__}")
    return normalized


__all__ = [
    "GPUDeviceStats",
    "LocalImageModelBase",
    "coerce_generated_images",
]
