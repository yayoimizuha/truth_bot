import asyncio
import logging

from dotenv import find_dotenv, load_dotenv

from sns_agent import AgentService

load_dotenv(find_dotenv())


async def main() -> None:
    service = AgentService()
    try:
        await service.run_forever()
    finally:
        await service.aclose()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(main())
