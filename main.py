import json
import logging
from time import sleep

from bs4 import BeautifulSoup
from truthbrush import Api
from sqlite3 import connect

DB = "proceed.sqlite"

logging.getLogger(__name__).setLevel(logging.WARNING)

conn = connect(DB)
cursor = conn.cursor()
cursor.execute(R"CREATE TABLE IF NOT EXISTS proceed_table(id INTEGER PRIMARY KEY UNIQUE NOT NULL )")
cursor.execute(R"CREATE UNIQUE INDEX IF NOT EXISTS proceed_index ON proceed_table(id)")
cursor.close()
api = Api()

# noinspection PyProtectedMember
api._Api__check_login()

print(api.auth_id)

params = {"types[]": ["mention"]}
# noinspection PyProtectedMember
notifications = api._get(url="/v1/notifications", params=params)

for notification in notifications:
    print("\n\n")
    print(json.dumps(notification, ensure_ascii=False))
    if not notification.get("status"):
        continue
    try:
        if notification["status"]["in_reply_to"]["account"]["username"] != "mizuha_bot":
            continue
    except:
        pass
    # if not notification["status"].get("in_reply_to"):
    #     continue
    # if not notification["status"]["in_reply_to"].get("account"):
    #     continue
    # if not notification["status"]["in_reply_to"]["account"].get("username"):
    #     continue
    # print(f'{notification["status"]["in_reply_to"]["account"]["username"]=}')
    if not notification["status"].get("content"):
        continue
    print(f'{notification["status"]["content"]=}')

    mention_post = notification["status"]["id"]  # このIDのポストに返信
    post_html = notification["status"]["content"]
    post_html = BeautifulSoup(post_html, "lxml")
    list(map(lambda mention: mention.decompose(), post_html.find_all("a", {"class": "mention"})))
    print(post_html)

# while True:
# print("\n\n\n\n")
# noinspection PyProtectedMember
# notifications = api._get(url="/v1/notifications", params=params)
# if not notifications: break
# for b in notifications:
#     cursor = conn.cursor()
#     if not b.get("status"):
#         break
#     # try:
#     if cursor.execute("SELECT EXISTS (SELECT 1 FROM proceed_table WHERE id=?)", (b["id"],)).fetchone()[0]:
#         print("exist")
#         exit()
#     # print(exists)
#     # print(json.dumps(b, indent=4))
#     # print(b["id"])
#     # if "mentions" in b["status"].keys():
#     #     print(b["status"]["mentions"])
#     # print(b["status"]["account"].keys())
#     print(f'{b["status"]=}')
#     print(type(b))
#     post_html = BeautifulSoup(b["status"]["content"], "lxml")
#     list(map(lambda mention: mention.decompose(), post_html.find_all("a", {"class": "mention"})))
#     post_text = post_html.get_text()
#     print(post_text)
#     params["max_id"] = b["id"]
#     # cursor.execute("INSERT INTO proceed_table values (?)", (b["id"],))
#     conn.commit()
#     # except Exception as e:
#     #     print(e)
#     #     conn.rollback()
#     #     exit()
#     # finally:
#     #     cursor.close()
# sleep(5)
