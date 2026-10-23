"""LLM Provider 抽象与工厂。

- chat：自由文本（复盘日报等 Markdown 输出）
- chat_json：结构化 JSON（情绪/风控等），schema_hint 供 Mock 按场景返回正确形状

工厂规则：LLM_MOCK=1 或未配置 API Key → MockProvider（离线全流程可跑）。
"""
from abc import ABC, abstractmethod


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def chat(self, messages: list[dict], temperature: float = 0.3) -> str:
        ...

    @abstractmethod
    async def chat_json(self, messages: list[dict], temperature: float = 0.3,
                        schema_hint: str = "") -> dict:
        ...


def get_provider() -> LLMProvider:
    from app.config import get_settings

    settings = get_settings()
    from app.llm.mock_provider import MockProvider

    if settings.llm_provider == "claude":
        if not settings.anthropic_api_key:
            return MockProvider()  # 未配置 Key → 离线 Mock
        from app.llm.claude import ClaudeProvider

        return ClaudeProvider()
    if settings.llm_provider == "qwen":
        if not settings.qwen_api_key:
            return MockProvider()
        from app.llm.qwen import QwenProvider

        return QwenProvider()
    if settings.llm_provider == "local":
        from app.llm.local import LocalProvider

        return LocalProvider()
    # deepseek（默认主力）
    if settings.llm_mock or not settings.deepseek_api_key:
        return MockProvider()
    from app.llm.deepseek import DeepSeekProvider

    return DeepSeekProvider()
