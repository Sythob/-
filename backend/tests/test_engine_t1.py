"""T+1 铁律：当日买入当日卖出必被拒；次日解锁后卖出成功。"""
from app.engine.backtester import BacktestConfig, BacktestEngine
from app.strategies.base import Strategy
from tests.helpers import make_daily


class BuyThenSellSameDay(Strategy):
    name = "test_t1"

    def on_bar(self, bar):
        if bar.trade_date == "2020-01-02":
            self.buy(bar.symbol, volume=100)
            self.sell(bar.symbol, volume=100)  # 当日买当日卖 → 应被拒
        elif bar.trade_date == "2020-01-06":
            # 01-02 提交买单 → 01-03 开盘成交 → 01-03 收盘解锁 → 01-06 信号卖出
            self.sell(bar.symbol, volume=100)


def test_t1_same_day_sell_rejected():
    engine = BacktestEngine(
        {"600519": make_daily()}, BuyThenSellSameDay,
        config=BacktestConfig(initial_capital=1_000_000, slippage=0.0),
    )
    result = engine.run()

    # 01-02 同时提交的卖单：01-03 开盘检查可卖=0 → 拒单（T+1）
    rejected = [o for o in result.orders if o.status.value == "rejected"]
    assert any("可卖" in o.reject_reason for o in rejected)

    # 买单 01-03 开盘成交（次日成交铁律）
    buys = [t for t in result.trades if t.direction.value == "buy"]
    assert len(buys) == 1
    assert buys[0].trade_time == "2020-01-03"

    # 01-06 提交的卖单在 01-07 成交（01-03 收盘已解锁）
    sells = [t for t in result.trades if t.direction.value == "sell"]
    assert len(sells) == 1
    assert sells[0].trade_time == "2020-01-07"
    assert result.metrics["round_trips"] == 1
