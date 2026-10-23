"""费用模型：最低佣金 5 元、印花税仅卖出、过户费双向，引擎资金账手算对拍。"""
import pytest

from app.engine.backtester import BacktestConfig, BacktestEngine
from app.engine.costs import CostModel
from app.engine.objects import Direction
from app.strategies.base import Strategy
from tests.helpers import make_daily


def test_cost_model_pure():
    cm = CostModel()
    assert cm.commission(1000.0) == 5.0      # 最低佣金生效
    assert cm.commission(100000.0) == 25.0
    assert cm.stamp_tax(1000.0, Direction.SELL) == 0.5
    assert cm.stamp_tax(1000.0, Direction.BUY) == 0.0
    assert cm.transfer_fee(1000.0) == 0.01
    # 1200 元卖出：佣金 max(0.3,5)=5 + 印花税 0.6 + 过户费 0.012 → 5.61
    assert cm.total_fee(12.0, 100, Direction.SELL) == pytest.approx(5.61)


class RoundTripStrategy(Strategy):
    name = "test_costs"

    def on_bar(self, bar):
        if bar.trade_date == "2020-01-02":
            self.buy(bar.symbol, volume=100)
        elif bar.trade_date == "2020-01-03":
            self.sell(bar.symbol, volume=100)


def test_engine_cash_accounting():
    rows = [
        ("2020-01-02", 10.0, 10.5, 9.8, 10.2, 2.0),
        ("2020-01-03", 12.0, 12.5, 11.8, 12.3, 2.5),
        ("2020-01-06", 12.5, 12.6, 12.2, 12.4, -0.8),
    ]
    engine = BacktestEngine(
        {"600519": make_daily(rows=rows)}, RoundTripStrategy,
        config=BacktestConfig(initial_capital=100_000, slippage=0.0),
    )
    result = engine.run()

    # 01-03 开盘 12.0 买入 100 股：佣金 max(1200×0.00025,5)=5 + 过户费 0.012 → 5.01
    # cash = 100000 - 1200 - 5.01 = 98794.99
    # 01-06 开盘 12.5 卖出：佣金 5 + 印花税 0.625 + 过户费 0.0125 → 5.64
    # cash = 98794.99 + 1250 - 5.64 = 100039.35
    assert result.cash[-1] == pytest.approx(100039.35, abs=0.01)

    sell = [t for t in result.trades if t.direction.value == "sell"][0]
    assert sell.pnl == pytest.approx((12.5 - 12.0) * 100 - 5.64, abs=0.01)

    buy = [t for t in result.trades if t.direction.value == "buy"][0]
    assert buy.commission == pytest.approx(5.01, abs=0.001)
