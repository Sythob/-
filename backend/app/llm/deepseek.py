"""DeepSeek 实现：AsyncOpenAI（OpenAI 兼容协议）。

- Key 只从 config（.env）读取，零硬编码
- 请求间最小间隔限速（防触发平台限流）
- 429/5xx/网络错误指数退避重试
- JSON 输出模式失败时降级正则提取
"""
import asyncio
import json
import re
import time

from openai import AsyncOpenAI

from app.config import get_settings
from app.llm.base import LLMProvider
from app.llm.client import retrying
from app.utils.logger import get_logger

logger = get_logger("llm.deepseek")


class DeepSeekProvider(LLMProvider):
    name = "deepseek"

    def __init__(self):
        settings = get_settings()
        self._client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout,
        )
        self._model = settings.llm_model
        self._min_interval = settings.llm_min_interval
        self._max_retries = settings.llm_max_retries
        self._last_call = 0.0
        self._throttle_lock = asyncio.Lock()

    async def _throttle(self) -> None:
        async with self._throttle_lock:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_call = time.monotonic()

    async def chat(self, messages: list[dict], temperature: float = 0.3) -> str:
        resp = await self._call(messages, temperature, json_mode=False)
        return resp.choices[0].message.content or ""

    async def chat_json(self, messages: list[dict], temperature: float = 0.3,
                        schema_hint: str = "") -> dict:
        resp = await self._call(messages, temperature, json_mode=True)
        content = resp.choices[0].message.content or ""
        result = self._extract_json(content)
        # 兼容模型把输出包成 {"type": "json_object", "content": "{...}"} 的壳
        if isinstance(result, dict) and isinstance(result.get("content"), str):
            inner = self._extract_json(result["content"])
            if inner:
                return inner
        return result

    async def _call(self, messages, temperature, json_mode):
        await self._throttle()
        if json_mode:
            # DeepSeek 官方要求：json_object 模式的提示词必须出现 "json" 字样，
            # 否则报 400。统一在最后一条消息尾部补上。
            messages = [dict(m) for m in messages]
            if messages and "json" not in str(messages[-1].get("content", "")).lower():
                messages[-1]["content"] = f"{messages[-1]['content']}\n请以 json 格式输出。"
        kwargs = dict(model=self._model, messages=messages, temperature=temperature)
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        async for attempt in retrying(self._max_retries):
            with attempt:
                return await self._client.chat.completions.create(**kwargs)
        raise RuntimeError("unreachable")  # reraise=True 保证不会到这

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
