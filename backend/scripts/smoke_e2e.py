"""端到端冒烟：数据 → 回测 → AI 日报 → 模拟盘 → API 检查。

有网络时同步 2 只股票真实数据；网络不可用时自动用合成数据继续（离线冒烟）。
任一步失败退出码非 0。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from app.data import database
from app.data.sync import _upsert_daily_bars, sync_trade_calendar, sync_watchlist_bars

SYMBOLS = ["600519", "000001"]


def _seed_synthetic():
    """离线兜底：合成 120 根日线入库。"""
    rng = np.random.default_rng(42)
    rows = []
    dates = pd.date_range("2022-01-04", periods=120, freq="B").strftime("%Y-%m-%d")
    for sym in SYMBOLS:
        close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.015, len(dates))))
        for i, d in enumerate(dates):
            rows.append({
                "symbol": sym, "trade_date": d,
                "open": close[i] * 0.998, "high": close[i] * 1.012,
                "low": close[i] * 0.988, "close": close[i],
                "volume": 1e6, "amount": close[i] * 1e6,
                "pct_change": 0.0, "turnover_rate": 1.0, "adj_factor": 1.0,
            })
    _upsert_daily_bars(rows)
    return "合成数据（离线模式）"


def step(name: str, fn) -> None:
    print(f"[{name}] ...", end=" ", flush=True)
    result = fn()
    print("OK" + (f" ({result})" if result else ""))
    return result


def main() -> int:
    database.init_db()

    # 1. 数据
    source = step("数据同步", lambda: (
        lambda: sync_watchlist_bars(SYMBOLS).get("updated") or _seed_synthetic()
    )())
    _ = source

    # 2. 回测
    from app.data.loader import load_daily
    from app.engine.backtester import BacktestConfig, BacktestEngine
    from app.strategies import StrategyRegistry

    data = {}
    for sym in SYMBOLS:
        df = load_daily(sym, start="2022-01-01", adjust="qfq")
        if not df.empty:
            data[sym] = df
    assert data, "回测数据为空"

    bt_metrics: dict = {}

    def run_backtest():
        bt_metrics.update(
            BacktestEngine(data, StrategyRegistry.get("dual_ma"),
                           config=BacktestConfig(initial_capital=1_000_000)).run().metrics
        )
        return f"总收益 {bt_metrics['total_return']:+.2%}"

    step("双均线回测", run_backtest)

    # 3. AI 日报（mock provider，离线可用）
    from app.llm.mock_provider import MockProvider
    from app.llm.tasks.daily_report import generate_daily_report

    step("AI 复盘日报", lambda: asyncio.run(
        generate_daily_report(provider=MockProvider(), date="2024-01-05")
    )["id"] and "日报已入库")

    # 4. 模拟盘
    from app.paper import account as paper_account

    step("模拟账户", lambda: f"总资产 {paper_account.account_summary()['total']:,.0f}")

    # 5. API 冒烟
    from fastapi.testclient import TestClient

    from app.main import app

    def api_check():
        with TestClient(app) as client:
            assert client.get("/api/health").json()["status"] == "ok"
            assert len(client.get("/api/strategies").json()) >= 3
            assert len(client.get("/api/market/bars/600519").json()) > 50
            assert isinstance(client.get("/api/paper/account").json()["cash"], float)
            assert client.get("/api/ai/reports").json()
            assert isinstance(client.get("/api/system/config").json()["watchlist"], list)
        return "全部接口正常"

    step("API 检查", api_check)

    print("\n===== 端到端冒烟通过 =====")
    print(f"回测指标: 总收益 {bt_metrics['total_return']:+.2%}，最大回撤 {bt_metrics['max_drawdown']:.2%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
