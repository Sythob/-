"""AI 小助手测试（离线）。"""
import asyncio

from app.llm.mock_provider import MockProvider
from app.llm.tasks.assistant import assistant_context, chat_with_assistant


def test_assistant_context_has_market_and_holdings(tmp_db, monkeypatch):
    """数据快照包含市场状态与持仓信息。"""
    monkeypatch.setattr(
        "app.data.sentiment.market_regime",
        lambda: {"regime": "weak", "date": "2024-01-05", "advance": 1000, "decline": 4000,
                 "limit_up": 20, "limit_down": 80, "trend_strict": True},
    )
    context = assistant_context()
    assert "市场状态" in context
    assert "weak" in context
    assert "账户" in context or "现金" in context  # 账户部分（无持仓时也有现金行）
    assert "自选池" in context


def test_chat_with_assistant_mock(tmp_db, monkeypatch):
    monkeypatch.setattr(
        "app.data.sentiment.market_regime",
        lambda: {"regime": "neutral", "date": "2024-01-05", "advance": 2500, "decline": 2500,
                 "limit_up": 50, "limit_down": 50, "trend_strict": False},
    )
    result = asyncio.run(chat_with_assistant(
        "今天市场怎么样", [{"role": "user", "content": "你好"}],
        provider=MockProvider(),
    ))
    assert result["model"] == "mock"
    assert isinstance(result["reply"], str) and result["reply"]


def test_chat_api_endpoint(tmp_db, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app

    # 测试必须离线：清空 Key 强制 MockProvider
    monkeypatch.setattr(get_settings(), "deepseek_api_key", "")
    monkeypatch.setattr(
        "app.data.sentiment.market_regime",
        lambda: {"regime": "neutral", "date": "2024-01-05", "advance": 2500, "decline": 2500,
                 "limit_up": 50, "limit_down": 50, "trend_strict": False},
    )
    with TestClient(app) as client:
        resp = client.post("/api/ai/chat", json={"message": "你好", "history": []})
        assert resp.status_code == 200
        data = resp.json()
        assert "reply" in data and data["model"] == "mock"
        # 空消息 → 400
        resp2 = client.post("/api/ai/chat", json={"message": "  ", "history": []})
        assert resp2.status_code == 400
