"""小源 QQ 机器人 - LLM 客户端 (MiMo-V2.5 / OpenAI 兼容)"""
from __future__ import annotations

import os

from openai import AsyncOpenAI

from .config import get_config
from .persona import build_system_prompt


class LLMClient:
    """MiMo-V2.5 API 客户端 (OpenAI 兼容协议，支持文本 + 视觉)"""

    def __init__(self):
        config = get_config()
        llm_cfg = config.get("llm", {})

        api_key = llm_cfg.get("api_key") or os.getenv("LLM_API_KEY", "")
        base_url = llm_cfg.get("api_base", "https://api.xiaomimimo.com/v1")
        self.model = llm_cfg.get("model", "mimo-v2.5")
        self.max_tokens = llm_cfg.get("max_tokens", 4096)
        self.temperature = llm_cfg.get("temperature", 0.8)
        self.timeout_seconds = llm_cfg.get("timeout_seconds", 30)
        self.max_retries = llm_cfg.get("max_retries", 0)

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=self.timeout_seconds,
            max_retries=self.max_retries,
        )

    async def chat(
        self,
        context: str,
        user_profile: dict | None = None,
        scene: str = "group",
        user_message: str | None = None,
        mode: str = "normal",
        structured_history: list[dict] | None = None,
        group_id: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        system_prompt: str | None = None,
    ) -> str:
        """发送聊天请求到 MiMo API，支持多轮对话格式"""
        system = system_prompt or build_system_prompt(
            scene=scene,
            user_profile=user_profile,
            mode=mode,
            group_id=group_id,
        )
        request_max_tokens = max_tokens or self.max_tokens
        request_temperature = (
            self.temperature if temperature is None else temperature
        )

        if structured_history:
            # ── 多轮对话模式：每个消息保留独立角色 ──
            messages: list[dict] = [{"role": "system", "content": system}]

            # 上下文块（摘要、发言历史、语义搜索）作为 system 消息注入
            if context:
                messages.append({"role": "system", "content": f"[上下文参考]\n{context}"})

            # 历史对话 — 保留 user/assistant 角色交替
            messages.extend(structured_history)

            # 当前用户消息（纯消息内容，风格指令已移至 system prompt）
            if user_message:
                messages.append({"role": "user", "content": user_message})

            response = await self._client.chat.completions.create(
                model=self.model,
                max_tokens=request_max_tokens,
                temperature=request_temperature,
                messages=messages,
            )
        else:
            # ── 兼容旧格式：所有内容压扁为一个 user 消息 ──
            parts = []
            if context:
                parts.append(f"以下是之前的对话上下文：\n{context}")
            if user_message:
                parts.append(f"\n成员最新消息：{user_message}")
            user_content = "\n".join(parts)

            response = await self._client.chat.completions.create(
                model=self.model,
                max_tokens=request_max_tokens,
                temperature=request_temperature,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
            )

        content = (response.choices[0].message.content or "").strip()
        return content if content else "刚刚没生成出来，你再说一次？"

    async def decision(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 160,
    ) -> str:
        """Run a compact classifier without persona, history, or long output."""

        response = await self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=0.1,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        return (response.choices[0].message.content or "").strip()

    async def generate_summary(self, messages_text: str) -> str:
        """生成对话摘要（用于记忆压缩）"""
        system = "你是一个对话摘要助手。请将以下对话压缩为简洁的摘要，保留关键信息和人物关系。"

        response = await self._client.chat.completions.create(
            model=self.model,
            max_tokens=1024,
            temperature=0.3,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"请摘要以下对话：\n{messages_text}"},
            ],
        )
        return response.choices[0].message.content or ""


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
