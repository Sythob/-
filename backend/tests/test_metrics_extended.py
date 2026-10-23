"""扩展绩效指标测试：索提诺/卡玛/连赢连亏/Alpha/Beta/信息比率。"""
import pandas as pd
import pytest

from app.engine.metrics import (
    alpha_beta,
    calmar_ratio,
    information_ratio,
    rolling_sharpe,
    sortino_ratio,
    streak_stats,
)
from app.engine.objects import Direction, TradeData


def _eq(values):
    return pd.Series(values, index=pd.date_range("2020-01-02", periods=len(values), freq="B"),
                     dtype=float)


def _bench(values):
    dates = pd.date_range("2020-01-02", periods=len(values), freq="B").strftime("%Y-%m-%d")
    return pd.DataFrame({"trade_date": dates, "close": values})


def test_sortino_penalizes_drawdown_only():
    up = _eq([100, 102, 104, 106, 108])      # 无下行波动 → 索提诺为 0（定义）
    assert sortino_ratio(up) == 0.0
    # 至少 2 个下行日才能计算
    mixed = _eq([100, 103, 101, 104, 106, 102, 105, 103, 108, 110])
    assert sortino_ratio(mixed) > 0


def test_calmar_ratio():
    eq = _eq([100, 110, 105, 115])  # 最大回撤 ~ -4.55%
    ann_expected = (115 / 100) ** (250 / 4) - 1
    mdd_expected = abs(105 / 110 - 1)
    assert calmar_ratio(eq, 100.0) == pytest.approx(ann_expected / mdd_expected)


def test_streak_stats():
    trades = [
        TradeData("a", Direction.SELL, 10, 100, "d1", pnl=10.0),
        TradeData("a", Direction.SELL, 10, 100, "d2", pnl=5.0),
        TradeData("a", Direction.SELL, 10, 100, "d3", pnl=-3.0),
        TradeData("a", Direction.SELL, 10, 100, "d4", pnl=2.0),
        TradeData("a", Direction.SELL, 10, 100, "d5", pnl=-1.0),
        TradeData("a", Direction.SELL, 10, 100, "d6", pnl=-2.0),
        TradeData("a", Direction.SELL, 10, 100, "d7", pnl=-4.0),
    ]
    s = streak_stats(trades)
    assert s["max_consecutive_wins"] == 2
    assert s["max_consecutive_losses"] == 3


def test_alpha_beta_aligned():
    # 策略日收益 = 2 × 基准日收益 → beta=2, alpha≈0
    bench_ret = [0.01, -0.005, 0.02, 0.0, 0.015] * 6  # 30 天
    bench_close = [100.0]
    for r in bench_ret:
        bench_close.append(bench_close[-1] * (1 + r))
    bench = _bench(bench_close)
    strat_close = [100.0]
    for r in bench_ret:
        strat_close.append(strat_close[-1] * (1 + 2 * r))
    strat = _eq(strat_close)
    result = alpha_beta(strat, bench)
    assert result["beta"] == pytest.approx(2.0, rel=0.02)
    assert abs(result["alpha"]) < 0.2


def test_information_ratio_positive():
    bench_close = [100 * (1.001 ** i) for i in range(30)]
    strat_close = [100 * (1.0015 ** i) for i in range(30)]
    ir = information_ratio(_eq(strat_close), _bench(bench_close))
    assert ir > 0


def test_rolling_sharpe_shape():
    rng = [100 * (1 + 0.0005 * ((i * 37) % 5 - 2) / 100) for i in range(120)]
    rs = rolling_sharpe(_eq(rng), window=60)
    assert len(rs["dates"]) == len(rs["values"]) == 120 - 60
