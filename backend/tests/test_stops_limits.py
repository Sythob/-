"""止损止盈与亏损限额测试。"""
import pytest

from app.engine.backtester import BacktestConfig, BacktestEngine
from app.strategies.base import Strategy
from tests.helpers import make_daily


class BuyWithStop(Strategy):
    """首日买入并挂移动止损；若被止损卖出则不再买入。"""
    name = "test_stop"
    params = {"stop_pct": 0.15}

    def on_bar(self, bar):
        pos = self.position(bar.symbol)
        if bar.trade_date == "2020-01-02":
            self.buy(bar.symbol, volume=100)
            self.set_stop(bar.symbol, "trailing", trailing_pct=self.params["stop_pct"])


def test_trailing_stop_triggered():
    """价格冲高后回落超过 15% → 止损触发 → 次日开盘卖出。"""
    rows = [
        ("2020-01-02", 10.0, 10.5, 9.8, 10.2, 2.0),     # 买入信号
        ("2020-01-03", 10.2, 10.8, 10.0, 10.5, 2.9),    # 开盘成交（无涨停）
        ("2020-01-06", 10.5, 12.5, 10.4, 12.4, 18.1),   # 冲高（highest=12.4）
        ("2020-01-07", 12.4, 12.5, 10.0, 10.3, -16.9),  # 回落 16.9% > 15% → 触发
        ("2020-01-08", 10.3, 10.5, 10.0, 10.1, -1.9),   # 次日开盘卖出
    ]
    engine = BacktestEngine(
        {"600519": make_daily(rows=rows)}, BuyWithStop,
        config=BacktestConfig(initial_capital=100_000, slippage=0.0),
    )
    result = engine.run()
    buys = [t for t in result.trades if t.direction.value == "buy"]
    sells = [t for t in result.trades if t.direction.value == "sell"]
    assert len(buys) == 1
    assert len(sells) == 1
    assert sells[0].trade_time == "2020-01-08"  # 触发次日成交（无未来函数）


def test_stop_not_triggered_when_price_recovers():
    rows = [
        ("2020-01-02", 10.0, 10.5, 9.8, 10.2, 2.0),
        ("2020-01-03", 10.2, 10.8, 10.0, 10.6, 3.9),   # 正常上涨
        ("2020-01-06", 10.6, 11.0, 10.4, 10.9, 2.8),   # 未回落超 15%
        ("2020-01-07", 10.9, 11.0, 10.6, 10.8, -0.9),
    ]
    engine = BacktestEngine(
        {"600519": make_daily(rows=rows)}, BuyWithStop,
        config=BacktestConfig(initial_capital=100_000, slippage=0.0),
    )
    result = engine.run()
    assert len([t for t in result.trades if t.direction.value == "sell"]) == 0


class BuyOnce(Strategy):
    name = "test_buy_once"

    def on_bar(self, bar):
        if bar.trade_date == "2020-01-02":
            self.buy(bar.symbol, volume=100)


def test_fixed_stop_and_take_profit():
    """固定止损：close ≤ 9.5 → 卖出；固定止盈：close ≥ 11.5 → 卖出。"""
    rows_loss = [
        ("2020-01-02", 10.0, 10.5, 9.8, 10.2, 2.0),
        ("2020-01-03", 10.2, 10.3, 9.4, 9.5, -6.9),   # 破 9.5 → 固定止损触发
        ("2020-01-06", 9.5, 9.6, 9.3, 9.4, -1.1),
    ]

    class FixedStop(Strategy):
        name = "test_fixed_stop"

        def on_bar(self, bar):
            if bar.trade_date == "2020-01-02":
                self.buy(bar.symbol, volume=100)
                self.set_stop(bar.symbol, "fixed", trigger_price=9.5)

    engine = BacktestEngine(
        {"600519": make_daily(rows=rows_loss)}, FixedStop,
        config=BacktestConfig(initial_capital=100_000, slippage=0.0),
    )
    result = engine.run()
    sells = [t for t in result.trades if t.direction.value == "sell"]
    assert len(sells) == 1
    assert sells[0].trade_time == "2020-01-06"

    rows_profit = [
        ("2020-01-02", 10.0, 10.5, 9.8, 10.2, 2.0),     # 买入信号 + 止盈 11.5
        ("2020-01-03", 10.2, 10.8, 10.0, 10.5, 2.9),    # 开盘成交（无涨停）
        ("2020-01-06", 10.5, 11.6, 10.4, 11.5, 9.5),    # 达 11.5 → 止盈触发（9.5% < 涨停线）
        ("2020-01-07", 11.5, 11.6, 11.2, 11.4, -0.9),   # 次日开盘卖出
    ]

    class TakeProfit(Strategy):
        name = "test_take_profit"

        def on_bar(self, bar):
            if bar.trade_date == "2020-01-02":
                self.buy(bar.symbol, volume=100)
                self.set_stop(bar.symbol, "take_profit", trigger_price=11.5)

    engine2 = BacktestEngine(
        {"600519": make_daily(rows=rows_profit)}, TakeProfit,
        config=BacktestConfig(initial_capital=100_000, slippage=0.0),
    )
    result2 = engine2.run()
    sells2 = [t for t in result2.trades if t.direction.value == "sell"]
    assert len(sells2) == 1
    assert sells2[0].trade_time == "2020-01-07"


def test_max_loss_per_trade():
    """单笔亏损超 10% → 强制卖出。"""
    rows = [
        ("2020-01-02", 10.0, 10.5, 9.8, 10.2, 2.0),
        ("2020-01-03", 10.2, 10.3, 8.9, 9.0, -11.8),  # 亏损 11.8% > 10%
        ("2020-01-06", 9.0, 9.1, 8.8, 8.9, -1.1),
    ]
    engine = BacktestEngine(
        {"600519": make_daily(rows=rows)}, BuyOnce,
        config=BacktestConfig(initial_capital=100_000, slippage=0.0,
                              max_loss_per_trade_pct=0.10),
    )
    result = engine.run()
    sells = [t for t in result.trades if t.direction.value == "sell"]
    assert len(sells) == 1
    assert sells[0].trade_time == "2020-01-06"


class KeepBuying(Strategy):
    """每天无脑买入（测单日亏损限额的暂停开仓）。"""
    name = "test_keep_buying"

    def on_bar(self, bar):
        if self.cash > 500:
            self.buy(bar.symbol, volume=100)


def test_daily_loss_halt():
    """单日大跌触发限额 → 次日开仓被拒。"""
    rows = [
        ("2020-01-02", 10.0, 10.5, 9.8, 10.2, 2.0),    # 买入信号（次日成交）
        ("2020-01-03", 10.2, 10.3, 8.0, 8.1, -20.6),   # 开盘 400 股买入后暴跌 → 权益跌 17%
        ("2020-01-06", 8.1, 8.2, 8.0, 8.1, 0.0),       # 次日买入应被拒（亏损限额）
    ]
    engine = BacktestEngine(
        {"600519": make_daily(rows=rows)}, KeepBuying,
        config=BacktestConfig(initial_capital=2_000, slippage=0.0,
                              max_daily_loss_pct=0.05),
    )
    result = engine.run()
    buys = [t for t in result.trades if t.direction.value == "buy"]
    assert len(buys) == 1  # 只有首日买入成功（100 股 = 51% 仓位，跌幅放大到权益 -10%）
    rejected = [o for o in result.orders if o.status.value == "rejected"]
    assert any("亏损限额" in o.reject_reason for o in rejected)
