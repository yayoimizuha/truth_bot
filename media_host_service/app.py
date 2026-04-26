from __future__ import annotations

import html
from io import BytesIO
import json
import mimetypes
import os
import secrets
import shutil
from pathlib import Path

import av
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from PIL import Image, ImageOps, UnidentifiedImageError


def storage_dir() -> Path:
    return Path(os.getenv("MEDIA_HOST_STORAGE_DIR", "media_host_storage"))


def ensure_storage_dir() -> Path:
    path = storage_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def sanitize_filename(filename: str) -> str:
    name = Path(filename or "image").name
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in name) or "image"


def build_public_url(request: Request, page_id: str) -> str:
    return str(request.url_for("media_page", page_id=page_id))


def build_og_image_url(request: Request, page_id: str) -> str:
    return str(request.url_for("og_image", page_id=page_id))


def is_supported_mime_type(mime_type: str) -> bool:
    return mime_type.startswith("image/") or mime_type.startswith("video/")


def media_kind(mime_type: str) -> str:
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/"):
        return "video"
    raise HTTPException(status_code=400, detail="only image and video files are supported")


def load_metadata(page_id: str) -> dict:
    metadata_path = ensure_storage_dir() / page_id / "metadata.json"
    if not metadata_path.exists():
        raise HTTPException(status_code=404, detail="page not found")
    return json.loads(metadata_path.read_text())


def extract_video_poster(video_path: Path) -> Image.Image:
    try:
        with av.open(str(video_path)) as container:
            if not container.streams.video:
                raise HTTPException(status_code=400, detail="video stream not found")
            for frame in container.decode(video=0):
                return frame.to_image().convert("RGB")
    except av.FFmpegError as exc:
        raise HTTPException(status_code=400, detail="invalid video file") from exc
    raise HTTPException(status_code=400, detail="video contains no decodable frames")


def save_video_poster(page_dir: Path, filename: str, video_path: Path) -> tuple[str, str]:
    poster_name = f"{Path(filename).stem}.poster.png"
    poster_path = page_dir / poster_name
    poster = extract_video_poster(video_path)
    try:
        poster.save(poster_path, format="PNG", optimize=True)
    finally:
        poster.close()
    return poster_name, "image/png"


def open_page_preview_images(page_id: str, items: list[dict[str, str]]) -> list[Image.Image]:
    page_dir = ensure_storage_dir() / page_id
    images: list[Image.Image] = []
    try:
        for item in items:
            preview_name = item.get("poster_filename") or item["filename"]
            image_path = page_dir / Path(preview_name).name
            image = Image.open(image_path)
            image.load()
            images.append(image.convert("RGB"))
    except FileNotFoundError as exc:
        for image in images:
            image.close()
        raise HTTPException(status_code=404, detail="file not found") from exc
    except UnidentifiedImageError as exc:
        for image in images:
            image.close()
        raise HTTPException(status_code=500, detail="invalid stored image") from exc
    return images


