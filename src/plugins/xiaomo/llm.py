"""小源 QQ 机器人 - LLM 客户端 (DeepSeek / OpenAI 兼容)"""
from __future__ import annotations

import os
from typing import Optional

from openai import AsyncOpenAI

from .config import get_config
from .persona import build_system_prompt


class LLMClient:
    """DeepSeek API 客户端 (OpenAI 兼容协议)"""

    def __init__(self):
        config = get_config()
        llm_cfg = config.get("llm", {})

        api_key = llm_cfg.get("api_key") or os.getenv("DEEPSEEK_API_KEY", "")
        base_url = llm_cfg.get("api_base", "https://api.deepseek.com/v1")
        self.model = llm_cfg.get("model", "deepseek-chat")
        self.max_tokens = llm_cfg.get("max_tokens", 4096)
        self.temperature = llm_cfg.get("temperature", 0.8)

        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def chat(
        self,
        context: str,
        user_profile: dict | None = None,
        scene: str = "group",
        user_message: str | None = None,
        mode: str = "normal",
    ) -> str:
        """发送聊天请求到 DeepSeek API"""
        system = build_system_prompt(scene=scene, user_profile=user_profile, mode=mode)

        parts = []
        if context:
            parts.append(f"以下是之前的对话上下文：\n{context}")
        if user_message:
            parts.append(f"\n成员最新消息：{user_message}")
        parts.append("\n请以小源（开源协会顾问猫娘）的身份回复。记住：回复要简短精炼，适度使用颜文字。")

        user_content = "\n".join(parts)

        response = await self._client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
        )

        return response.choices[0].message.content or "喵...小源卡住了 (´;ω;`)"

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
