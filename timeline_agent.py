import asyncio

from dotenv import load_dotenv, find_dotenv
from escape_cf_browser import ContinuousBrowserClass
from post_parser import parse_llm_syntax
from ts_worker import TruthSocialWorker
from sub_agents.naive_agent import NaiveAgent
from sub_agents.image_gen_agent import ImageGenAgent
from sub_agents.image_edit_agent import ImageEditAgent

load_dotenv(find_dotenv())


async def main():
    async with (ContinuousBrowserClass(headless=True) as browser):
        worker = TruthSocialWorker(browser=browser)
        async for post in worker.iterate_notifications():
            ancestors = await worker.get_ancestors(post)
            *history, user_message = ancestors
            parsed = parse_llm_syntax(user_message.content)

            match parsed["type"]:
                case "naive":
                    await NaiveAgent(worker).run(user_message, history, parsed)
                case "image_gen":
                    await ImageGenAgent(worker).run(user_message, history, parsed)
                case "image_edit":
                    await ImageEditAgent(worker).run(user_message, history, parsed)
                case _:
                    await worker.make_post(
                        content="不明なコマンドです。",
                        in_reply_to=post.post_id,
                    )


if __name__ == '__main__':
    asyncio.run(main())
