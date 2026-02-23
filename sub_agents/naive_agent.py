from os import environ
from typing import Optional

from aiohttp import ClientSession
from pydantic_ai import Agent, ModelRequest, UserPromptPart, BinaryContent, TextPart, ModelResponse, ModelMessage, \
    SystemPromptPart, RunContext, ToolReturn
from pydantic_ai.models.bedrock import BedrockConverseModel

from post_parser import parse_llm_syntax
from ts_worker import TruthSocialWorker, TruthPost, TruthMedia
from sub_agents import AgentClass


async def truth_media_to_multimodal_input(media: TruthMedia) -> Optional[BinaryContent]:
    async with ClientSession() as session:
        data = await (await session.get(media.url)).read()
    # noinspection PyTypeChecker
    return BinaryContent(
        data=data,
        media_type=media.mime_type()
    )


_agent = Agent(
    model=BedrockConverseModel("global.amazon.nova-2-lite-v1:0"),
    deps_type=TruthSocialWorker
)


@_agent.tool
async def get_post_content_from_id(ctx: RunContext[TruthSocialWorker], post_id: int) -> ToolReturn:
    """
    指定されたIDの投稿内容を取得します。
    ユーザーの投稿に 'quote:数字' が含まれている場合、その数字を post_id に指定することで引用元の内容を確認できます。
    """
    post = await ctx.deps.from_id(post_id)
    return ToolReturn(return_value=f"投稿内容(ID: {post.post_id}, 投稿者: {post.author})",
                      content=[
                          " " if post.content == "" else post.content,
                          *[await truth_media_to_multimodal_input(media) for media in post.media_ids]
                      ])


class NaiveAgent(AgentClass):
    """通常の会話・質問応答を行うエージェント。"""

    async def run(
        self,
        post: TruthPost,
        parsed: dict,
    ) -> None:
        ancestors = await self._worker.get_ancestors(post)
        *history, user_message = ancestors

        history_message: list[ModelMessage] = [ModelRequest(parts=[SystemPromptPart(
            content=("あなたは、Truth Socialのタイムラインを監視し、投稿に対して返信や引用リツイートを行うエージェントです。"
                     "ユーザーからの質問に対して、適切な返信を生成してください。"
                     "返信には、必要に応じて画像や動画を添付することができます。"
                     "ユーザーからの質問には、丁寧かつ親切に答えるようにしてください。"
                     "ユーザーからの質問が不適切な場合は、その旨を伝えてください。"
                     "Markdown形式は使えません。数式などは上付き・下付き文字を駆使するなどして表現してください。")
        )])]

        for hist in history:
            if hist.author == environ["TRUTHSOCIAL_USERNAME"]:
                # ボット自身のリプライ: マーカーを解析し、エラー投稿は除外する
                parsed_reply = parse_llm_syntax(hist.content)

                if parsed_reply["type"] == "bot_error":
                    # エラーリプライは空のレスポンスとして扱う
                    history_message.append(ModelResponse(parts=[TextPart(content=" ")]))
                else:
                    history_message.append(ModelResponse(
                        parts=[
                            TextPart(content=hist.content),
                            # noinspection PyTypeChecker
                            *[await truth_media_to_multimodal_input(media) for media in hist.media_ids]
                        ]
                    ))
            else:
                # ユーザー投稿が連続する場合（間のボットエラーが除外された等）、
                # ダミーの ModelResponse を挿入して ModelRequest の連続を防ぐ
                if history_message and isinstance(history_message[-1], ModelRequest):
                    history_message.append(ModelResponse(parts=[TextPart(content=" ")]))

                history_message.append(ModelRequest(
                    parts=[
                        # noinspection PyTypeChecker
                        UserPromptPart(content=[
                            " " if hist.content == "" else hist.content,
                            *[await truth_media_to_multimodal_input(media) for media in hist.media_ids]
                        ])
                    ]
                ))

        # noinspection PyTypeChecker
        agent_resp = await _agent.run(
            user_prompt=[
                user_message.content,
                *[await truth_media_to_multimodal_input(media) for media in user_message.media_ids]
            ],
            message_history=history_message,
            deps=self._worker
        )

        await self.create_post(
            content=agent_resp.output,
            in_reply_to=user_message.post_id,
        )
