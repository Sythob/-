"""本地 Ollama 提供商（OpenAI 兼容接口，GPU 加速，省 API 费用）。

配置：LLM_PROVIDER=local、LOCAL_BASE_URL=http://127.0.0.1:11434/v1、LOCAL_MODEL=qwen2.5:7b
JSON 模式：不传 response_format（Ollama 兼容性参差），靠提示词 + 正则提取兜底。
"""
import json
import re

from openai import AsyncOpenAI

from app.llm.base import LLMProvider
from app.utils.logger import get_logger

logger = get_logger("llm.local")


class LocalProvider(LLMProvider):
    name = "local"

    def __init__(self, base_url: str = "", model: str = ""):
        from app.config import get_settings

        settings = get_settings()
        self._model = model or settings.local_model
        base = base_url or settings.local_base_url
        self._client = AsyncOpenAI(base_url=base, api_key="ollama")

    async def chat(self, messages: list[dict], temperature: float = 0.3) -> str:
        resp = await self._client.chat.completions.create(
            model=self._model, messages=messages, temperature=temperature,
        )
        return resp.choices[0].message.content or ""

    async def chat_json(self, messages: list[dict], temperature: float = 0.3,
                        schema_hint: str = "") -> dict:
        msgs = [dict(m) for m in messages]
        if msgs and "json" not in str(msgs[-1].get("content", "")).lower():
            msgs[-1]["content"] = f"{msgs[-1]['content']}\n请以 json 格式输出。"
        content = await self.chat(msgs, temperature)
        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError):
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
            logger.warning("本地模型 JSON 解析失败，返回空 dict（前 100 字：%s）", content[:100])
            return {}
