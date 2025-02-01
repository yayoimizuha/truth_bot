import logging
import os
import random
import shlex
import shutil
import subprocess
from os.path import basename

import litellm
from pathlib import Path
from time import sleep
from typing import Optional
from bs4 import BeautifulSoup
from truthbrush import Api
from sqlite3 import connect
import re

DB = "proceed.sqlite"
MODEL_BASE = Path("/home/katayama_23266031/models/")
IMAGE_OUT = Path("/home/katayama_23266031/image_dest")
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


def parse_param(param_string: str, prompts: list[dict[str, list[dict[str, str]] | str]]) \
        -> dict[str, list[str] | str] | None:
    model_name, *params = param_string.split(sep=":")
    match model_name:
        case "gemini" | "gemini-1.5-flash" | "gemini-2.0-flash" | "gpt-4o-mini" | "haiku" | "claude-3.5-haiku" | \
             "llm-jp-3-13b-instruct" | "llm-jp-3":
            default_config = {
                "temperature": 1.0,
                "max_tokens": 2000,
                "top_p": None
            }
            params: list[str]
            for param in params:
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
            model_name = "claude-3-5-haiku-latest" if model_name == "claude-3.5-haiku" else model_name
            model_name = "ollama/hf.co/alfredplpl/llm-jp-3-13b-instruct-gguf" if model_name == "llm-jp-3-13b-instruct" else model_name

            print("generate text.")
            resp = litellm.completion(model=model_name, messages=prompts, **default_config)
            return {"resp_text": resp.choices[0].message.content}

        case "flux-dev" | "sd-3.5-large" | "animagine-xl":
            default_config = {
                "seed": 42,
                "cfg-scale": None,
                "sampling-method": "euler_a",
                "batch-count": 1,
                "sizeH": 512,
                "sizeW": 512,
                "neg": None
            }
            params: list[str]
            for param in params:
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
                        default_config["sizeH"] = max(32, min(1024, int(param.removeprefix("sizeH="))))
                    except ValueError as e:
                        return {"error": f"failed while parsing sizeH. :{e}"}
                if param.startswith("sizeW="):
                    try:
                        default_config["sizeW"] = max(32, min(1024, int(param.removeprefix("sizeW="))))
                    except ValueError as e:
                        return {"error": f"failed while parsing sizeW. :{e}"}

            print("generate image.")
            tmp_img_id = random.randint(1000000, 10000000)
            dest_path = IMAGE_OUT / f"out_{tmp_img_id}"
            shutil.rmtree(dest_path, ignore_errors=True)
            os.makedirs(dest_path)
            command_builder = ["sd", "-p", prompts[-1]["content"][-1]["text"],
                               "--sampling-method", default_config["sampling-method"],
                               "-o", dest_path / "out",
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
                    command_builder.extend(["--diffusion-model", "sd3.5_large-q8_0.gguf"])
                    command_builder.extend(["--clip_g", "clip_g.safetensors"])
                    command_builder.extend(["--clip_l", "clip_l.safetensors"])
                    command_builder.extend(["--t5xxl", "t5xxl_fp8_e4m3fn_scaled.safetensors"])
                case "animagine-xl":
                    command_builder.extend(["--model", "animagine-xl-4.0.safetensors"])
                    command_builder.extend(["--vae", "ae.safetensors"])

            print(shlex.join(str(p) for p in command_builder))
            subprocess.run(command_builder, cwd=MODEL_BASE / model_name)
            return {"image_path": list(dest_path.glob("*.png"))}
        case _ as model:
            print(f"unknown model: {model}")
            return {"error": f"{model} is not available."}


def post_reply(destination: int, resp_text: Optional[str] = None, image_path: Optional[list[str]] = None,
               error: Optional[str] = None):
    media_attachments = []
    if image_path is not None:
        for p in image_path:
            # noinspection PyProtectedMember
            resp = api._make_session().post(
                "https://truthsocial.com/api/v1/media",
                impersonate="chrome123",
                headers={
                    "Authorization": "Bearer " + api.auth_id,
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
                        " Chrome/123.0.0.0 Safari/537.36"
                    ),
                },
                files={"file": (basename(p), open(p, mode="rb"), "image/png")}
            )
            media_attachments.append(resp.json()["id"])
    # noinspection PyProtectedMember
    api._make_session().post(
        "https://truthsocial.com/api/v1/statuses",
        impersonate="chrome123",
        headers={
            "Authorization": "Bearer " + api.auth_id,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
                " Chrome/123.0.0.0 Safari/537.36"
            ),
        },
        data={"content_type": "text/plain", "in_reply_to_id": str(destination),
              "media_ids": [str(_id) for _id in media_attachments],
              "poll": None, "quote_id": "",
              "status": resp_text if resp_text is not None else error if error is not None else "",
              "to": [], "visibility": "public",
              "group_timeline_visible": True}
    )


with subprocess.Popen(["ollama", "serve"], env=dict(os.environ, **{"OLLAMA_KEEP_ALIVE": "10s"})) as ollama:
    while True:
        notifications = api._get(url="/v1/notifications", params=params)
        for notification in notifications:
            # print(json.dumps(notification, ensure_ascii=False))
            if not notification.get("status"):
                continue
            print("")
            if not notification["status"].get("content"):
                continue
            in_reply_to = notification["status"]["in_reply_to_id"]

            call_point = notification["status"]["id"]  # このIDのポストに返信
            post_text = html_to_text(notification["status"]["content"])

            matches = config_match.search(post_text)
            parse_error = matches is None
            if not parse_error:
                call_param = matches.group(1)
                prompts = get_all_contents(call_point)
                resp_content = parse_param(call_param, prompts)
                print(prompts)
                print(resp_content)
                post_reply(destination=call_point, **resp_content)

        break
    print("finishing...")
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
