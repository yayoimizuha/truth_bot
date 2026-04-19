import asyncio
import time
import unittest

from sns_agent.gpu_tasks import GPUTaskLimiter


class GPUTaskLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def test_limiter_serializes_when_max_concurrency_is_one(self):
        limiter = GPUTaskLimiter(1)
        entered: list[float] = []

        async def job() -> float:
            entered.append(time.perf_counter())
            await asyncio.sleep(0.05)
            return entered[-1]

        started = time.perf_counter()
        await asyncio.gather(limiter.run(job), limiter.run(job))
        elapsed = time.perf_counter() - started

        self.assertEqual(len(entered), 2)
        self.assertGreaterEqual(elapsed, 0.095)
