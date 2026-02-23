from abc import ABC, abstractmethod
from os import PathLike
from typing import Optional

from PIL import Image

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

    @abstractmethod
    async def run(
        self,
        post: TruthPost,
        history: list[TruthPost],
        parsed: dict,
    ) -> None:
        """
        通知への返信処理を行う。
        post     : 返信対象の投稿
        history  : post より前の祖先スレッド (oldest → newest)
        parsed   : parse_llm_syntax() の返り値
        """
        ...
