from abc import ABC, abstractmethod
from os import PathLike
from typing import Optional

from PIL import Image

from post_parser import BOT_ERROR_MARKER
from ts_worker import TruthSocialWorker, TruthPost


class AgentClass(ABC):
    def __init__(self, worker: TruthSocialWorker):
        self._worker = worker

    async def create_post(
        self,
        content: str,
        in_reply_to: Optional[int] = None,
        mentions: Optional[list[str]] = None,
        quote_id: Optional[int] = None,
        media: Optional[list[Image.Image | bytes | PathLike]] = None,
    ) -> TruthPost:
        return await self._worker.make_post(
            content=content,
            in_reply_to=in_reply_to,
            mentions=mentions,
            quote_id=quote_id,
            media=media,
        )

    async def create_error_post(
        self,
        error_text: str,
        in_reply_to: int,
    ) -> TruthPost:
        """[BOT_ERROR] マーカー付きでエラーリプライを投稿する。
        会話履歴の再構築時に通常の応答とは区別され、除外される。"""
        content = f"{BOT_ERROR_MARKER} {error_text}"
        return await self.create_post(content=content, in_reply_to=in_reply_to)

    @abstractmethod
    async def run(
        self,
        post: TruthPost,
        parsed: dict,
    ) -> None:
        """
        通知への返信処理を行う。
        post   : 返信対象の投稿
        parsed : parse_llm_syntax() の返り値
        """
        ...
