import base64
import json
import sqlite3
import time
from io import BytesIO
from os import environ
from typing import Literal, Optional

from PIL import Image
from box import Box
from dotenv import load_dotenv, find_dotenv
from scrapling.fetchers import StealthySession
from playwright.sync_api import Page

load_dotenv(find_dotenv())

APP_TOKEN = ""
USER_ID = -1


def login_action(page: Page):
    def get_token(_page: Page):
        _localstorage_data = _page.evaluate("window.localStorage.getItem('truth:auth');")
        print(f"{_localstorage_data=}")
        USER_DATA = json.loads(_localstorage_data)["users"][
            f"https://truthsocial.com/@{environ['TRUTHSOCIAL_USERNAME']}"]
        global APP_TOKEN
        APP_TOKEN = USER_DATA["access_token"]
        global USER_ID
        USER_ID = int(USER_DATA["id"])

    page.wait_for_timeout(3000)
    if page.locator('button:has(span:text-is("Sign In"))').count() == 0:
        get_token(page)
        return
    if page.locator('div[id=cookiescript_accept]').count() != 0:
        page.focus('div[id=cookiescript_accept]')
        page.keyboard.press(key="Enter")
    page.wait_for_timeout(500)
    page.focus('button:has(span:text-is("Sign In"))')
    page.keyboard.press(key="Enter")
    page.wait_for_timeout(1000)

    page.locator('input[name="username"]').focus()
    page.wait_for_timeout(100)
    page.type(selector='input[name="username"]', text=environ["TRUTHSOCIAL_USERNAME"])
    page.wait_for_timeout(100)
    page.locator('input[name="password"]').focus()
    page.wait_for_timeout(100)
    page.type(selector='input[name="password"]', text=environ["TRUTHSOCIAL_PASSWORD"])
    page.wait_for_timeout(100)

    page.wait_for_timeout(500)

    page.focus('button[type="submit"]')
    page.keyboard.press(key="Enter")
    page.wait_for_timeout(3000)
    get_token(page)


def fetch_in_browser(page: Page, url: str, method: Literal["GET", "POST"] = "GET", headers=None, body: dict = None):
    try:
        page.evaluate(
            f"""async () => {{
            const response = await fetch("https://truthsocial.com/api/v1/bookmarks/statuses", {{
                headers: {{"Authorization":" Bearer {APP_TOKEN}"}},
                method: "GET",
            }});
            return await response.json();
    }}""")
    except Exception as e:
        print(e)
        raise Exception("AuthenticationError")

    if headers is None:
        headers = dict()
    headers |= {"Authorization": f"Bearer {APP_TOKEN}", "Content-Type": "application/json"}
    _eval = page.evaluate(
        f"""async (body) => {{
        const response = await fetch("{url}", {{
            headers: {json.dumps(headers)},
            method: "{method}",
            body: body
        }});
        return await response.json();
        }}""", json.dumps(body) if body is not None else None)
    # print(_eval)
    return _eval


def get_chat_history(page: Page, post_id: int):
    histories = [fetch_in_browser(page, url=f"https://truthsocial.com/api/v1/statuses/{post_id}")]
    while True:
        _post_id: int = post_id
        ancestors = fetch_in_browser(page, url=f"https://truthsocial.com/api/v2/statuses/{_post_id}/context/ancestors")
        histories.extend(ancestors)
        if len(ancestors) != 20:
            break
        _post_id = int(ancestors[-1]["id"])
    # print(histories)
    return [Box(history) for history in histories]


def post_reply(page: Page, post_id: int, content: str, images: list[Image.Image] = None):
    # getting sample images
    if images is None:
        images = []
        for _ in range(2):
            image_byte = page.request.get("https://picsum.photos/800/600").body()
            image = Image.open(BytesIO(image_byte), formats=["JPEG"]).convert("RGB")
            images.append(image)

    media_ids = []
    for image in images:
        buffered = BytesIO()
        image.save(buffered, format="PNG")
        img_str = "data:image/png;base64," + base64.b64encode(buffered.getvalue()).decode()
        uploaded = page.evaluate(f"""
                async (imgBase64) => {{
                    const res = await fetch(imgBase64);
                    const blob = await res.blob();
                    const formData = new FormData();
                    formData.append('file', blob, 'image.png');

                    const response = await fetch("https://truthsocial.com/api/v1/media", {{
                        method: "POST",
                        headers: {{ "Authorization": "Bearer {APP_TOKEN}" }},
                        body: formData
                    }});
                    return await response.json();
                }}
            """, img_str)
        print("uploaded image:", uploaded["url"])
        media_ids.append(uploaded["id"])
    fetch_in_browser(page=page, url="https://truthsocial.com/api/v1/statuses", method="POST",
                     body={"content_type": "text/plain",
                           "in_reply_to_id": str(post_id),
                           "media_ids": media_ids,
                           "poll": None,
                           "published": True,
                           "quote_id": None,
                           "status": "やぁやぁ！！テストリプライだよ!" + "\n\n\n\n" + content,
                           "title": "",
                           "visibility": "public",
                           "group_timeline_visible": False})


def process_post(page: Page, post_id: int):
    print(f"==== getting chat history =[{post_id}]===")
    histories = get_chat_history(page, post_id)
    # print(histories)
    post_reply(page=page, post_id=post_id, content=histories[-1].content)


def main(page: Page):
    _last_reload = time.time()
    while True:
        with sqlite3.connect("history.db") as conn:
            notifications = fetch_in_browser(page,
                                             "https://truthsocial.com/api/v1/alerts?category=mentions&follow_mentions=true")
            for notification in notifications:
                notification = Box(notification)
                if conn.execute("SELECT COUNT(*) FROM proceed WHERE id=?;", (int(notification.status.id),)).fetchone()[
                    0] != 0:
                    continue
                conn.execute("REPLACE INTO proceed(id,complete) VALUES(?,FALSE);", (int(notification.status.id),))
                try:
                    process_post(page, int(notification.status.id))
                except Exception as e:
                    print(f"Error processing post {notification.status.id}: {e}")
                finally:
                    conn.execute("REPLACE INTO proceed(id,complete) VALUES(?,TRUE);", (int(notification.status.id),))
                break
            if int(time.time() - _last_reload) > 180:
                page.reload()
                _last_reload = time.time()
            conn.commit()
            time.sleep(10)


sqlite3.connect("history.db").execute(
    "CREATE TABLE IF NOT EXISTS proceed(id INTEGER PRIMARY KEY,complete BOOLEAN NOT NULL);"
)
while True:
    with StealthySession(headless=True, humanize=True) as session:
        session.fetch("https://truthsocial.com", solve_cloudflare=True, page_action=login_action)
        print(f"token:{APP_TOKEN}")
        session.fetch(url="https://truthsocial.com", page_action=lambda page: main(page), solve_cloudflare=True)
    time.sleep(60)
