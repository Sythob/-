"""仓位分配模式测试：fixed 固定比例、equal 等权、kelly 半凯利。"""
import pytest

from app.engine.backtester import BacktestConfig, BacktestEngine
from app.strategies.base import Strategy
from tests.helpers import make_daily


class BuyBothDayOne(Strategy):
    """第一天同时买入两个标的（自动数量），验证分配预算。"""
    name = "test_alloc"

    def on_bar(self, bar):
        if bar.trade_date == "2020-01-02":
            self.buy(bar.symbol)  # 自动数量


TWO_SYMBOLS = {
    "600519": make_daily(),
    "000001": make_daily(symbol="000001"),
}


def _run(config: BacktestConfig):
    engine = BacktestEngine(TWO_SYMBOLS, BuyBothDayOne, config=config)
    return engine.run()


def test_fixed_fraction_budget():
    """fixed=0.5：单笔预算 = 总资产 × 0.5，两笔合计 ≈ 全部资金的 50% × 2。"""
    result = _run(BacktestConfig(initial_capital=100_000, slippage=0.0,
                                 allocation="fixed", fixed_fraction=0.5))
    buys = [t for t in result.trades if t.direction.value == "buy"]
    assert len(buys) == 2
    # 每笔成交额 ≈ 100000 × 0.5（整手向下取整）
    for t in buys:
        assert t.price * t.volume == pytest.approx(50_000, rel=0.05)
    # 现金几乎用完
    assert result.cash[-1] < 3_000


def test_equal_weight_budget():
    """equal 目标 2 仓：每笔预算 = 总资产 / 2。"""
    result = _run(BacktestConfig(initial_capital=100_000, slippage=0.0,
                                 allocation="equal", target_positions=2))
    buys = [t for t in result.trades if t.direction.value == "buy"]
    assert len(buys) == 2
    for t in buys:
        assert t.price * t.volume == pytest.approx(50_000, rel=0.05)


def test_kelly_conservative():
    """kelly(0.5, 1.0) → f=0 → 买不进（预算 0 拒单）；kelly(0.6, 1.5) → 半凯利 f≈0.166。"""
    result = _run(BacktestConfig(initial_capital=100_000, slippage=0.0,
                                 allocation="kelly", kelly_win_rate=0.5, kelly_payoff=1.0))
    assert not [t for t in result.trades if t.direction.value == "buy"]  # f=0 全部拒单

    result2 = _run(BacktestConfig(initial_capital=100_000, slippage=0.0,
                                  allocation="kelly", kelly_win_rate=0.6, kelly_payoff=1.5))
    buys = [t for t in result2.trades if t.direction.value == "buy"]
    assert len(buys) == 2
    # f = 0.6 - 0.4/1.5 = 0.3333 → 半凯利 0.1667
    for t in buys:
        assert t.price * t.volume == pytest.approx(100_000 * 0.1667, rel=0.1)


def test_full_mode_default():
    """默认 full：第一笔买光现金，第二笔资金不足拒单。"""
    result = _run(BacktestConfig(initial_capital=100_000, slippage=0.0))
    buys = [t for t in result.trades if t.direction.value == "buy"]
    assert len(buys) == 1
    rejected = [o for o in result.orders if o.status.value == "rejected"]
    assert any("资金不足" in o.reject_reason for o in rejected)
