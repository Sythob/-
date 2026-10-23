"""Claude Provider：官方 anthropic SDK（不用 OpenAI 兼容垫片）。

注意：当前 Claude 模型不接受 temperature/top_p 采样参数（会 400），
本实现忽略调用方的 temperature。SDK 自带 429/5xx 指数退避重试。
"""
import asyncio
import json
import re
import time

from anthropic import AsyncAnthropic

from app.config import get_settings
from app.llm.base import LLMProvider
from app.utils.logger import get_logger

logger = get_logger("llm.claude")


class ClaudeProvider(LLMProvider):
    name = "claude"

    def __init__(self):
        settings = get_settings()
        self._client = AsyncAnthropic(
            api_key=settings.anthropic_api_key, timeout=settings.llm_timeout,
        )
        self._model = settings.claude_model
        self._min_interval = settings.llm_min_interval
        self._last_call = 0.0
        self._throttle_lock = asyncio.Lock()

    async def _throttle(self) -> None:
        async with self._throttle_lock:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_call = time.monotonic()

    async def chat(self, messages: list[dict], temperature: float = 0.3) -> str:
        await self._throttle()
        system, msgs = self._split(messages)
        resp = await self._client.messages.create(
            model=self._model,
            max_tokens=16000,
            system=system,
            messages=msgs,
        )
        return "".join(b.text for b in resp.content if b.type == "text")

    async def chat_json(self, messages: list[dict], temperature: float = 0.3,
                        schema_hint: str = "") -> dict:
        text = await self.chat(messages, temperature)
        return self._extract_json(text)

    @staticmethod
    def _split(messages: list[dict]) -> tuple[str | None, list[dict]]:
        """Anthropic 消息结构：system 提到顶层，其余保留顺序。"""
        system = None
        msgs = []
        for m in messages:
            if m["role"] == "system" and system is None:
                system = m["content"]
            else:
                msgs.append({"role": m["role"], "content": m["content"]})
        return system, msgs

    @staticmethod
    def _extract_json(text: str) -> dict:
        """JSON 解析，失败时提取文本中的 {...} 块重试。"""
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                return json.loads(match.group())
            raise ValueError(f"无法从模型输出中解析 JSON: {text[:200]!r}")
