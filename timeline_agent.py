import asyncio
import logging

from dotenv import find_dotenv, load_dotenv

from sns_agent import AgentService

load_dotenv(find_dotenv())
SUPPRESSED_ACCESS_PATH = "/api/v1/alerts"


class AccessPathFilter(logging.Filter):
    def __init__(self, suppressed_path: str):
        super().__init__()
        self._suppressed_path = suppressed_path

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._suppressed_path:
            return True
        return self._suppressed_path not in record.getMessage()


def configure_access_log_filters() -> None:
    root_logger = logging.getLogger()
    if any(isinstance(filter_, AccessPathFilter) for filter_ in root_logger.filters):
        return

    filter_ = AccessPathFilter(SUPPRESSED_ACCESS_PATH)
    root_logger.addFilter(filter_)
    for handler in root_logger.handlers:
        if any(isinstance(existing, AccessPathFilter) for existing in handler.filters):
            continue
        handler.addFilter(filter_)


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
    configure_access_log_filters()
    asyncio.run(main())
