"""通义千问 Provider：OpenAI 兼容协议（DashScope）。复用 DeepSeek 实现，仅换端点/Key/模型。"""
from openai import AsyncOpenAI

from app.config import get_settings
from app.llm.deepseek import DeepSeekProvider


class QwenProvider(DeepSeekProvider):
    name = "qwen"

    def __init__(self):
        super().__init__()  # 复用限速/重试/JSON 降级逻辑
        settings = get_settings()
        self._client = AsyncOpenAI(
            api_key=settings.qwen_api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            timeout=settings.llm_timeout,
        )
        self._model = settings.qwen_model
