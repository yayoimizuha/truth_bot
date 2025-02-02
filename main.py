import datetime
import json
import logging
import os
import pickle
import random
import shlex
import shutil
import subprocess
from time import sleep

import litellm
import re
from curl_cffi import requests
from pathlib import Path
from typing import Optional
from bs4 import BeautifulSoup
from truthbrush import Api
from sqlite3 import connect
from loguru import logger
from cloudscraper import create_scraper
from truthbrush.api import USER_AGENT, BASE_URL, CLIENT_ID, CLIENT_SECRET, proxies

DB = "proceed.sqlite"
MODEL_BASE = Path(os.environ["MODEL_BASE"])
IMAGE_OUT = Path(os.environ["IMAGE_OUT"])
PROCEED_PICKLE = Path(os.environ["PROCEED_PICKLE"])
if not PROCEED_PICKLE.is_file():
    with PROCEED_PICKLE.open(mode="wb") as f:
        # noinspection PyTypeChecker
        pickle.dump({"XXXXX"}, f)
logging.getLogger(__name__).setLevel(logging.WARNING)

conn = connect(DB)
cursor = conn.cursor()
cursor.execute(R"CREATE TABLE IF NOT EXISTS proceed_table(id INTEGER PRIMARY KEY UNIQUE NOT NULL )")
cursor.execute(R"CREATE UNIQUE INDEX IF NOT EXISTS proceed_index ON proceed_table(id)")
cursor.close()


class WritableApi(Api):
    def get_auth_id(self, username: str, password: str) -> Optional[str]:
        """Logs in to Truth account and returns the session token"""
        url = BASE_URL + "/oauth/token"
        try:
            payload = {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type": "password",
                "username": username,
                "password": password,
                "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
                "scope": "read write follow push",
            }

            sess_req = requests.request(
                "POST",
                url,
                json=payload,
                proxies=proxies,
                impersonate="chrome120",
                headers={
                    "User-Agent": USER_AGENT,
                },
            )
            sess_req.raise_for_status()
        except requests.RequestsError as e:
            logger.error(f"Failed login request: {str(e)}")
            return None

        if not sess_req.json()["access_token"]:
            raise ValueError("Invalid truthsocial.com credentials provided!")

        return sess_req.json()["access_token"]


api = WritableApi()

# noinspection PyProtectedMember,PyUnresolvedReferences
api._Api__check_login()

params = {"types[]": ["mention"]}

config_match = re.compile(r"^\s*?\[([A-z:=0-9\-.\s,]*)]")


def html_to_text(post_html: str) -> str:
    post_html = BeautifulSoup(post_html, "lxml")
    list(map(lambda mention: mention.decompose(), post_html.find_all("a", {"class": "mention"})))
    return post_html.get_text()


def get_all_contents(post_id: int) -> list[dict[str, list | str]]:
    contents = []
    while True:
        # noinspection PyProtectedMember
        status = api._get(url=f"/v1/statuses/{post_id}")
        user_name = status["account"]["username"]
        role = "assistant" if user_name == "mizuha_bot" else "user"
        if not contents:
            contents.append({"role": role, "content": []})
        if contents[-1]["role"] != role:
            contents.append({"role": role, "content": []})

        text_content = html_to_text(status["content"])
        config_span = config_match.search(text_content)
        if config_span is not None:
            text_content = text_content[config_match.search(text_content).span()[1]:]

        contents[-1]["content"].insert(0, {"type": "text", "text": text_content})
        for media in reversed(status["media_attachments"]):
            contents[-1]["content"].insert(0, {"type": "image_url", "image_url": media["url"]})
        if status["in_reply_to_id"] is None:
            return contents
        post_id = status["in_reply_to_id"]


