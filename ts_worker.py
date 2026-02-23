from __future__ import annotations

import asyncio
import sqlite3

import filetype
import base64
import json
import mimetypes
from dataclasses import dataclass
from io import BytesIO
from os import PathLike
from pathlib import Path
from typing import Optional, Iterator, AsyncGenerator
from PIL import Image
from box.box import Box
from escape_cf_browser import ContinuousBrowserClass
from post_parser import post_parser


@dataclass
class TruthMedia:
    media_id: int
    type: str
    url: str

    def mime_type(self):
        return mimetypes.guess_type(self.url)[0]


@dataclass
class TruthPost:
    post_id: int
    content: str
    author: str
    media_ids: list[TruthMedia]
    in_reply_to: Optional[int]
    mentions: list[str]
    quote_id: Optional[int] = None

    @classmethod
    def parse_api_response(cls, cont: Box) -> TruthPost:
        return TruthPost(
            post_id=int(cont.id),
            content=post_parser(cont.content),
            author=cont.account.acct,
            media_ids=[TruthMedia(int(media.id), media.type, media.url) for media in cont.media_attachments],
            in_reply_to=cont.in_reply_to_id,
            mentions=[mention.acct for mention in cont.mentions],
            quote_id=int(cont.quote_id) if cont.get("quote_id") else None,
        )


class TruthSocialWorker:
    def __init__(self, browser: ContinuousBrowserClass):
        self._browser = browser

    async def make_post(self, content: str, in_reply_to: Optional[int], mentions: list[str] = None,
                        quote_id: Optional[int] = None,
                        media: list[Image.Image | bytes | PathLike] = None) -> TruthPost:
        media = media or []
        cont = await self._browser.post(
            url="https://truthsocial.com/api/v1/statuses",
            content_type="application/json",
            body={"content_type": "text/plain",
                  "in_reply_to_id": str(in_reply_to) if in_reply_to is not None else "",
                  "media_ids": asyncio.gather(*[self.upload_media(m) for m in media]),
                  "poll": None,
                  "published": True,
                  "quote_id": str(quote_id) if quote_id is not None else None,
                  "status": content,
                  "title": "",
                  "visibility": "public",
                  "group_timeline_visible": True}
        )
        if "errors" in json.loads(cont).keys():
            raise Exception(f"Post failed: {cont}")
        return TruthPost.parse_api_response(Box(json.loads(cont)))

    async def upload_media(self, media: Image.Image | bytes | PathLike) -> TruthMedia:
        file_stream: BytesIO
        mime_type: str
        file_ext: str
        match media:
            case Image.Image():
                file_stream = BytesIO()
                media.save(file_stream, format="PNG")
                file_stream.seek(0)
                mime_type = "image/png"
                file_ext = ".png"
                pass
            case bytes():
                file_stream = BytesIO(media)
                mime_type = filetype.guess_mime(media).mime
                file_ext = mimetypes.guess_extension(mime_type) or ""
                pass
            case PathLike():
                with open(media, "rb") as f:
                    file_stream = BytesIO(f.read())
                file_stream.seek(0)
                mime_type = mimetypes.guess_type(media)[0]
                file_ext = media.__str__().split(".")[-1]
        print(mime_type, file_ext)
        # noinspection PyProtectedMember
        _resp = await self._browser._page.evaluate(f"""
                        async ({{base64_data, mime_type, file_ext}}) => {{
                                const res = await fetch(base64_data);
                                const blob = await res.blob();
                                const formData = new FormData();
                                formData.append("file", blob, "upload." + file_ext);
                                const response = await fetch("https://truthsocial.com/api/v1/media", {{
                                    method: "POST",
                                    headers: {{ "Authorization": "Bearer {self._browser._token}" }},
                                    body: formData
                                }});
                                if (!response.ok) {{
                                    const errorText = await response.text();
                                    throw new Error(`Upload failed: ${{response.status}} ${{errorText}}`);
                                }}
                                return await response.json();
                        }};""", {
            "base64_data": f"data:{mime_type};base64," + base64.b64encode(file_stream.getvalue()).decode(),
            "mime_type": mime_type, "file_ext": file_ext})
        print(_resp)
        return TruthMedia(int(_resp["id"]), _resp["type"], _resp["url"])

    async def iterate_notifications(self) -> AsyncGenerator[TruthPost, None]:
        with sqlite3.connect("history.db") as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS proceed(id INTEGER PRIMARY KEY,complete BOOLEAN NOT NULL);"
            )
            while True:
                for cont in json.loads(await self._browser.get(
                        url="https://truthsocial.com/api/v1/alerts?category=mentions&follow_mentions=false"
                )):
                    post_id = int(cont["status"]["id"])
                    if conn.execute(
                            "SELECT COUNT(*) FROM proceed WHERE id=?;",
                            (post_id,)
                    ).fetchone()[0] != 0:
                        continue
                    else:
                        conn.execute(
                            "REPLACE INTO proceed(id,complete) VALUES(?,FALSE);",
                            (post_id,)
                        )
                        conn.commit()
                        yield await self.from_id(post_id)
                    await asyncio.sleep(5)

    async def from_id(self, post_id: int) -> TruthPost:
        cont = Box(json.loads(await self._browser.get(url=f"https://truthsocial.com/api/v1/statuses/{post_id}")))
        return TruthPost.parse_api_response(cont)

    async def get_ancestors(self, post: TruthPost) -> list[TruthPost]:
        if post.in_reply_to is None:
            return [post]
        else:
            sleep_timer = 1
            while True:
                try:
                    # noinspection PyBroadException
                    obj = await self._browser.get(
                        url=f"https://truthsocial.com/api/v2/statuses/{post.post_id}/context/ancestors"
                    )
                    data = json.loads(obj)
                    if not isinstance(data, list) and "errors" in data:
                        raise Exception(f"Fetch ancestors failed: {obj}")
                    break
                except Exception as e:
                    print("Error fetching ancestors:", e)
                    await asyncio.sleep(sleep_timer)
                    sleep_timer *= 2

            ancestors_data = json.loads(obj)
            if not ancestors_data:
                return [post]

            ancestors_posts = [TruthPost.parse_api_response(Box(cont)) for cont in ancestors_data]
            _top = ancestors_posts[0]
            _reply = ancestors_posts[1:] + [post]
            return await self.get_ancestors(_top) + _reply


async def main():
    async with ContinuousBrowserClass(headless=False) as browser:
        worker = TruthSocialWorker(browser=browser)
        print(worker.get_ancestors(await worker.from_id(115868358287116495)))
        print(worker.upload_media(Path(r"W:\100.png")))


if __name__ == '__main__':
    asyncio.run(main())
