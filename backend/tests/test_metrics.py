"""绩效指标手算对拍。"""
import pandas as pd
import pytest

from app.engine.metrics import (
    annualized_return,
    compute_metrics,
    max_drawdown,
    round_trip_stats,
    sharpe_ratio,
)
from app.engine.objects import Direction, TradeData


def _eq(values):
    dates = pd.date_range("2020-01-02", periods=len(values), freq="B")
    return pd.Series(values, index=dates, dtype=float)


def test_max_drawdown():
    eq = _eq([100, 120, 90, 110])
    mdd = max_drawdown(eq)
    assert mdd["mdd"] == pytest.approx(90 / 120 - 1)  # -25%
    assert mdd["peak_date"] == "2020-01-03"
    assert mdd["trough_date"] == "2020-01-06"


def test_annualized_return():
    eq = _eq([100, 110, 121])  # 2 天 21%
    expected = 1.21 ** (250 / 3) - 1
    assert annualized_return(eq, 100.0) == pytest.approx(expected)


def test_sharpe_zero_variance():
    assert sharpe_ratio(_eq([100.0] * 10)) == 0.0


def test_round_trip_stats():
    trades = [
        TradeData("600519", Direction.SELL, 12.0, 100, "2020-01-06", pnl=100.0),
        TradeData("600519", Direction.SELL, 11.0, 100, "2020-01-07", pnl=-50.0),
        TradeData("600519", Direction.SELL, 10.0, 100, "2020-01-08", pnl=0.0),
        TradeData("600519", Direction.BUY, 10.0, 100, "2020-01-03"),
    ]
    rt = round_trip_stats(trades)
    assert rt["round_trips"] == 2  # pnl != 0 的卖出
    assert rt["win_rate"] == 0.5
    assert rt["profit_loss_ratio"] == 2.0  # 100 / 50


def test_compute_metrics_smoke():
    dates = ["2020-01-02", "2020-01-03", "2020-01-06"]
    equity = [100_000.0, 101_000.0, 102_000.0]
    trades = [
        TradeData("600519", Direction.BUY, 10.0, 100, "2020-01-03"),
        TradeData("600519", Direction.SELL, 11.0, 100, "2020-01-06", pnl=95.0),
    ]
    bench = pd.DataFrame({
        "trade_date": ["2020-01-02", "2020-01-03", "2020-01-06"],
        "close": [100.0, 101.0, 103.0],
    })
    m = compute_metrics(dates, equity, 100_000.0, trades, benchmark=bench)
    assert m["total_return"] == pytest.approx(0.02)
    assert m["win_rate"] == 1.0
    assert m["trade_count"] == 2
    assert m["benchmark_return"] == pytest.approx(0.03)
    assert m["excess_return"] == pytest.approx(-0.01)
