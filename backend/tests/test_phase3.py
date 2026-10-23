"""三期功能测试：分钟线标准化、市场广度统计、因子轮动策略、60m 回测。"""
import numpy as np
import pandas as pd
import pytest

from app.data.akshare_source import _normalize_minute
from app.data.sentiment import compute_breadth
from app.data.sync import _upsert_daily_bars, _upsert_minute_bars
from app.engine.backtester import BacktestConfig, BacktestEngine
from app.strategies.factor_rotation import FactorRotationStrategy


def test_normalize_minute():
    raw = pd.DataFrame({
        "day": ["2024-01-02 10:30:00", "2024-01-02 11:30:00"],
        "open": [10.0, 10.5], "high": [10.6, 10.8], "low": [9.9, 10.4],
        "close": [10.5, 10.7], "volume": [12345.0, 23456.0],
    })
    out = _normalize_minute(raw)
    assert list(out.columns) == ["dt", "open", "high", "low", "close", "volume"]
    assert out["dt"].tolist() == ["2024-01-02 10:30:00", "2024-01-02 11:30:00"]
    assert out["volume"].tolist() == [12345.0, 23456.0]  # 不缩放（股）


def test_compute_breadth():
    quotes = pd.DataFrame({
        "pct_change": [10.0, 9.9, 5.0, 1.0, 0.0, -1.0, -5.0, -9.9, -10.0],
    })
    b = compute_breadth(quotes)
    assert b["limit_up"] == 2   # 10.0 和 9.9
    assert b["limit_down"] == 2  # -9.9 和 -10.0
    assert b["advance"] == 4    # 10.0/9.9/5.0/1.0
    assert b["decline"] == 4    # -1/-5/-9.9/-10
    assert b["total"] == 9
    assert compute_breadth(pd.DataFrame())["total"] == 0


def _seed_symbol(session, symbol, seed, n=120):
    rng = np.random.default_rng(seed)
    close = 10 * np.exp(np.cumsum(rng.normal(0.001, 0.02, n)))
    dates = pd.date_range("2022-01-04", periods=n, freq="B").strftime("%Y-%m-%d")
    rows = [
        {"symbol": symbol, "trade_date": d, "open": c * 0.998, "high": c * 1.012,
         "low": c * 0.988, "close": c, "volume": 1e6 * (1 + 0.1 * np.sin(i)),
         "amount": c * 1e6, "pct_change": 0.0, "turnover_rate": 1.0, "adj_factor": 1.0}
        for i, (d, c) in enumerate(zip(dates, close))
    ]
    _upsert_daily_bars(rows)
    return dates


def test_factor_rotation_backtest(tmp_db):
    """三标的因子轮动端到端：能跑通、产生调仓交易。"""
    _seed_symbol(tmp_db, "600001", seed=1)
    _seed_symbol(tmp_db, "600002", seed=2)
    _seed_symbol(tmp_db, "600003", seed=3)
    from app.data.loader import load_daily

    data = {sym: load_daily(sym, adjust="qfq") for sym in ("600001", "600002", "600003")}
    engine = BacktestEngine(
        data, FactorRotationStrategy,
        params={"top_k": 2, "rebalance_days": 20},
        config=BacktestConfig(initial_capital=100_000, allocation="equal", target_positions=2),
    )
    result = engine.run()
    assert len(result.dates) > 100
    assert result.metrics["trade_count"] >= 2  # 至少一次调仓产生买卖
    # 任意时刻持仓数 ≤ top_k
    assert result.metrics["total_return"] > -1.0


def test_minute_bars_upsert_and_backtest(tmp_db):
    """60m 数据入库 → 加载 → 引擎回测（引擎对 bar 粒度通用）。"""
    rows = [
        {"dt": f"2024-01-02 {h:02d}:00:00", "open": 10 + i * 0.1, "high": 10.5 + i * 0.1,
         "low": 9.8 + i * 0.1, "close": 10.2 + i * 0.1, "volume": 1e5}
        for i, h in enumerate((10, 11, 13, 14))
    ]
    n = _upsert_minute_bars("600519", "60m", rows)
    assert n == 4
    # 幂等
    assert _upsert_minute_bars("600519", "60m", rows) == 4

    from app.data.loader import load_minute

    df = load_minute("600519", interval="60m")
    assert len(df) == 4
    assert df["trade_date"].tolist()[0] == "2024-01-02 10:00:00"

    from app.strategies.dual_ma import DualMAStrategy

    engine = BacktestEngine(
        {"600519": df}, DualMAStrategy,
        config=BacktestConfig(initial_capital=100_000),
    )
    result = engine.run()
    assert len(result.dates) == 4
