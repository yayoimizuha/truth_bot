import asyncio
from os import environ
from typing import Optional

from aiohttp import ClientSession
from dotenv import load_dotenv, find_dotenv
from pydantic_ai import Agent, ModelRequest, UserPromptPart, BinaryContent, TextPart, ModelResponse, ModelMessage, \
    SystemPromptPart, VideoUrl, ImageUrl, RunContext, ToolReturn
from pydantic_ai.models.bedrock import BedrockConverseModel
from escape_cf_browser import ContinuousBrowserClass
from ts_worker import TruthSocialWorker, TruthPost, TruthMedia

load_dotenv(find_dotenv())


async def truth_media_to_multimodal_input(media: TruthMedia) -> Optional[BinaryContent]:
    async with ClientSession() as session:
        data = await (await session.get(media.url)).read()
    # noinspection PyTypeChecker
    return BinaryContent(
        data=data,
        media_type=media.mime_type()
    )


agent = Agent(
    model=BedrockConverseModel("global.amazon.nova-2-lite-v1:0"),
    deps_type=TruthSocialWorker
)


@agent.tool
async def get_post_content_from_id(ctx: RunContext[TruthSocialWorker], post_id: int) -> ToolReturn:
    """
    指定されたIDの投稿内容を取得します。
    ユーザーの投稿に 'quote:数字' が含まれている場合、その数字を post_id に指定することで引用元の内容を確認できます。
    """
    post = await ctx.deps.from_id(post_id)
    return ToolReturn(return_value=f"投稿内容(ID: {post.post_id}, 投稿者: {post.author})",
                      content=[
                          post.content,
                          *[await truth_media_to_multimodal_input(media) for media in post.media_ids]
                      ])


async def main():
    async with (ContinuousBrowserClass(headless=True) as browser):
        worker = TruthSocialWorker(browser=browser)
        async for post in worker.iterate_notifications():
            *history, user_message = await worker.get_ancestors(post)
            print([*history, user_message])
            history: list[TruthPost]
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
                    history_message.append(ModelResponse(
                        parts=[
                            TextPart(content=hist.content),
                            *[await truth_media_to_multimodal_input(media) for media in hist.media_ids]
                        ]
                    ))
                else:
                    history_message.append(ModelRequest(
                        parts=[
                            UserPromptPart(content=[
                                hist.content,
                                *[await truth_media_to_multimodal_input(media) for media in hist.media_ids]
                            ])
                        ]
                    ))
            agent_resp = await agent.run(
                user_prompt=[
                    user_message.content,
                    *[await truth_media_to_multimodal_input(media) for media in user_message.media_ids]
                ],
                message_history=history_message,
                deps=worker
            )
            print(agent_resp.new_messages())
            await worker.make_post(
                content=agent_resp.output,
                in_reply_to=post.post_id
            )


if __name__ == '__main__':
    asyncio.run(main())
