"""一夜持股策略与引擎收盘成交模式测试。"""
import pytest

from app.engine.backtester import BacktestConfig, BacktestEngine
from app.strategies.base import Strategy
from app.strategies.overnight_hold import OvernightHoldStrategy
from tests.helpers import make_daily


class BuyOnDayOneClose(Strategy):
    """收盘成交模式冒烟：尾盘买入（收盘成交）+ 次日开盘卖单。"""
    name = "test_close_fill"
    fill_style = "close"

    def on_bar(self, bar):
        if bar.trade_date == "2020-01-02":
            self.buy(bar.symbol, volume=100, fill_on_close=True)  # 尾盘单
            self.sell(bar.symbol, volume=100)                     # 普通单：次日开盘


def test_close_fill_same_day_buy_next_open_sell():
    """信号 t 收盘 → 尾盘买入按 t 收盘价成交（当日）；普通卖单次日开盘成交。"""
    rows = [
        ("2020-01-02", 10.0, 10.5, 9.8, 10.4, 4.0),
        ("2020-01-03", 10.4, 10.8, 10.2, 10.6, 1.9),
    ]
    engine = BacktestEngine(
        {"600519": make_daily(rows=rows)}, BuyOnDayOneClose,
        config=BacktestConfig(initial_capital=100_000, slippage=0.0),
    )
    result = engine.run()
    buys = [t for t in result.trades if t.direction.value == "buy"]
    sells = [t for t in result.trades if t.direction.value == "sell"]
    assert len(buys) == 1
    assert buys[0].trade_time == "2020-01-02"     # 当日收盘成交！
    assert buys[0].price == pytest.approx(10.4)   # 按收盘价
    assert len(sells) == 1
    assert sells[0].trade_time == "2020-01-03"    # 次日开盘卖出
    assert sells[0].price == pytest.approx(10.4)  # 次日开盘价


def test_close_fill_t1_still_enforced():
    """尾盘单之间仍受 T+1：同日尾盘买入后，尾盘卖单可卖不足被拒。"""
    class BuyThenSellBothClose(Strategy):
        name = "test_close_t1"
        fill_style = "close"

        def on_bar(self, bar):
            if bar.trade_date == "2020-01-02":
                self.buy(bar.symbol, volume=100, fill_on_close=True)
                self.sell(bar.symbol, volume=100, fill_on_close=True)  # 尾盘卖 → 当日不可卖

    rows = [
        ("2020-01-02", 10.0, 10.5, 9.8, 10.4, 4.0),
        ("2020-01-03", 10.4, 10.8, 10.2, 10.6, 1.9),
    ]
    engine = BacktestEngine(
        {"600519": make_daily(rows=rows)}, BuyThenSellBothClose,
        config=BacktestConfig(initial_capital=100_000, slippage=0.0),
    )
    result = engine.run()
    rejected = [o for o in result.orders if o.status.value == "rejected"]
    assert any("T+1" in o.reject_reason for o in rejected)  # 尾盘当日卖被拒
    assert not [t for t in result.trades if t.direction.value == "sell"]


def test_close_order_cancelled_when_limit_up():
    """涨停日尾盘买不进：尾盘单当日撤销，不隔夜。"""
    class BuyCloseOnly(Strategy):
        name = "test_close_limit"
        fill_style = "close"

        def on_bar(self, bar):
            if bar.trade_date == "2020-01-03":  # 涨停日挂尾盘单 → 买不进 → 撤销
                self.buy(bar.symbol, volume=100, fill_on_close=True)

    rows = [
        ("2020-01-02", 10.0, 10.5, 9.8, 10.4, 4.0),
        ("2020-01-03", 10.4, 10.4, 10.4, 10.4, 10.0),  # 一字涨停
        ("2020-01-06", 11.4, 11.5, 11.2, 11.4, 9.6),   # 接近涨停
    ]
    engine = BacktestEngine(
        {"600519": make_daily(rows=rows)}, BuyCloseOnly,
        config=BacktestConfig(initial_capital=100_000, slippage=0.0),
    )
    result = engine.run()
    # 尾盘单未成交即撤销，之后不隔夜补成交
    assert not [t for t in result.trades if t.direction.value == "buy"]
    orders = [o for o in result.orders if o.direction.value == "buy"]
    assert orders[0].status.value == "cancelled"


