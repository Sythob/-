"""本地 LLM 提供商测试：mock OpenAI 客户端，验证 chat/chat_json（含正则兜底）。"""
import asyncio

from app.llm.local import LocalProvider


class _FakeCompletions:
    def __init__(self, text: str):
        self._text = text

    async def create(self, **kwargs):
        return _FakeResp(self._text)


class _FakeResp:
    def __init__(self, text: str):
        self.choices = [_FakeChoice(text)]


class _FakeChoice:
    def __init__(self, text: str):
        self.message = _FakeMsg(text)


class _FakeMsg:
    def __init__(self, text: str):
        self.content = text


class _FakeClient:
    def __init__(self, text: str):
        self.chat = _FakeChat(text)


class _FakeChat:
    def __init__(self, text: str):
        self.completions = _FakeCompletions(text)


def _provider(text: str) -> LocalProvider:
    p = LocalProvider()
    p._client = _FakeClient(text)  # type: ignore[assignment]
    return p


def test_chat_returns_text(tmp_db):
    p = _provider("你好，这是本地模型的回复")
    result = asyncio.run(p.chat([{"role": "user", "content": "hi"}]))
    assert result == "你好，这是本地模型的回复"


def test_chat_json_parses_pure_json(tmp_db):
    p = _provider('{"view": "neutral", "confidence": 0.6}')
    result = asyncio.run(p.chat_json([{"role": "user", "content": "分析"}],
                                     schema_hint="multiagent"))
    assert result == {"view": "neutral", "confidence": 0.6}


def test_chat_json_regex_fallback(tmp_db):
    """本地小模型常输出带前后缀的 JSON → 正则提取兜底。"""
    p = _provider('好的，分析结果如下：\n{"lessons": ["教训A"]}\n希望有帮助')
    result = asyncio.run(p.chat_json([{"role": "user", "content": "提炼教训"}]))
    assert result == {"lessons": ["教训A"]}


def test_chat_json_garbage_returns_empty(tmp_db):
    p = _provider("完全不是 JSON 的内容")
    result = asyncio.run(p.chat_json([{"role": "user", "content": "x"}]))
    assert result == {}
