from __future__ import annotations

from sns_agent.schemas import GeneratedImage


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
    "coerce_generated_images",
]