def parse_param(param_string: str, _prompts: list[dict[str, list[dict[str, str]] | str]]) \
        -> dict[str, list[str] | str] | None:
    model_name, *_params = param_string.split(sep=":")
    match model_name:
        case "gemini" | "gemini-1.5-flash" | "gemini-2.0-flash" | "gpt-4o-mini" | "haiku" | "claude-3.5-haiku" | \
             "llm-jp-3-13b-instruct" | "llm-jp-3":
            default_config = {
                "temperature": 1.0,
                "max_tokens": 2000,
                "top_p": None
            }
            # params: list[str]
            for param in _params:
                if param.startswith("temperature="):
                    try:
                        default_config["temperature"] = max(0.0, min(2.0, float(param.removeprefix("temperature="))))
                    except ValueError as e:
                        return {"error": f"failed while parsing temperature. :{e}"}
                if param.startswith("temp="):
                    try:
                        default_config["temperature"] = max(0.0, min(2.0, float(param.removeprefix("temp="))))
                    except ValueError as e:
                        return {"error": f"failed while parsing temp. :{e}"}
                if param.startswith("max_tokens="):
                    try:
                        default_config["max_tokens"] = max(1, min(2500, int(param.removeprefix("max_tokens="))))
                    except ValueError as e:
                        return {"error": f"failed while parsing max_tokens. :{e}"}
                if param.startswith("top_p="):
                    try:
                        default_config["top_p"] = max(0.1, min(1.0, float(param.removeprefix("top_p="))))
                    except ValueError as e:
                        return {"error": f"failed while parsing top_p. :{e}"}

            model_name = "gemini-2.0-flash" if model_name == "gemini" else model_name
            model_name = "claude-3.5-haiku" if model_name == "haiku" else model_name
            model_name = "llm-jp-3-13b-instruct" if model_name == "llm-jp-3" else model_name

            model_name = "gemini/gemini-2.0-flash-exp" if model_name == "gemini-2.0-flash" else model_name
            model_name = "anthropic/claude-3-5-haiku-latest" if model_name == "claude-3.5-haiku" else model_name
            model_name = "ollama/hf.co/alfredplpl/llm-jp-3-13b-instruct-gguf" \
                if model_name == "llm-jp-3-13b-instruct" else model_name

            print("generate text.")
            resp = litellm.completion(model=model_name, messages=_prompts, **default_config)
            return {"resp_text": resp.choices[0].message.content}

        case "flux-dev" | "sd-3.5-large" | "animagine-xl":
            default_config = {
                "seed": 42,
                "cfg-scale": None,
                "sampling-method": "euler_a",
                "batch-count": 1,
                "sizeH": 768,
                "sizeW": 768,
                "neg": None
            }
            match model_name:
                case "flux-dev":
                    pass
                case "sd-3.5-large":
                    default_config["sampling-method"] = "euler"
            _params: list[str]
            for param in _params:
                if param.startswith("seed="):
                    try:
                        default_config["seed"] = int(param.removeprefix("seed="))
                    except ValueError as e:
                        return {"error": f"failed while parsing seed. :{e}"}
                if param.startswith("cfg-scale="):
                    try:
                        default_config["cfg-scale"] = max(1, min(20, int(param.removeprefix("cfg-scale="))))
                    except ValueError as e:
                        return {"error": f"failed while parsing cfg-scale. :{e}"}
                if param.startswith("neg="):
                    try:
                        default_config["neg"] = str(param.removeprefix("neg="))
                    except ValueError as e:
                        return {"error": f"failed while parsing negative prompt. :{e}"}
                if param.startswith("b="):
                    try:
                        default_config["batch-count"] = max(1, min(20, int(param.removeprefix("b="))))
                    except ValueError as e:
                        return {"error": f"failed while parsing batch-count. :{e}"}
                if param.startswith("batch-count="):
                    try:
                        default_config["batch-count"] = max(1, min(20, int(param.removeprefix("batch-count="))))
                    except ValueError as e:
                        return {"error": f"failed while parsing batch-count. :{e}"}
                if param.startswith("sampling-method="):
                    try:
                        method_name = param.removeprefix("sampling-method=")
                        if method_name in ["euler", "euler_a", "heun", "dpm2", "dpm++2s_a", "dpm++2m", "dpm++2mv2",
                                           "ipndm", "ipndm_v", "lcm"]:
                            default_config["sampling-method"] = method_name
                        else:
                            raise ValueError(f"method \"{method_name}\" is not supported.")
                    except ValueError as e:
                        return {"error": f"failed while parsing sampling-method. :{e}"}
                if param.startswith("sizeH="):
                    try:
                        default_config["sizeH"] = max(32, min(1280, int(param.removeprefix("sizeH="))))
                    except ValueError as e:
                        return {"error": f"failed while parsing sizeH. :{e}"}
                if param.startswith("sizeW="):
                    try:
                        default_config["sizeW"] = max(32, min(1280, int(param.removeprefix("sizeW="))))
                    except ValueError as e:
                        return {"error": f"failed while parsing sizeW. :{e}"}

            print("generate image.")
            tmp_img_id = random.randint(1000000, 10000000)
            dest_path = IMAGE_OUT / f"out_{tmp_img_id}"
            shutil.rmtree(dest_path, ignore_errors=True)
            os.makedirs(dest_path)
            command_builder = ["/home/katayama_23266031/local/bin/sd", "-p", _prompts[-1]["content"][-1]["text"],
                               "--sampling-method", default_config["sampling-method"],
                               "-o", str(dest_path / "out"),
                               "-H", str(default_config["sizeH"]), "-W", str(default_config["sizeW"]),
                               "-b", str(default_config["batch-count"]), "--seed", str(default_config["seed"])]

            if default_config["cfg-scale"] is not None:
                command_builder.extend(["--cfg-scale", str(default_config["cfg-scale"])])
            if default_config["neg"] is not None:
                command_builder.extend(["-n", default_config["neg"]])

            match model_name:
                case "flux-dev":
                    command_builder.extend(["--diffusion-model", "flux1-dev-q8_0.gguf"])
                    command_builder.extend(["--vae", "ae.safetensors"])
                    command_builder.extend(["--clip_l", "clip_l.safetensors"])
                    command_builder.extend(["--t5xxl", "t5xxl_fp16.safetensors"])
                case "sd-3.5-large":
                    command_builder.extend(["--model", "sd3.5_large-q8_0.gguf"])
                    command_builder.extend(["--clip_g", "clip_g.safetensors"])
                    command_builder.extend(["--clip_l", "clip_l.safetensors"])
                    command_builder.extend(["--vae", "ae.safetensors"])
                    command_builder.extend(["--t5xxl", "t5xxl_fp16.safetensors"])
                case "animagine-xl":
                    command_builder.extend(["--model", "animagine-xl-4.0.safetensors"])
                    command_builder.extend(["--vae", "ae.safetensors"])

            print(shlex.join(str(p) for p in command_builder))
            with open("sd-out.log", mode="a") as sd_out, open("sd-err.log", mode="a") as sd_err:
                subprocess.run(command_builder, cwd=MODEL_BASE / model_name, stdout=sd_out, stderr=sd_err)
            return {"image_path": list(dest_path.glob("*.png")),
                    "resp_text": "config: {}".format(json.dumps(default_config, indent=2)),
                    }
        case _ as model:
            print(f"unknown model: {model}")
            return {"error": f"{model} is not available."}