def test_default_style_still_next_open():
    """默认 next_open 铁律不受影响：信号次日开盘成交。"""
    class DefaultBuy(Strategy):
        name = "test_default_fill"

        def on_bar(self, bar):
            if bar.trade_date == "2020-01-02":
                self.buy(bar.symbol, volume=100)

    rows = [
        ("2020-01-02", 10.0, 10.5, 9.8, 10.4, 4.0),
        ("2020-01-03", 10.4, 10.8, 10.2, 10.6, 1.9),
    ]
    engine = BacktestEngine(
        {"600519": make_daily(rows=rows)}, DefaultBuy,
        config=BacktestConfig(initial_capital=100_000, slippage=0.0),
    )
    result = engine.run()
    buys = [t for t in result.trades if t.direction.value == "buy"]
    assert buys[0].trade_time == "2020-01-03"  # 仍是次日成交


def _strong_day_rows():
    """首日强势（涨幅 5%、收盘位置高、量比放大），其后普通日。"""
    return [
        ("2020-01-02", 10.0, 10.6, 9.9, 10.5, 5.0),   # 强势日：close 位置 0.86
        ("2020-01-03", 10.5, 10.9, 10.4, 10.7, 1.9),
        ("2020-01-06", 10.7, 10.8, 10.5, 10.6, -0.9),
    ]


def test_overnight_full_cycle():
    """强势日尾盘买入（收盘成交）→ 次日开盘卖出，隔夜一轮完整。"""
    rows = _strong_day_rows()
    engine = BacktestEngine(
        {"600519": make_daily(rows=rows)}, OvernightHoldStrategy,
        params={"min_pct": 3.0, "max_pct": 7.0, "volume_ratio_min": 1.0,
                "position_pct": 0.5},
        config=BacktestConfig(initial_capital=100_000, slippage=0.0),
    )
    result = engine.run()
    buys = [t for t in result.trades if t.direction.value == "buy"]
    sells = [t for t in result.trades if t.direction.value == "sell"]
    assert len(buys) == 1
    assert buys[0].trade_time == "2020-01-02"
    assert len(sells) == 1
    assert sells[0].trade_time == "2020-01-03"  # 隔夜兑现


def test_overnight_skips_weak_day():
    """弱日（涨幅不足）不触发。"""
    rows = [
        ("2020-01-02", 10.0, 10.1, 9.9, 10.05, 0.5),  # 涨幅 0.5% < 3% → 不买
        ("2020-01-03", 10.05, 10.2, 10.0, 10.1, 0.5),
    ]
    engine = BacktestEngine(
        {"600519": make_daily(rows=rows)}, OvernightHoldStrategy,
        params={"min_pct": 3.0, "volume_ratio_min": 1.0},
        config=BacktestConfig(initial_capital=100_000, slippage=0.0),
    )
    result = engine.run()
    assert not [t for t in result.trades if t.direction.value == "buy"]


def test_overnight_skips_high_pct():
    """涨幅超上限（逼近涨停）不追。"""
    rows = [
        ("2020-01-02", 10.0, 10.9, 9.9, 10.85, 8.5),  # 8.5% > max 7% → 不买
        ("2020-01-03", 10.85, 11.0, 10.7, 10.9, 0.5),
    ]
    engine = BacktestEngine(
        {"600519": make_daily(rows=rows)}, OvernightHoldStrategy,
        params={"max_pct": 7.0, "volume_ratio_min": 1.0},
        config=BacktestConfig(initial_capital=100_000, slippage=0.0),
    )
    result = engine.run()
    assert not [t for t in result.trades if t.direction.value == "buy"]