def paste_image(canvas: Image.Image, source: Image.Image, box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    fitted = ImageOps.fit(source, (x1 - x0, y1 - y0), method=Image.Resampling.LANCZOS)
    canvas.paste(fitted, (x0, y0))


def render_og_image(images: list[Image.Image]) -> bytes:
    width = 1200
    height = 630
    gutter = 12
    canvas = Image.new("RGB", (width, height), color=(17, 17, 17))

    count = min(len(images), 4)
    if count == 1:
        boxes = [(0, 0, width, height)]
    elif count == 2:
        mid = width // 2
        boxes = [(0, 0, mid - gutter // 2, height), (mid + gutter // 2, 0, width, height)]
    else:
        mid_x = width // 2
        mid_y = height // 2
        boxes = [
            (0, 0, mid_x - gutter // 2, mid_y - gutter // 2),
            (mid_x + gutter // 2, 0, width, mid_y - gutter // 2),
            (0, mid_y + gutter // 2, mid_x - gutter // 2, height),
            (mid_x + gutter // 2, mid_y + gutter // 2, width, height),
        ]

    for image, box in zip(images[:count], boxes, strict=False):
        paste_image(canvas, image, box)

    output = BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    return output.getvalue()


app = FastAPI()


@app.post("/media")
async def create_media_page(request: Request, files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="at least one file is required")
    if len(files) > 4:
        raise HTTPException(status_code=400, detail="up to 4 files are supported")

    page_id = secrets.token_urlsafe(8)
    page_dir = ensure_storage_dir() / page_id
    page_dir.mkdir(parents=True, exist_ok=False)

    items: list[dict[str, str]] = []
    try:
        for index, upload in enumerate(files, start=1):
            mime_type = upload.content_type or "application/octet-stream"
            if not is_supported_mime_type(mime_type):
                raise HTTPException(status_code=400, detail="only image and video files are supported")

            kind = media_kind(mime_type)
            default_name = "image" if kind == "image" else "video"
            suffix = Path(upload.filename or "").suffix or mimetypes.guess_extension(mime_type) or ".bin"
            filename = f"{index}-{sanitize_filename(Path(upload.filename or default_name).stem)}{suffix}"
            target_path = page_dir / filename
            target_path.write_bytes(await upload.read())

            item = {
                "filename": filename,
                "mime_type": mime_type,
                "kind": kind,
                "url": str(request.url_for("media_file", page_id=page_id, filename=filename)),
            }
            if kind == "video":
                poster_filename, poster_mime_type = save_video_poster(page_dir, filename, target_path)
                item["poster_filename"] = poster_filename
                item["poster_mime_type"] = poster_mime_type
                item["poster_url"] = str(request.url_for("media_file", page_id=page_id, filename=poster_filename))

            items.append(item)
    except Exception:
        shutil.rmtree(page_dir, ignore_errors=True)
        raise

    metadata = {
        "page_id": page_id,
        "items": items,
    }
    (page_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2))

    return {
        "page_id": page_id,
        "public_url": build_public_url(request, page_id),
    }


@app.get("/m/{page_id}", name="media_page")
async def media_page(request: Request, page_id: str):
    metadata = load_metadata(page_id)
    items = metadata.get("items") or []
    if not items:
        raise HTTPException(status_code=404, detail="page is empty")

    public_url = build_public_url(request, page_id)
    og_image_url = build_og_image_url(request, page_id)
    title = f"Generated Media {page_id}"
    body_media = "\n".join(render_media_item(item) for item in items)
    content = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{html.escape(title)}</title>
    <meta property="og:title" content="{html.escape(title, quote=True)}">
    <meta property="og:type" content="website">
    <meta property="og:url" content="{html.escape(public_url, quote=True)}">
    <meta property="og:image" content="{html.escape(og_image_url, quote=True)}">
    <style>
      body {{
        margin: 0;
        font-family: sans-serif;
        background: #111;
        color: #f5f5f5;
      }}
      main {{
        max-width: 960px;
        margin: 0 auto;
        padding: 24px;
      }}
      img {{
        display: block;
        width: 100%;
        height: auto;
        margin: 0 0 16px;
        border-radius: 12px;
      }}
      video {{
        display: block;
        width: 100%;
        height: auto;
        margin: 0 0 16px;
        border-radius: 12px;
      }}
    </style>
  </head>
  <body>
    <main>
      <h1>{html.escape(title)}</h1>
      {body_media}
    </main>
  </body>
</html>
"""
    return HTMLResponse(content)


def render_media_item(item: dict[str, str]) -> str:
    if item.get("kind") == "video":
        poster_url = item.get("poster_url")
        poster_attr = f' poster="{html.escape(poster_url, quote=True)}"' if poster_url else ""
        return (
            f'<video controls preload="metadata" playsinline{poster_attr}>'
            f'<source src="{html.escape(item["url"], quote=True)}" type="{html.escape(item["mime_type"], quote=True)}">'
            "Your browser does not support the video tag."
            "</video>"
        )
    return f'<img src="{html.escape(item["url"], quote=True)}" alt="generated media" loading="lazy">'


@app.get("/og/{page_id}.png", name="og_image")
async def og_image(page_id: str):
    metadata = load_metadata(page_id)
    items = metadata.get("items") or []
    if not items:
        raise HTTPException(status_code=404, detail="page is empty")
    images = open_page_preview_images(page_id, items)
    try:
        return Response(content=render_og_image(images), media_type="image/png")
    finally:
        for image in images:
            image.close()


@app.get("/media/{page_id}/{filename}", name="media_file")
async def media_file(page_id: str, filename: str):
    safe_name = Path(filename).name
    target_path = ensure_storage_dir() / page_id / safe_name
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(target_path)
