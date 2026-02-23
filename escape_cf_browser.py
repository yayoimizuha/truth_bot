import asyncio
import json
import time
from datetime import datetime
from functools import wraps
from os import environ
from typing import Literal
from aiolimiter import AsyncLimiter
from dotenv import load_dotenv, find_dotenv
# noinspection PyProtectedMember
from scrapling.core.utils._utils import setup_logger
from scrapling.fetchers import AsyncStealthySession
from playwright.async_api import Page

load_dotenv(find_dotenv())


def limit_async(limiter: AsyncLimiter):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            async with limiter:
                return await func(*args, **kwargs)

        return wrapper

    return decorator


_limiter = AsyncLimiter(3, 10)  # 3 requests per 10 seconds


class ContinuousBrowserClass:
    def __init__(self, headless: bool = True):
        self._page = None
        self._token = None
        self._headless = headless
        # noinspection PyArgumentList
        self._session = AsyncStealthySession(headless=headless, humanize=True, solve_cloudflare=True)
        self._last_reloaded = time.time()
        self._last_forced_reload = time.time()  # Track forced reload to avoid Turnstile

    async def __aenter__(self):
        await self._session.__aenter__()
        self._page: Page = await self._session.context.new_page()
        await self.normalize_page_status(is_init=True)
        await asyncio.sleep(3)
        return self

    async def normalize_page_status(self, is_init: bool = False):
        if not self._page.url.startswith("https://truthsocial.com"):
            await self._page.goto("https://truthsocial.com/")
            await asyncio.sleep(5)
        if time.time() - self._last_reloaded > 180:
            await self._page.reload()
            await asyncio.sleep(3)
            self._last_reloaded = time.time()

        # noinspection PyProtectedMember
        if self._session._detect_cloudflare(
                await self._fetch(method="GET", url="https://truthsocial.com/api/v1/truth/policies/pending")
        ) or time.time() - self._last_forced_reload > 1740:  # Force reload every 29 minutes (1740s) to avoid Turnstile
            if not is_init:
                await self._page.close(reason="Cloudflare detected, reopening page to solve it.")
                await self._session.close()
                # noinspection PyArgumentList
                self._session = AsyncStealthySession(headless=self._headless, humanize=True, solve_cloudflare=True)
                await self._session.__aenter__()
                self._page: Page = await self._session.context.new_page()
                # noinspection PyProtectedMember
                await self._page.goto("https://truthsocial.com/")
            # noinspection PyProtectedMember
            await self._session._cloudflare_solver(self._page)
            self._last_forced_reload = time.time()  # Reset forced reload timer after solving Cloudflare
            await asyncio.sleep(5)
            await self._login()
            await asyncio.sleep(3)

    async def _login(self):
        await self.normalize_page_status()
        # noinspection PyUnresolvedReferences
        login_state = await self._fetch(method="GET", url="https://truthsocial.com/api/v1/truth/policies/pending")
        if login_state == '{}':
            return
        # print(login_state)
        if "USER_UNAUTHENTICATED" in login_state:
            tokens = await self._fetch(
                method="POST",
                url="https://truthsocial.com/oauth/v2/token",
                body={"client_id": environ["TRUTHSOCIAL_CLIENT_ID"],
                      "client_secret": environ["TRUTHSOCIAL_CLIENT_SECRET"],
                      "redirect_uri": "urn:ietf:wg:oauth:2.0:oob", "grant_type": "password",
                      "scope": "read write follow push",
                      "username": environ['TRUTHSOCIAL_USERNAME'],
                      "password": environ["TRUTHSOCIAL_PASSWORD"]}
            )
            setup_logger().info(f"GET {tokens=}")
            self._token = json.loads(tokens)["access_token"]
            if not await self._fetch(method="GET", url="https://truthsocial.com/api/v1/truth/policies/pending") == '{}':
                raise Exception("login failed")
        # else:
        #     raise Exception("login failed")

    async def _fetch(self, url: str, body: str | bytes | dict[str, str | int | float] = None, content_type: str = None,
                     headers: dict[str, str] = None, method: Literal["GET", "POST"] = "GET") -> str:

        headers = {
                      "Authorization": f"Bearer {self._token or ''}",
                      "Cache-Control": "no-cache",
                      "Pragma": "no-cache",
                      "Content-Type": content_type or "application/json",
                  } | (headers or {})

        is_bytes = False
        if type(body) is bytes:
            body = list(body)
            is_bytes = True

        return await self._page.evaluate("""
                                         async ([url, method, headers, body, is_bytes, content_type]) => {
                                             if (is_bytes) {
                                                 const arr = new Uint8Array(body);
                                                 body = new Blob([arr], {type: content_type}).stream();
                                             } else if (body !== null && typeof body !== "string") {
                                                 body = JSON.stringify(body);
                                             }
                                             const response = await fetch(url, {
                                                 method: method,
                                                 headers: headers,
                                                 body: body,
                                             });
                                             return await response.text();
                                         }
                                         """, [url, method, headers, body, is_bytes, content_type])

    @limit_async(_limiter)
    async def get(self, **kwargs) -> str:
        await self.normalize_page_status()
        await self._login()
        setup_logger().info(f"GET {kwargs.get('url')}")
        return await self._fetch(method="GET", **kwargs)

    @limit_async(_limiter)
    async def post(self, **kwargs) -> str:
        await self.normalize_page_status()
        await self._login()
        return await self._fetch(method="POST", **kwargs)

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._session.close()


if __name__ == '__main__':
    cb = ContinuousBrowserClass(headless=False)
    asyncio.run(cb.__aenter__())
    while True:
        print(datetime.now(), asyncio.run(cb.get(url="https://truthsocial.com/api/v1/truth/policies/pending")) == '{}')
        time.sleep(60)
