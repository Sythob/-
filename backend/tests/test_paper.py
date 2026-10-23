"""模拟盘测试：账户、引擎撮合（mock 行情）、T+1、策略单次日撮合、AI 风控。"""
from datetime import datetime, timedelta

import pytest

from app.data import database
from app.data.models import PaperOrder
from app.data.sync import _upsert_daily_bars
from app.engine.objects import Direction
from app.paper import account as account_service
from app.paper import risk as paper_risk
from app.paper.engine import PaperEngine, get_paper_engine


@pytest.fixture
def engine(tmp_db, monkeypatch):
    # 行情数据：给持仓估值用
    rows = [
        {"symbol": "600519", "trade_date": "2024-01-05", "open": 100, "high": 102,
         "low": 99, "close": 101, "volume": 1e6, "amount": 1e8,
         "pct_change": 1.0, "turnover_rate": 1.0, "adj_factor": 1.0},
    ]
    _upsert_daily_bars(rows)
    monkeypatch.setattr("app.paper.engine.fetch_quotes", _fake_quotes)
    # 清掉单例，每个测试独立引擎
    monkeypatch.setattr("app.paper.engine._engine", None)
    return get_paper_engine()


def _fake_quotes(symbols):
    return {
        sym: {"last_price": 101.0, "pct_change": 1.0, "open": 100.0,
              "high": 102.0, "low": 99.0, "volume": 1e6, "amount": 1e8}
        for sym in symbols
    }


def test_account_lifecycle(tmp_db):
    acc = account_service.ensure_account()
    assert acc.cash == 1_000_000
    summary = account_service.account_summary()
    assert summary["total"] == 1_000_000
    assert summary["positions"] == []


def test_buy_fill_updates_state(engine):
    row = engine.place_order("600519", Direction.BUY, 100)
    engine.process_quotes()
    with database.get_session() as session:
        order = session.get(PaperOrder, row.id)
        assert order.status == "filled"
        assert order.filled_price == pytest.approx(101.01)  # 101 + 滑点 0.01
    summary = account_service.account_summary()
    assert summary["positions"][0]["volume"] == 100
    assert summary["cash"] < 1_000_000  # 扣了资金+费用
    assert summary["market_value"] == pytest.approx(10100, rel=0.01)


def test_same_day_sell_rejected_t1(engine):
    """同一轮撮合内：买入成交后，当日卖出的委托因 T+1 被拒。"""
    buy_row = engine.place_order("600519", Direction.BUY, 100)
    sell_row = engine.place_order("600519", Direction.SELL, 100)
    engine.process_quotes()  # 一次轮询：先买后卖，卖被 T+1 拒单
    with database.get_session() as session:
        assert session.get(PaperOrder, buy_row.id).status == "filled"
        order = session.get(PaperOrder, sell_row.id)
        assert order.status == "rejected"
        assert "T+1" in order.reject_reason


def test_sell_succeeds_after_settle(engine):
    engine.place_order("600519", Direction.BUY, 100)
    engine.process_quotes()
    engine.settle()  # 收盘结算解锁 T+1
    row = engine.place_order("600519", Direction.SELL, 100)
    engine.process_quotes()
    with database.get_session() as session:
        order = session.get(PaperOrder, row.id)
        assert order.status == "filled"
    assert account_service.account_summary()["positions"] == []


def test_strategy_order_waits_next_session(engine):
    """next_session 单当日不撮合；把 created_at 改到昨天后次日可撮合。"""
    row = engine.place_order("600519", Direction.BUY, 100, next_session=True)
    engine.process_quotes()
    with database.get_session() as session:
        assert session.get(PaperOrder, row.id).status == "submitted"  # 当日不撮合

    with database.get_session() as session:
        order = session.get(PaperOrder, row.id)
        order.created_at = datetime.now() - timedelta(days=1)
        session.commit()
    engine.process_quotes()
    with database.get_session() as session:
        assert session.get(PaperOrder, row.id).status == "filled"


def test_limit_order_waits_for_price(engine):
    row = engine.place_order("600519", Direction.BUY, 100, price_type="limit", price=50.0)
    engine.process_quotes()
    with database.get_session() as session:
        assert session.get(PaperOrder, row.id).status == "submitted"  # 未穿越


def test_equity_recorded_after_settle(engine):
    engine.place_order("600519", Direction.BUY, 100)
    engine.process_quotes()
    engine.settle()
    equity = account_service.account_summary()
    assert equity["total"] == pytest.approx(equity["cash"] + 10100, rel=0.01)


def test_reset_account(engine):
    engine.place_order("600519", Direction.BUY, 100)
    engine.process_quotes()
    summary = account_service.reset_account()
    assert summary["cash"] == 1_000_000
    assert summary["positions"] == []


def test_risk_check_block_mode(engine, monkeypatch):
    acc = account_service.ensure_account()
    with database.get_session() as session:
        row = session.get(type(acc), acc.id)
        row.risk_mode = "block"
        session.commit()

    async def fake_check(order, stats, news, account, provider=None):
        return {"approved": False, "risk_level": "high", "reasons": ["测试拦截"]}

    monkeypatch.setattr(paper_risk, "check_order_risk", fake_check)
    with pytest.raises(paper_risk.RiskBlocked):
        paper_risk.run_risk_check("600519", "buy", 0, 100, "market")


def test_risk_check_warn_mode_passes(engine):
    acc = account_service.ensure_account()
    with database.get_session() as session:
        row = session.get(type(acc), acc.id)
        row.risk_mode = "warn"
        session.commit()
    # 无 key → MockProvider → 放行
    result = paper_risk.run_risk_check("600519", "buy", 0, 100, "market")
    assert result["approved"] is True


def test_resolve_symbol_by_name(tmp_db):
    """下单代码解析：6 位代码直返、名称匹配、歧义/未命中报错。"""
    import pytest
    from fastapi import HTTPException

    from app.api.paper import _resolve_symbol
    from app.data.models import Instrument

    with tmp_db() as session:
        session.add(Instrument(symbol="600519", name="贵州茅台", type="stock"))
        session.add(Instrument(symbol="601318", name="中国平安", type="stock"))
        session.add(Instrument(symbol="000001", name="平安银行", type="stock"))
        session.commit()

    assert _resolve_symbol("600519") == "600519"          # 代码直返
    assert _resolve_symbol("茅台") == "600519"            # 名称唯一匹配
    with pytest.raises(HTTPException):                    # 歧义：平安 → 2 只
        _resolve_symbol("平安")
    with pytest.raises(HTTPException):                    # 未命中
        _resolve_symbol("不存在的股票")
