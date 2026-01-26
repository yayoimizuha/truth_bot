from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from io import BytesIO
from typing import Optional, Iterator
from PIL import Image

from escape_cf_browser import ContinuousBrowserClass


@dataclass
class TruthPost:
    post_id: int
    content: str
    author: str
    media_ids: list[int]
    in_reply_to: Optional[int]
    mentions: list[str]

    def get_ancestors(self, _browser: ContinuousBrowserClass) -> list[TruthPost]:
        pass

    def from_id(self, post_id: int, _browser: ContinuousBrowserClass) -> TruthPost:
        pass


class TruthSocialWorker:
    def __init__(self, browser: ContinuousBrowserClass):
        self._browser = browser

    def make_post(self, content: str, media: list[Image.Image | bytes], in_reply_to: Optional[int], mentions: list[str]) \
            -> TruthPost:
        pass

    def upload_media(self, media: Image.Image | bytes) -> int:
        pass

    def new_post(self) -> Iterator[TruthPost]:
        pass


if __name__ == '__main__':
    worker = TruthSocialWorker(browser=ContinuousBrowserClass())
    a = BytesIO()
    a.name = "aaaa.md"
    a.type = mimetypes.guess_type(a.name)[0]
    print(a.type)
