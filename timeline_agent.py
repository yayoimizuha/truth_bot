import asyncio
import logging
from dotenv import load_dotenv, find_dotenv
from escape_cf_browser import ContinuousBrowserClass
from post_parser import parse_llm_syntax
from ts_worker import TruthSocialWorker
from sub_agents.naive_agent import NaiveAgent
from sub_agents.image_gen_agent import ImageGenAgent
from sub_agents.image_edit_agent import ImageEditAgent

load_dotenv(find_dotenv())

logger = logging.getLogger(__name__)


async def main():
    async with ContinuousBrowserClass(headless=True) as browser:
        worker = TruthSocialWorker(browser=browser)
        task_queue: set[asyncio.Task] = set()
        notif_iter = worker.iterate_notifications()

        fetch_task = asyncio.create_task(notif_iter.__anext__(), name="fetch-next")

        while True:
            waitables: set[asyncio.Task] = set(task_queue)
            waitables.add(fetch_task)

            done, _ = await asyncio.wait(
                waitables, return_when=asyncio.FIRST_COMPLETED,
            )

            # 完了した処理タスクを回収
            for task in done - {fetch_task}:
                task_queue.discard(task)
                exc = task.exception()
                if exc is not None:
                    logger.error("Task %s failed: %s", task.get_name(), exc, exc_info=exc)
                else:
                    logger.info("Task %s completed.", task.get_name())

            # 通知取得タスクが完了した場合
            if fetch_task in done:
                post = fetch_task.result()
                parsed = parse_llm_syntax(post.content)
                match parsed["type"]:
                    case "naive":
                        task_queue.add(asyncio.create_task(
                            NaiveAgent(worker).run(post, parsed),
                            name=f"naive-{post.post_id}",
                        ))
                    case "image_gen":
                        task_queue.add(asyncio.create_task(
                            ImageGenAgent(worker).run(post, parsed),
                            name=f"image_gen-{post.post_id}",
                        ))
                    case "image_edit":
                        task_queue.add(asyncio.create_task(
                            ImageEditAgent(worker).run(post, parsed),
                            name=f"image_edit-{post.post_id}",
                        ))
                    case _:
                        task_queue.add(asyncio.create_task(
                            worker.make_post(
                                content="不明なコマンドです。",
                                in_reply_to=post.post_id,
                            ),
                            name=f"unknown-{post.post_id}",
                        ))

                fetch_task = asyncio.create_task(
                    notif_iter.__anext__(), name="fetch-next"
                )


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(main())
