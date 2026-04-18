from __future__ import annotations

from .schemas import CommandEnvelope, ImageGenerationRequest, VideoGenerationRequest


class CommandParseError(ValueError):
    pass


def parse_command(text: str) -> CommandEnvelope | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None

    lines = stripped.splitlines()
    command_name = lines[0].strip()
    if command_name not in {"/image", "/video"}:
        return None

    headers: dict[str, str] = {}
    prompt_lines: list[str] = []
    in_body = False
    for line in lines[1:]:
        if not in_body and line.strip() == "":
            in_body = True
            continue
        if not in_body:
            if ":" not in line:
                raise CommandParseError(f"invalid header line: {line}")
            key, value = line.split(":", 1)
            headers[key.strip()] = value.strip()
        else:
            prompt_lines.append(line)

    prompt = "\n".join(prompt_lines).strip()
    return CommandEnvelope(name=command_name[1:], headers=headers, prompt=prompt)


def _parse_int(headers: dict[str, str], key: str) -> int | None:
    if key not in headers or headers[key] == "":
        return None
    return int(headers[key])


def _parse_float(headers: dict[str, str], key: str) -> float | None:
    if key not in headers or headers[key] == "":
        return None
    return float(headers[key])


def to_image_request(command: CommandEnvelope) -> ImageGenerationRequest:
    if command.name != "image":
        raise CommandParseError("command is not /image")
    if not command.prompt:
        raise CommandParseError("prompt is required for /image")

    allowed = {"model", "size", "steps", "cfg_scale", "seed", "count", "negative", "sampler"}
    unknown = sorted(set(command.headers) - allowed)
    if unknown:
        raise CommandParseError(f"unknown headers: {', '.join(unknown)}")

    count = _parse_int(command.headers, "count") or 1
    if not 1 <= count <= 4:
        raise CommandParseError("count must be between 1 and 4")

    return ImageGenerationRequest(
        prompt=command.prompt,
        model=command.headers.get("model"),
        size=command.headers.get("size"),
        steps=_parse_int(command.headers, "steps"),
        cfg_scale=_parse_float(command.headers, "cfg_scale"),
        seed=_parse_int(command.headers, "seed"),
        count=count,
        negative=command.headers.get("negative"),
        sampler=command.headers.get("sampler"),
    )


def to_video_request(command: CommandEnvelope) -> VideoGenerationRequest:
    if command.name != "video":
        raise CommandParseError("command is not /video")
    if not command.prompt:
        raise CommandParseError("prompt is required for /video")

    allowed = {"model", "duration_seconds", "size", "fps", "seed"}
    unknown = sorted(set(command.headers) - allowed)
    if unknown:
        raise CommandParseError(f"unknown headers: {', '.join(unknown)}")

    return VideoGenerationRequest(
        prompt=command.prompt,
        model=command.headers.get("model"),
        duration_seconds=_parse_int(command.headers, "duration_seconds"),
        size=command.headers.get("size"),
        fps=_parse_int(command.headers, "fps"),
        seed=_parse_int(command.headers, "seed"),
    )
