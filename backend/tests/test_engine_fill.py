"""撮合铁律：信号次日开盘成交（含滑点）、涨停拒买、市价单不隔夜。"""
import pytest

from app.engine.backtester import BacktestConfig, BacktestEngine
from app.strategies.base import Strategy
from tests.helpers import make_daily


class BuyDayOne(Strategy):
    name = "test_fill"

    def on_bar(self, bar):
        if bar.trade_date == "2020-01-02":
            self.buy(bar.symbol, volume=100)


def test_fill_next_open_with_slippage():
    rows = [
        ("2020-01-02", 10.0, 10.5, 9.8, 10.2, 2.0),
        ("2020-01-03", 11.0, 11.5, 10.8, 11.2, 1.8),
        ("2020-01-06", 11.2, 11.4, 11.0, 11.1, -0.9),
    ]
    engine = BacktestEngine(
        {"600519": make_daily(rows=rows)}, BuyDayOne,
        config=BacktestConfig(initial_capital=1_000_000, slippage=0.05),
    )
    result = engine.run()

    # 01-02 提交 → 01-02 收盘时无成交
    assert result.dates[0] == "2020-01-02"
    # 01-03 开盘 11.0 + 滑点 0.05 成交
    buys = [t for t in result.trades if t.direction.value == "buy"]
    assert len(buys) == 1
    assert buys[0].trade_time == "2020-01-03"
    assert buys[0].price == pytest.approx(11.05)


def test_limit_up_no_fill_then_cancel():
    rows = [
        ("2020-01-02", 10.0, 10.5, 9.8, 10.2, 2.0),
        ("2020-01-03", 11.22, 11.22, 11.22, 11.22, 10.0),  # 涨停（一字板）
        ("2020-01-06", 11.2, 11.4, 11.0, 11.1, -1.07),
    ]
    engine = BacktestEngine(
        {"600519": make_daily(rows=rows)}, BuyDayOne,
        config=BacktestConfig(initial_capital=1_000_000, slippage=0.0),
    )
    result = engine.run()

    # 涨停日市价买单未成交 → 当日撤销，无买入成交
    assert not [t for t in result.trades if t.direction.value == "buy"]
    buy_orders = [o for o in result.orders if o.direction.value == "buy"]
    assert buy_orders[0].status.value == "cancelled"


def test_suspended_symbol_cancels_market_order():
    """停牌日无 bar → 市价单撤销，不隔夜。"""
    rows_a = [
        ("2020-01-02", 10.0, 10.5, 9.8, 10.2, 2.0),
        ("2020-01-03", 10.2, 10.8, 10.0, 10.5, 2.94),
    ]
    rows_b = [
        ("2020-01-02", 20.0, 20.5, 19.8, 20.2, 1.0),
        # 01-03 停牌：无 bar
        ("2020-01-06", 20.5, 21.0, 20.3, 20.8, 1.5),
    ]
    engine = BacktestEngine(
        {"600519": make_daily(rows=rows_a), "000001": make_daily(symbol="000001", rows=rows_b)},
        BuyDayOne, config=BacktestConfig(initial_capital=1_000_000, slippage=0.0),
    )
    result = engine.run()

    # 600519 正常成交；000001 在 01-03 停牌 → 市价单撤销
    buys_600519 = [t for t in result.trades if t.symbol == "600519"]
    assert len(buys_600519) == 1
    orders_000001 = [o for o in result.orders if o.symbol == "000001" and o.direction.value == "buy"]
    assert orders_000001[0].status.value == "cancelled"
    assert not [t for t in result.trades if t.symbol == "000001"]
