"""持仓风险分析测试：落库、覆盖、读取（LLM 全 mock，离线）。"""
import asyncio

from app.data.sync import _upsert_daily_bars
from app.llm.mock_provider import MockProvider
from app.llm.tasks.position_risk import analyze_position_risk, load_position_risks


def _seed(tmp_db, symbol="600519"):
    _upsert_daily_bars([
        {"symbol": symbol, "trade_date": f"2024-01-{i + 5:02d}", "open": 10, "high": 10.5,
         "low": 9.8, "close": 10 + i * 0.1, "volume": 1e6, "amount": 6e8,
         "pct_change": 1.0, "turnover_rate": 2.0, "adj_factor": 1.0}
        for i in range(6)
    ])
    from app.paper import account as account_service

    account_service.upsert_position(1, symbol, 100, 100, 10.0)


def test_analyze_and_load(tmp_db):
    _seed(tmp_db)
    r = asyncio.run(analyze_position_risk("600519", provider=MockProvider()))
    assert r["risk_level"] == "中"
    assert r["score"] == 55.0
    assert r["factors"]
    assert r["suggestion"]
    rows = load_position_risks()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "600519"
    # 同日重复分析覆盖（不产生重复行）
    asyncio.run(analyze_position_risk("600519", provider=MockProvider()))
    assert len(load_position_risks()) == 1


def test_analyze_rejects_non_position(tmp_db):
    """未持仓的股票分析报错。"""
    import pytest

    _seed(tmp_db, symbol="600519")  # 只有 600519 持仓；000001 无持仓
    with pytest.raises(ValueError, match="未持有"):
        asyncio.run(analyze_position_risk("000001", provider=MockProvider()))
