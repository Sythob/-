"""四期功能测试：Claude/Qwen provider、个股解读、策略模板。"""
import asyncio

import pytest

from app.data.models import AiReport
from app.engine.backtester import BacktestConfig, BacktestEngine
from app.llm.claude import ClaudeProvider
from app.llm.mock_provider import MockProvider
from app.llm.tasks.symbol_analysis import analyze_symbol
from app.strategies.bollinger_breakout import BollingerBreakoutStrategy
from app.strategies.grid_trading import GridTradingStrategy
from app.strategies.turtle_trend import TurtleTrendStrategy
from tests.helpers import make_daily


def test_claude_split_and_json():
    messages = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "回答"},
    ]
    system, msgs = ClaudeProvider._split(messages)
    assert system == "你是助手"
    assert len(msgs) == 2  # system 提到顶层
    assert msgs[0]["role"] == "user"

    assert ClaudeProvider._extract_json('前缀 {"a": 1} 后缀') == {"a": 1}
    with pytest.raises((ValueError, TypeError)):
        ClaudeProvider._extract_json("没有JSON")


def test_qwen_provider_imports(monkeypatch):
    """Qwen provider 复用 OpenAI 兼容实现（离线构造，不发起请求）。"""
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "qwen_api_key", "test-key")
    monkeypatch.setattr(s, "deepseek_api_key", "test-key")
    from app.llm.qwen import QwenProvider

    provider = QwenProvider()
    assert provider.name == "qwen"
    assert "dashscope" in str(provider._client.base_url)


def test_symbol_analysis_mock(tmp_db):
    from app.data.sync import _upsert_daily_bars

    rows = [
        {"symbol": "600519", "trade_date": f"2024-01-{i + 2:02d}", "open": 100 + i,
         "high": 103 + i, "low": 98 + i, "close": 101 + i, "volume": 1e6, "amount": 1e8,
         "pct_change": 1.0, "turnover_rate": 1.0, "adj_factor": 1.0}
        for i in range(25)
    ]
    _upsert_daily_bars(rows)
    result = asyncio.run(analyze_symbol("600519", provider=MockProvider()))
    assert result["model"] == "mock"
    with tmp_db() as session:
        row = session.query(AiReport).filter(AiReport.report_type == "stock").first()
        assert row is not None
        assert "600519" in row.title


@pytest.mark.parametrize("strategy_cls,params", [
    (GridTradingStrategy, {"grid_pct": 0.03, "volume_per_grid": 100, "max_layers": 3}),
    (TurtleTrendStrategy, {"entry_days": 20, "exit_days": 10, "atr_multiple": 2.0}),
    (BollingerBreakoutStrategy, {"period": 20, "k": 2.0}),
])
def test_strategy_templates_run(strategy_cls, params):
    """三个新策略模板端到端可回测。"""
    engine = BacktestEngine(
        {"600519": make_daily()}, strategy_cls, params=params,
        config=BacktestConfig(initial_capital=100_000),
    )
    result = engine.run()
    assert len(result.dates) == 4
    assert "total_return" in result.metrics