def post_reply(destination: int, mention_to: str, resp_text: Optional[str] = None,
               image_path: Optional[list[Path]] = None, error: Optional[str] = None):
    media_attachments = []
    cfs = create_scraper()
    headers = {
        "Authorization": f"Bearer {api.auth_id}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0",
        "Referer": "https://truthsocial.com",
        "Origin": "https://truthsocial.com",
        "Accept": "*/*"
    }
    if image_path is not None:
        for p in image_path:
            resp = cfs.post(
                url="https://truthsocial.com/api/v1/media",
                headers=headers,
                files={"file": p.read_bytes()},
            )
            if resp.status_code != 200:
                print("https://truthsocial.com/api/v1/media", resp.json())
            media_attachments.append(resp.json()["id"])
    # noinspection PyProtectedMember
    resp = cfs.post(
        "https://truthsocial.com/api/v1/statuses",
        headers=headers,
        json={"content_type": "text/plain", "in_reply_to_id": str(destination),
              "media_ids": [str(_id) for _id in media_attachments],
              "poll": None, "quote_id": "",
              "status": resp_text if resp_text is not None else error if error is not None else "",
              "to": [mention_to], "visibility": "public",
              "group_timeline_visible": False}
    )
    if resp.status_code != 200:
        print("https://truthsocial.com/api/v1/statuses", resp.request.body)
        print("https://truthsocial.com/api/v1/statuses", resp.json())


with (open("ollama.log", mode="a") as ollama_log,
      subprocess.Popen(["/home/katayama_23266031/local/bin/ollama", "serve"],
                       env=dict(os.environ, **{"OLLAMA_KEEP_ALIVE": "10s"}),
                       stdout=ollama_log, stderr=ollama_log) as ollama):
    try:
        while True:
            # noinspection PyProtectedMember
            notifications = api._get(url="/v1/notifications", params=params)
            for notification in notifications:
                # print(json.dumps(notification, ensure_ascii=False))

                if not notification.get("status"):
                    continue
                if not notification["status"].get("content"):
                    continue
                in_reply_to = notification["status"]["in_reply_to_id"]
                mention_id = notification["account"]["username"]

                call_point = notification["status"]["id"]  # このIDのポストに返信

                with PROCEED_PICKLE.open(mode="rb") as pickle_file:
                    pickle_data = pickle_file.read()
                    # print(pickle.loads(pickle_data))
                    if call_point in pickle.loads(pickle_data):
                        continue
                # exit()

                # print("")
                post_text = html_to_text(notification["status"]["content"])

                matches = config_match.search(post_text)
                parse_error = matches is None
                if not parse_error:
                    call_param = matches.group(1)
                    try:
                        prompts = get_all_contents(call_point)
                        resp_content = parse_param(call_param, prompts)
                        print(prompts)
                        print(resp_content)
                        post_reply(destination=call_point, mention_to=mention_id, **resp_content)
                    except Exception as e:
                        print(e)
                    with PROCEED_PICKLE.open(mode="r+b") as pickle_file:
                        # noinspection PyTypeChecker
                        proceed = pickle.loads(pickle_file.read())
                        proceed |= {call_point}
                        print(call_point)
                        pickle_file.seek(0)
                        pickle_file.write(pickle.dumps(proceed))

            sleep(20)
            print("[NOW] ", datetime.datetime.now())
    except KeyboardInterrupt as e:
        print("finishing...", e)
        ollama.terminate()
    # print(post_text[matches.span()[1]:])
    # resp = litellm.completion(model="gemini/gemini-1.5-flash-latest", messages=messages)
    # print(resp)
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
