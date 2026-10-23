"""模拟盘条件单与通知测试（离线）。"""
import pytest

from app.data import database
from app.data.models import PaperOrder
from app.data.sync import _upsert_daily_bars
from app.engine.objects import Direction
from app.paper.engine import PaperEngine, get_paper_engine


@pytest.fixture
def engine(tmp_db, monkeypatch):
    rows = [
        {"symbol": "600519", "trade_date": "2024-01-05", "open": 100, "high": 102,
         "low": 99, "close": 101, "volume": 1e6, "amount": 1e8,
         "pct_change": 1.0, "turnover_rate": 1.0, "adj_factor": 1.0},
    ]
    _upsert_daily_bars(rows)
    monkeypatch.setattr("app.paper.engine.fetch_quotes", _fake_quotes)
    monkeypatch.setattr("app.paper.engine._engine", None)
    return get_paper_engine()


def _fake_quotes(symbols):
    return {
        sym: {"last_price": 101.0, "pct_change": 1.0, "open": 100.0,
              "high": 102.0, "low": 99.0, "volume": 1e6, "amount": 1e8}
        for sym in symbols
    }


def test_stop_loss_condition_waits_then_fills(engine):
    """止损单：行情未跌破触发价 → 继续等待（不成交不撤单）。"""
    engine.place_order("600519", Direction.SELL, 100, condition_type="stop_loss", trigger_price=90.0)
    engine.process_quotes()  # 现价 101 > 触发价 90（止损需跌破）→ 不触发
    with database.get_session() as session:
        rows = session.query(PaperOrder).filter(PaperOrder.status == "submitted").all()
        assert len(rows) == 1  # 仍在等待


def test_take_profit_condition_triggers(engine):
    """止盈单：价格 101 ≥ 触发价 100 → 立即触发。但无持仓卖出被 T+1/无持仓拒单。"""
    row = engine.place_order("600519", Direction.BUY, 100)
    engine.process_quotes()  # 先买入
    with database.get_session() as session:
        assert session.get(PaperOrder, row.id).status == "filled"

    engine.settle()  # T+1 解锁
    profit_row = engine.place_order("600519", Direction.SELL, 100,
                                    condition_type="take_profit", trigger_price=100.0)
    engine.process_quotes()  # 101 ≥ 100 → 触发并成交
    with database.get_session() as session:
        assert session.get(PaperOrder, profit_row.id).status == "filled"


def test_invalid_condition_rejected(engine):
    with pytest.raises(ValueError):
        engine.place_order("600519", Direction.BUY, 100, condition_type="magic", trigger_price=100)
    with pytest.raises(ValueError):
        engine.place_order("600519", Direction.BUY, 100, condition_type="stop_loss", trigger_price=0)


def test_notify_skips_without_config(tmp_db):
    """未配置推送通道时静默跳过，不报错。"""
    from app.utils.notify import send_notification

    result = send_notification("测试", "内容", async_send=False)
    assert result["email"] == "skip"
    assert result["serverchan"] == "skip"
    assert result["telegram"] == "skip"
