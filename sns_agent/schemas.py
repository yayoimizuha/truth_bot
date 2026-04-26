from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class MediaAttachment:
    media_id: str
    media_type: str
    url: str
    preview_url: str | None = None
    mime_type: str | None = None


@dataclass(slots=True)
class NormalizedPost:
    post_id: str
    author_handle: str
    author_display_name: str
    parent_post_id: str | None
    raw_content: str
    plain_text: str
    llm_text: str
    command_text: str
    leading_mentions: list[str]
    inline_mentions: list[str]
    expanded_urls: list[str]
    media: list[MediaAttachment]
    created_at: str | None
    quote_post_id: str | None = None
    source_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NotificationItem:
    notification_id: str
    post_id: str
    reason: str
    account_handle: str
    payload: dict[str, Any]


@dataclass(slots=True)
class ImageGenerationRequest:
    prompt: str
    model: str | None = None
    size: str | None = None
    steps: int | None = None
    cfg_scale: float | None = None
    flow_shift: float | None = None
    seed: int | None = None
    count: int = 1
    negative: str | None = None
    sampler: str | None = None
    reference_images: list["ReferenceImage"] = field(default_factory=list)


@dataclass(slots=True)
class ReferenceImage:
    content: bytes
    filename: str
    mime_type: str
    source_url: str | None = None


@dataclass(slots=True)
class VideoGenerationRequest:
    prompt: str
    model: str | None = None
    duration_seconds: int | None = None
    size: str | None = None
    fps: int | None = None
    seed: int | None = None


@dataclass(slots=True)
class GeneratedImage:
    content: bytes
    mime_type: str = "image/png"
    filename: str = "image.png"
    source: str = "unknown"


@dataclass(slots=True)
class GeneratedVideo:
    content: bytes
    mime_type: str = "video/mp4"
    filename: str = "video.mp4"
    source: str = "unknown"


@dataclass(slots=True)
class AgentResponse:
    text: str
    mentions: list[str] = field(default_factory=list)
    images: list[GeneratedImage] = field(default_factory=list)
    video: GeneratedVideo | None = None

    def validate(self) -> None:
        if self.images and self.video is not None:
            raise ValueError("images and video are mutually exclusive")
        if len(self.images) > 4:
            raise ValueError("images must be <= 4")


@dataclass(slots=True)
class PublishResult:
    status_id: str
    raw_response: dict[str, Any]
    published_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class HostedMediaPage:
    page_id: str
    public_url: str


@dataclass(slots=True)
class CommandEnvelope:
    name: str
    headers: dict[str, str]
    prompt: str


@dataclass(slots=True)
class StoredGeneratedMedia:
    path: Path
    mime_type: str
    source: str
