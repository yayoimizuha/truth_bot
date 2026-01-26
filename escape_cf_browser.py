import json
import time
from datetime import datetime
from os import environ
from typing import Literal
from dotenv import load_dotenv, find_dotenv
from scrapling.fetchers import StealthySession

load_dotenv(find_dotenv())


class ContinuousBrowserClass:
    def __init__(self, headless: bool = True):
        self._token = None
        self._session = StealthySession(headless=headless, humanize=True, solve_cloudflare=True)
        self._session.__enter__()
        self._page = self._session.context.new_page()
        self._last_reloaded = time.time()
        self.normalize_page_status(is_init=True)

    def normalize_page_status(self, is_init: bool = False):
        if not self._page.url.startswith("https://truthsocial.com"):
            self._page.goto("https://truthsocial.com/")
            time.sleep(5)
        if time.time() - self._last_reloaded > 180:
            self._page.reload()
            self._last_reloaded = time.time()

        # noinspection PyProtectedMember
        if self._session._detect_cloudflare(
                self._fetch(method="GET", url="https://truthsocial.com/api/v1/truth/policies/pending")
        ):
            if not is_init:
                self._page.close(reason="Cloudflare detected, reopening page to solve it.")
                self._session.close()
                self._session = StealthySession(headless=False, humanize=True, solve_cloudflare=True)
                self._session.__enter__()
                self._page = self._session.context.new_page()
                # noinspection PyProtectedMember
                self._page.goto("https://truthsocial.com/")
            # noinspection PyProtectedMember
            self._session._cloudflare_solver(self._page)
            time.sleep(5)
            self._login()

    def _login(self):
        self.normalize_page_status()
        login_state = self._fetch(method="GET", url="https://truthsocial.com/api/v1/truth/policies/pending")
        if login_state == '{}':
            return
        # print(login_state)
        if "USER_UNAUTHENTICATED" in login_state:
            tokens = self._fetch(
                method="POST",
                url="https://truthsocial.com/oauth/v2/token",
                body={"client_id": environ["TRUTHSOCIAL_CLIENT_ID"],
                      "client_secret": environ["TRUTHSOCIAL_CLIENT_SECRET"],
                      "redirect_uri": "urn:ietf:wg:oauth:2.0:oob", "grant_type": "password",
                      "scope": "read write follow push",
                      "username": environ['TRUTHSOCIAL_USERNAME'],
                      "password": environ["TRUTHSOCIAL_PASSWORD"]}
            )
            print(datetime.now(), f"{tokens=}")
            self._token = json.loads(tokens)["access_token"]
            if not self._fetch(method="GET", url="https://truthsocial.com/api/v1/truth/policies/pending") == '{}':
                raise Exception("login failed")
        # else:
        #     raise Exception("login failed")

    def _fetch(self, url: str, body: str | bytes | dict[str, str | int | float] = None, content_type: str = None,
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

        return self._page.evaluate("""
                                   async ([url, method, headers, body, is_bytes, content_type]) => {
                                       if (is_bytes) {
                                           const arr = new Uint8Array(body);
                                           body = new Blob([arr], {type: content_type}).stream();
                                       } else if (body !== null) {
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

    def get(self, **kwargs) -> str:
        self.normalize_page_status()
        self._login()
        return self._fetch(method="GET", **kwargs)

    def post(self, **kwargs) -> str:
        self.normalize_page_status()
        self._login()
        return self._fetch(method="POST", **kwargs)

    def __del__(self):
        self._session.close()


if __name__ == '__main__':
    cb = ContinuousBrowserClass(headless=False)
    while True:
        print(datetime.now(), cb.get(url="https://truthsocial.com/api/v1/truth/policies/pending") == '{}')
        time.sleep(60)
