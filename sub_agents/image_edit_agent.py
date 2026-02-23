from ts_worker import TruthPost
from sub_agents import AgentClass


class ImageEditAgent(AgentClass):
    """
    [image_edit:...] コマンドへの返信エージェント。
    ユーザーが添付した画像を編集し、投稿する。

    options:
        seed  : 乱数シード (int, 省略可)
        step  : サンプリングステップ数 (int, 省略可)
        strength : img2img の変換強度 0.0–1.0 (float, 省略可)
    """

    async def run(
        self,
        post: TruthPost,
        parsed: dict,
    ) -> None:
        prompt: str = parsed.get("prompt", "").strip()
        options: dict = parsed.get("options", {})

        if not post.media_ids:
            await self.create_post(
                content="画像編集を行うには、投稿に画像を添付してください。",
                in_reply_to=post.post_id,
            )
            return

        # TODO: 入力画像を取得し、img2img で編集する
        #   例: stable-diffusion-cpp-python を使う場合
        #   from aiohttp import ClientSession
        #   async with ClientSession() as session:
        #       input_bytes = await (await session.get(post.media_ids[0].url)).read()
        #   from stable_diffusion_cpp import StableDiffusion
        #   sd = StableDiffusion(model_path="...", ...)
        #   images = sd.img_to_img(
        #       image=input_bytes,
        #       prompt=prompt,
        #       strength=float(options.get("strength", 0.75)),
        #       seed=int(options.get("seed", -1)),
        #   )
        #   await self.create_post(content=prompt, in_reply_to=post.post_id, media=[images[0]])

        raise NotImplementedError("ImageEditAgent はまだ実装されていません。")
