from ts_worker import TruthPost
from sub_agents import AgentClass


class ImageGenAgent(AgentClass):
    """
    [image_gen:...] コマンドへの返信エージェント。
    parse_llm_syntax() の result["prompt"] を元に画像を生成し、投稿する。

    options:
        seed  : 乱数シード (int, 省略可)
        step  : サンプリングステップ数 (int, 省略可)
    """

    async def run(
        self,
        post: TruthPost,
        parsed: dict,
    ) -> None:
        prompt: str = parsed.get("prompt", "").strip()
        options: dict = parsed.get("options", {})

        # TODO: 画像生成バックエンドを呼び出す
        #   例: stable-diffusion-cpp-python を使う場合
        #   from stable_diffusion_cpp import StableDiffusion
        #   sd = StableDiffusion(model_path="...", ...)
        #   images = sd.txt_to_img(prompt=prompt, seed=int(options.get("seed", -1)), ...)
        #   image_bytes = images[0]  # PIL.Image or bytes
        #   await self.create_post(content=prompt, in_reply_to=post.post_id, media=[image_bytes])

        raise NotImplementedError("ImageGenAgent はまだ実装されていません。")
