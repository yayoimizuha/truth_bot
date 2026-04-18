from __future__ import annotations

from dataclasses import dataclass
import os
import re

import httpx
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider

from .media import ImageGenerator, VideoGenerator
from .schemas import AgentResponse, ImageGenerationRequest, NormalizedPost, VideoGenerationRequest

MENTION_RE = re.compile(r"@([A-Za-z0-9_.-]+)")


@dataclass(slots=True)
class ResponderDeps:
    image_generator: ImageGenerator
    video_generator: VideoGenerator
    response: AgentResponse


class LLMResponder:
    def __init__(self, image_generator: ImageGenerator, video_generator: VideoGenerator):
        self._image_generator = image_generator
        self._video_generator = video_generator
        self._api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("LLM_API_KEY") or ""
        self._model = os.getenv("LLM_MODEL", "openai/gpt-4.1-mini")
        self._site_url = os.getenv("LLM_HTTP_REFERER")
        self._site_name = os.getenv("LLM_X_TITLE")
        self._http_client = httpx.AsyncClient(timeout=120.0)
        self._agent = self._build_agent()

    async def respond(self, chain: list[NormalizedPost], target_post: NormalizedPost) -> AgentResponse:
        if not self._api_key:
            return AgentResponse(
                text="OPENROUTER_API_KEY が未設定のため、通常会話応答を生成できません。",
                mentions=[],
            )
        response = AgentResponse(text="")
        deps = ResponderDeps(
            image_generator=self._image_generator,
            video_generator=self._video_generator,
            response=response,
        )
        result = await self._agent.run(
            user_prompt=self._build_prompt(chain, target_post),
            deps=deps,
        )
        response.text = result.output.strip()
        response.mentions = [match.group(1) for match in MENTION_RE.finditer(response.text)]
        response.validate()
        return response

    async def aclose(self) -> None:
        await self._http_client.aclose()

    def _build_agent(self) -> Agent[ResponderDeps, str]:
        provider = OpenRouterProvider(
            api_key=self._api_key,
            app_url=self._site_url,
            app_title=self._site_name,
            http_client=self._http_client,
        )
        model = OpenRouterModel(self._model, provider=provider)
        settings = self._build_model_settings()
        agent = Agent(
            model,
            deps_type=ResponderDeps,
            output_type=str,
            instructions=(
                "あなたはSNS返信専用のAIエージェントです。"
                "通知の対象投稿と、その親投稿列だけを文脈として使ってください。"
                "自発投稿はしません。"
                "必要なときだけ tool を使って画像または動画を生成してください。"
                "最終出力はプレーンテキストの自然文です。Markdown は使わないでください。"
                "画像は最大4枚、動画は最大1件で、両方同時には使えません。"
            ),
            model_settings=settings,
            retries=1,
        )

        @agent.tool
        async def generate_image(
            ctx: RunContext[ResponderDeps],
            prompt: str,
            model: str | None = None,
            size: str | None = None,
            steps: int | None = None,
            cfg_scale: float | None = None,
            seed: int | None = None,
            count: int = 1,
            negative: str | None = None,
            sampler: str | None = None,
        ) -> str:
            """画像を生成して返信に追加する。動画と同時には使わない。"""
            if ctx.deps.response.video is not None:
                raise RuntimeError("video already selected; cannot generate images")
            request = ImageGenerationRequest(
                prompt=prompt,
                model=model,
                size=size,
                steps=steps,
                cfg_scale=cfg_scale,
                seed=seed,
                count=min(max(count, 1), 4),
                negative=negative,
                sampler=sampler,
            )
            ctx.deps.response.images.extend(await ctx.deps.image_generator.generate(request))
            ctx.deps.response.images = ctx.deps.response.images[:4]
            return f"{len(ctx.deps.response.images)} image(s) prepared"

        @agent.tool
        async def generate_video(
            ctx: RunContext[ResponderDeps],
            prompt: str,
            model: str | None = None,
            duration_seconds: int | None = None,
            size: str | None = None,
            fps: int | None = None,
            seed: int | None = None,
        ) -> str:
            """動画を1件生成して返信に追加する。画像と同時には使わない。"""
            if ctx.deps.response.images:
                raise RuntimeError("images already selected; cannot generate video")
            request = VideoGenerationRequest(
                prompt=prompt,
                model=model,
                duration_seconds=duration_seconds,
                size=size,
                fps=fps,
                seed=seed,
            )
            ctx.deps.response.video = await ctx.deps.video_generator.generate(request)
            return "video prepared"

        return agent

    @staticmethod
    def _build_prompt(chain: list[NormalizedPost], target_post: NormalizedPost) -> str:
        lines = [
            "以下は親投稿列のみを並べた会話履歴です。古い順です。",
            "",
        ]
        for index, post in enumerate(chain, start=1):
            speaker = "assistant" if post.author_handle == os.getenv("TRUTHSOCIAL_USERNAME") else "user"
            media_note = ""
            if post.media:
                media_note = f" [media: {', '.join(item.media_type for item in post.media)}]"
            lines.extend(
                [
                    f"[{index}] {speaker} @{post.author_handle}{media_note}",
                    post.llm_text or "(empty)",
                    "",
                ]
            )
        lines.extend(
            [
                "最後の投稿に対する返信を1件だけ生成してください。",
                f"対象投稿ID: {target_post.post_id}",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _build_model_settings() -> OpenRouterModelSettings | None:
        reasoning_effort = os.getenv("OPENROUTER_REASONING_EFFORT")
        if not reasoning_effort:
            return OpenRouterModelSettings(openrouter_usage={"include": True})
        return OpenRouterModelSettings(
            openrouter_reasoning={"effort": reasoning_effort},
            openrouter_usage={"include": True},
        )
