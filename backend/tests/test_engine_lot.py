"""整手规则：买入向下取整到 100 股，卖出非整手直接拒单。"""
from app.engine.backtester import BacktestConfig, BacktestEngine
from app.strategies.base import Strategy
from tests.helpers import make_daily


class LotStrategy(Strategy):
    name = "test_lot"

    def on_bar(self, bar):
        if bar.trade_date == "2020-01-02":
            self.buy(bar.symbol, volume=150)  # → 向下取整 100
        elif bar.trade_date == "2020-01-03":
            self.sell(bar.symbol, volume=150)  # 非整手 → 提交即拒


def test_buy_rounds_down_to_lot():
    engine = BacktestEngine(
        {"600519": make_daily()}, LotStrategy,
        config=BacktestConfig(initial_capital=1_000_000, slippage=0.0),
    )
    result = engine.run()

    buy_order = [o for o in result.orders if o.direction.value == "buy"][0]
    assert buy_order.volume == 100  # 150 → 100

    # 卖 150 股（非整手）→ 下单即拒，不产生卖出成交
    sell_orders = [o for o in result.orders if o.direction.value == "sell"]
    assert sell_orders and all(o.status.value == "rejected" for o in sell_orders)
    assert any("整手" in o.reject_reason for o in sell_orders)
    assert not [t for t in result.trades if t.direction.value == "sell"]
