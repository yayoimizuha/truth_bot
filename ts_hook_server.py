import asyncio
import base64
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from os import environ

from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from scrapling.fetchers import StealthySession

load_dotenv(find_dotenv())
SUPPRESSED_ACCESS_PATH = "/api/v1/alerts?category=mentions&follow_mentions=false"


class AccessPathFilter(logging.Filter):
    def __init__(self, suppressed_path: str):
        super().__init__()
        self._suppressed_path = suppressed_path

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._suppressed_path:
            return True
        message = record.getMessage()
        return self._suppressed_path not in message


def configure_access_log_filters() -> None:
    logger = logging.getLogger("uvicorn.access")
    if any(isinstance(filter_, AccessPathFilter) for filter_ in logger.filters):
        return
    logger.addFilter(AccessPathFilter(SUPPRESSED_ACCESS_PATH))


configure_access_log_filters()


class BrowserProxy:
    def __init__(self, headless: bool = False):
        self._headless = headless
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scrapling-browser")
        self._session: StealthySession = None
        self._page = None
        self._token: str | None = None
        self._last_reloaded = 0.0
        self._last_cf_reset = time.time()

    async def start(self):
        await asyncio.get_running_loop().run_in_executor(self._executor, self._start)
        return self

    def _start(self):
        self._session = StealthySession(headless=self._headless, humanize=True, solve_cloudflare=True)
        self._session.start()
        self._page = self._session.context.new_page()
        self._last_cf_reset = time.time()
        self._normalize(force_init=True)
        time.sleep(3)
        self._login()

    def _wait_for_page_ready(self):
        if self._page is None:
            raise RuntimeError("browser page is not initialized")

        self._page.wait_for_load_state("domcontentloaded")

    async def close(self):
        try:
            await asyncio.get_running_loop().run_in_executor(self._executor, self._close)
        finally:
            self._executor.shutdown(wait=True)

    def _close(self):
        if self._page is not None:
            self._page.close()
            self._page = None
        if self._session is not None:
            self._session.close()
            self._session = None
        self._token = None

    async def normalize(self):
        await asyncio.get_running_loop().run_in_executor(self._executor, self._normalize)

    def _normalize(self, force_init: bool = False):
        if self._page is None or self._session is None:
            raise RuntimeError("browser session is not initialized")

        if not self._page.url.startswith("https://truthsocial.com"):
            self._page.goto("https://truthsocial.com/")
            self._wait_for_page_ready()
            time.sleep(5)
            self._last_reloaded = time.time()
        elif time.time() - self._last_reloaded > 180:
            self._page.reload()
            self._wait_for_page_ready()
            time.sleep(3)
            self._last_reloaded = time.time()

        self._ensure_cloudflare(force_init=force_init)

    def _ensure_cloudflare(self, force_init: bool = False):
        pending = self._raw_fetch(
            method="GET",
            url="https://truthsocial.com/api/v1/truth/policies/pending",
        )
        session_expired = time.time() - self._last_cf_reset > 1740
        # noinspection PyProtectedMember
        if not self._session._detect_cloudflare(pending) and not session_expired:
            return

        if not force_init:
            self._page.close()
            self._session.close()
            self._token = None
            self._session = StealthySession(headless=self._headless, humanize=True, solve_cloudflare=True)
            self._session.start()
            self._page = self._session.context.new_page()
            self._page.goto("https://truthsocial.com/")
            self._wait_for_page_ready()
            self._last_cf_reset = time.time()

        # noinspection PyProtectedMember
        self._session._cloudflare_solver(self._page)
        self._wait_for_page_ready()
        self._last_cf_reset = time.time()
        time.sleep(5)
        self._login()

    def _login(self):
        login_state = self._raw_fetch(
            method="GET",
            url="https://truthsocial.com/api/v1/truth/policies/pending",
        )
        if login_state == "{}":
            return
        if "USER_UNAUTHENTICATED" not in login_state:
            raise RuntimeError(f"unexpected login state: {login_state}")

        tokens = self._raw_fetch(
            method="POST",
            url="https://truthsocial.com/oauth/v2/token",
            json_body={
                "client_id": environ["TRUTHSOCIAL_CLIENT_ID"],
                "client_secret": environ["TRUTHSOCIAL_CLIENT_SECRET"],
                "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
                "grant_type": "password",
                "scope": "read write follow push",
                "username": environ["TRUTHSOCIAL_USERNAME"],
                "password": environ["TRUTHSOCIAL_PASSWORD"],
            },
        )
        self._token = json.loads(tokens)["access_token"]
        if self._raw_fetch(
            method="GET",
            url="https://truthsocial.com/api/v1/truth/policies/pending",
        ) != "{}":
            raise RuntimeError("login failed")

    async def page_url(self):
        return await asyncio.get_running_loop().run_in_executor(self._executor, self._page_url)

    def _page_url(self):
        return self._page.url if self._page is not None else ""

    async def fetch(self, request: Request, path: str):
        body = await request.body()
        headers = self._build_forward_headers(request)
        url = f"https://truthsocial.com/{path.lstrip('/')}"
        if request.url.query:
            url = f"{url}?{request.url.query}"
        return await self.fetch_url(request.method, url, body if body else None, headers)

    async def fetch_url(
        self,
        method: str,
        url: str,
        body_bytes: bytes | None = None,
        headers: dict[str, str] | None = None,
    ):
        headers = headers or {}

        return await asyncio.get_running_loop().run_in_executor(
            self._executor,
            self._fetch,
            method,
            url,
            body_bytes,
            headers,
        )

    def _fetch(self, method: str, url: str, body_bytes: bytes | None, headers: dict[str, str]):
        self._normalize()
        self._login()
        return self._raw_fetch(
            method=method,
            url=url,
            body_bytes=body_bytes,
            headers=headers,
            return_metadata=True,
        )

    @staticmethod
    def _build_forward_headers(request: Request) -> dict[str, str]:
        excluded = {
            "host",
            "content-length",
            "connection",
            "transfer-encoding",
            "accept-encoding",
        }
        headers = {key: value for key, value in request.headers.items() if key.lower() not in excluded}
        headers.pop("authorization", None)
        return headers

    def _raw_fetch(
        self,
        *,
        method: str,
        url: str,
        body_bytes: bytes | None = None,
        json_body: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        return_metadata: bool = False,
    ):
        if self._page is None:
            raise RuntimeError("browser page is not initialized")

        merged_headers = {
            "Authorization": f"Bearer {self._token}" if self._token else "",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            **(headers or {}),
        }
        if json_body is not None:
            merged_headers.setdefault("Content-Type", "application/json")

        payload = {
            "url": url,
            "method": method,
            "headers": merged_headers,
            "bodyBase64": base64.b64encode(body_bytes).decode("ascii") if body_bytes is not None else None,
            "jsonBody": json_body,
            "returnMetadata": return_metadata,
        }
        for attempt in range(2):
            try:
                return self._page.evaluate(
                    """
                    async ({url, method, headers, bodyBase64, jsonBody, returnMetadata}) => {
                        const cleanHeaders = Object.fromEntries(
                            Object.entries(headers).filter(([, value]) => value !== "")
                        );
                        const decodeBase64 = (value) => {
                            const binary = atob(value);
                            const bytes = new Uint8Array(binary.length);
                            for (let i = 0; i < binary.length; i += 1) {
                                bytes[i] = binary.charCodeAt(i);
                            }
                            return bytes;
                        };
                        const encodeBase64 = (bytes) => {
                            let binary = "";
                            for (let i = 0; i < bytes.length; i += 0x8000) {
                                binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
                            }
                            return btoa(binary);
                        };

                        let body;
                        if (bodyBase64 !== null) {
                            body = new Blob([decodeBase64(bodyBase64)]);
                        } else if (jsonBody !== null) {
                            body = JSON.stringify(jsonBody);
                        }

                        const response = await fetch(url, {
                            method,
                            headers: cleanHeaders,
                            body,
                        });

                        if (!returnMetadata) {
                            return await response.text();
                        }

                        return {
                            status: response.status,
                            headers: Object.fromEntries(response.headers.entries()),
                            bodyBase64: encodeBase64(new Uint8Array(await response.arrayBuffer())),
                        };
                    }
                    """,
                    payload,
                )
            except Exception:
                if attempt == 1:
                    raise
                self._wait_for_page_ready()
                time.sleep(0.25)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.browser = await BrowserProxy().start()
    try:
        yield
    finally:
        await app.state.browser.close()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health(request: Request):
    try:
        await request.app.state.browser.normalize()
        return {"ok": True, "page_url": await request.app.state.browser.page_url()}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.api_route("/", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def proxy(request: Request, path: str = ""):
    try:
        response = await request.app.state.browser.fetch(request, path)
    except Exception as exc:
        return Response(content=f"Browser proxy error: {exc}", status_code=502)
    return Response(
        content=base64.b64decode(response["bodyBase64"]) if response["bodyBase64"] else b"",
        status_code=response["status"],
        media_type=response["headers"].get("content-type"),
    )
