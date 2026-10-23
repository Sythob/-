"""HTML 报告与参数优化测试（离线）。"""
import pandas as pd

from app.engine.optimizer import grid_search
from app.engine.report import generate_report_html
from app.strategies.base import Strategy
from app.strategies.dual_ma import DualMAStrategy
from tests.helpers import make_daily


def _sample_run() -> dict:
    dates = make_daily()["trade_date"].tolist()
    return {
        "id": 1, "strategy_name": "dual_ma", "params": {"fast": 5, "slow": 20},
        "universe": ["600519"], "start_date": "2020-01-02", "end_date": "2020-01-07",
        "capital": 1_000_000,
        "metrics": {
            "total_return": 0.12, "annualized_return": 0.31, "max_drawdown": -0.08,
            "sharpe_ratio": 1.4, "sortino_ratio": 1.9, "calmar_ratio": 3.8,
            "win_rate": 0.6, "profit_loss_ratio": 1.8, "trade_count": 12,
            "round_trips": 6, "avg_holding_days": 5.2, "turnover_rate": 1.1,
            "max_consecutive_wins": 3, "max_consecutive_losses": 2,
            "final_equity": 1_120_000,
        },
        "equity": [
            {"date": d, "equity": 1_000_000 + i * 10000}
            for i, d in enumerate(dates)
        ],
        "trades": [
            {"trade_time": d, "symbol": "600519", "direction": "buy",
             "price": 10.0, "volume": 100, "commission": 5.0, "pnl": 0.0}
            for d in dates[:3]
        ],
    }


def test_report_html_sections():
    html = generate_report_html(_sample_run())
    assert html.startswith("<!DOCTYPE html>")
    assert "回测报告 #1" in html
    assert "净值曲线" in html
    assert "回撤曲线" in html
    assert "月度收益热力图" in html
    assert "滚动夏普" in html
    assert "成交明细" in html
    assert "<svg" in html  # 内联 SVG，无 CDN 依赖
    body = html.split("<body")[1]
    assert "<script src" not in body and "<link " not in body  # 无外部资源引用


def test_grid_search_ranks():
    data = {"600519": make_daily()}
    grid = {"fast": [3, 5], "slow": [10, 20]}
    from app.engine.backtester import BacktestConfig

    result = grid_search(data, DualMAStrategy, grid,
                         config=BacktestConfig(initial_capital=100_000), max_workers=2)
    assert result["total"] == 4
    assert len(result["results"]) == 4
    assert all("total_return" in r["metrics"] for r in result["results"])
    # 按 total_return 降序
    values = [r["metrics"]["total_return"] for r in result["results"]]
    assert values == sorted(values, reverse=True)
    assert "warning" in result


def test_grid_search_rejects_huge_grid():
    from app.engine.backtester import BacktestConfig

    grid = {"fast": list(range(1, 21)), "slow": list(range(1, 21))}
    import pytest

    with pytest.raises(ValueError):
        grid_search({"600519": make_daily()}, DualMAStrategy, grid,
                    config=BacktestConfig(initial_capital=100_000))
