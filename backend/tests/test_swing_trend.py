"""波段策略模板测试：突破入场+移动止损、风险公式仓位、回调买点、均线止损。"""
import numpy as np
import pandas as pd
import pytest

from app.engine.backtester import BacktestConfig, BacktestEngine
from app.strategies.swing_trend import SwingTrendStrategy
from tests.helpers import make_daily


def _uptrend_then_reversal(n_up: int = 40, n_down: int = 10, start: float = 50.0) -> list:
    """先温和上涨（收盘价不断创小新高、趋势过滤通过）后回落（触发移动止损）。

    high/low 收紧（±0.5%）：每日 0.8% 的涨幅使收盘价能突破前一日最高价。
    """
    rows = []
    dates = pd.date_range("2023-01-03", periods=n_up + n_down, freq="B")
    for i, d in enumerate(dates):
        if i < n_up:
            close = start * (1 + 0.008 * i)          # 稳定上行
        else:
            close = rows[-1][4] * 0.93               # 连续大跌触发止损
        o, h, l = close * 0.997, close * 1.005, close * 0.995
        pct = (close / rows[-1][4] - 1) * 100 if rows else 0.0
        rows.append((d.strftime("%Y-%m-%d"), round(o, 2), round(h, 2), round(l, 2),
                     round(close, 2), round(pct, 2)))
    return rows


def test_breakout_entry_and_trailing_stop():
    """突破买点 + 移动止损：买入成交后大跌触发止损，次日开盘卖出。"""
    rows = _uptrend_then_reversal()
    engine = BacktestEngine(
        {"600519": make_daily(rows=rows)}, SwingTrendStrategy,
        params={"trend_ma": 30, "entry_mode": "breakout", "breakout_window": 10,
                "stop_mode": "trailing", "trailing_pct": 0.08, "exit_ma": 10, "risk_pct": 0.05},
        config=BacktestConfig(initial_capital=200_000, slippage=0.0),
    )
    result = engine.run()
    buys = [t for t in result.trades if t.direction.value == "buy"]
    sells = [t for t in result.trades if t.direction.value == "sell"]
    assert len(buys) == 1  # 突破买入
    assert len(sells) == 1  # 移动止损卖出
    # 卖出成交价 ≈ 买入后大跌的开盘（-7% 连续大跌首日开盘即卖出）
    assert sells[0].price < buys[0].price


def test_risk_position_sizing():
    """风险公式仓位：单笔风险 ≈ 总资金 × risk_pct。"""
    rows = _uptrend_then_reversal(n_up=40, n_down=3)
    engine = BacktestEngine(
        {"600519": make_daily(rows=rows)}, SwingTrendStrategy,
        params={"trend_ma": 30, "entry_mode": "breakout", "stop_mode": "trailing",
                "trailing_pct": 0.08, "risk_pct": 0.02},
        config=BacktestConfig(initial_capital=200_000, slippage=0.0),
    )
    result = engine.run()
    buys = [t for t in result.trades if t.direction.value == "buy"]
    assert buys
    # 单笔风险 = 股数 × 每股风险(收盘价×8%) ≈ 总资金 × 2%
    risk = buys[0].volume * buys[0].price * 0.08
    assert risk == pytest.approx(200_000 * 0.02, rel=0.15)


def test_pullback_entry_mode():
    """回调买点：回踩均线收阳触发买入。"""
    n = 60
    close = np.linspace(50, 60, n)
    # 构造回踩：第 50 天低点触及 20 日线附近后收阳
    dates = pd.date_range("2023-01-03", periods=n, freq="B").strftime("%Y-%m-%d")
    rows = []
    for i, d in enumerate(dates):
        c = close[i]
        if i == 50:  # 回踩日：低点打到均线下方 1% 内，收阳
            o, h, l = c * 0.985, c * 1.01, c * 0.975
        else:
            o, h, l = c * 0.995, c * 1.015, c * 0.99
        prev = rows[-1][4] if rows else c
        rows.append((d, round(o, 2), round(h, 2), round(l, 2), round(c, 2),
                     round((c / prev - 1) * 100, 2)))
    engine = BacktestEngine(
        {"600519": make_daily(rows=rows)}, SwingTrendStrategy,
        params={"trend_ma": 30, "entry_mode": "pullback", "pullback_ma": 20,
                "stop_mode": "trailing", "risk_pct": 0.05},
        config=BacktestConfig(initial_capital=200_000, slippage=0.0),
    )
    result = engine.run()
    buys = [t for t in result.trades if t.direction.value == "buy"]
    assert len(buys) >= 1


def test_ma_stop_mode_exits():
    """均线止损模式：收盘跌破止损均线卖出（不走引擎止损）。"""
    rows = _uptrend_then_reversal(n_up=40, n_down=15)
    engine = BacktestEngine(
        {"600519": make_daily(rows=rows)}, SwingTrendStrategy,
        params={"trend_ma": 30, "entry_mode": "breakout", "stop_mode": "ma",
                "stop_ma": 10, "exit_ma": 10, "risk_pct": 0.05},
        config=BacktestConfig(initial_capital=200_000, slippage=0.0),
    )
    result = engine.run()
    buys = [t for t in result.trades if t.direction.value == "buy"]
    sells = [t for t in result.trades if t.direction.value == "sell"]
    assert len(buys) == 1
    assert len(sells) == 1  # 破位离场


def test_no_entry_in_downtrend():
    """下跌趋势：趋势过滤拦截，无买入。"""
    rows = []
    dates = pd.date_range("2023-01-03", periods=70, freq="B")
    for i, d in enumerate(dates):
        c = 60 * (1 - 0.008 * i)
        rows.append((d.strftime("%Y-%m-%d"), round(c * 1.005, 2), round(c * 1.02, 2),
                     round(c * 0.98, 2), round(c, 2), -0.8))
    engine = BacktestEngine(
        {"600519": make_daily(rows=rows)}, SwingTrendStrategy,
        params={"trend_ma": 30}, config=BacktestConfig(initial_capital=200_000),
    )
    result = engine.run()
    assert not [t for t in result.trades if t.direction.value == "buy"]
