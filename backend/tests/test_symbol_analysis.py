"""个股解读测试：幂等去重、持仓范围筛选（LLM 全 mock，离线）。"""
import asyncio

from app.data.sync import _upsert_daily_bars
from app.llm.mock_provider import MockProvider
from app.llm.tasks.symbol_analysis import analyze_symbol


def _seed_bars(symbol: str) -> None:
    """种入 6 根日线，避免"数据不足→真实网络同步"。"""
    rows = []
    for i in range(6):
        rows.append({
            "symbol": symbol, "trade_date": f"2024-01-{i + 5:02d}",
            "open": 10, "high": 10.5, "low": 9.8, "close": 10 + i * 0.1,
            "volume": 1e6, "amount": 6e8, "pct_change": 1.0,
            "turnover_rate": 2.0, "adj_factor": 1.0,
        })
    _upsert_daily_bars(rows)


def test_analyze_symbol_idempotent(tmp_db):
    """同日重复解读自动跳过（只落库一份报告）。"""
    from app.data import database
    from app.data.models import AiReport

    _seed_bars("600519")
    r1 = asyncio.run(analyze_symbol("600519", provider=MockProvider()))
    assert "skipped" not in r1  # 第一次正常生成
    r2 = asyncio.run(analyze_symbol("600519", provider=MockProvider()))
    assert r2.get("skipped") is True  # 第二次跳过
    with database.get_session() as session:
        assert session.query(AiReport).filter(AiReport.report_type == "stock").count() == 1


def test_job_symbol_analysis_runs_for_positions(tmp_db, monkeypatch):
    """定时任务只解读当前持仓（mock 掉 LLM 与日期）。"""
    from app.paper import account as account_service
    from app.scheduler.jobs import job_symbol_analysis

    _seed_bars("600519")
    _seed_bars("000001")
    account_service.upsert_position(1, "600519", 100, 100, 1300.0)
    account_service.upsert_position(1, "000001", 200, 200, 10.0)
    calls: list[str] = []

    class Recorder(MockProvider):
        async def chat(self, messages, temperature=0.3):
            for m in messages:
                if "股票代码" in str(m.get("content", "")):
                    calls.append(str(m["content"])[:10])
            return "解读内容"
    from app.llm.tasks import symbol_analysis as sa

    monkeypatch.setattr(sa, "get_provider", lambda: Recorder())
    monkeypatch.setattr("app.data.calendar.is_trade_day", lambda: True)

    job_symbol_analysis()
    assert len(calls) == 2  # 两只持仓各解读一次
