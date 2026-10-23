"""智能批量回测测试（离线，合成数据）。"""
from app.data.sync import _upsert_daily_bars
from app.engine.auto_backtest import _run_one, list_auto_backtests, run_auto_backtests


def _seed(tmp_db, symbol, n=200, seed=7, name="测试股"):
    import numpy as np
    import pandas as pd

    from app.data.models import Instrument

    rng = np.random.default_rng(seed)
    close = 50 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, n)))
    dates = pd.date_range("2022-01-04", periods=n, freq="B").strftime("%Y-%m-%d")
    rows = [
        {"symbol": symbol, "trade_date": d, "open": c * 0.998, "high": c * 1.012,
         "low": c * 0.988, "close": c, "volume": 1e6, "amount": c * 1e6,
         "pct_change": 0.0, "turnover_rate": 5.0, "adj_factor": 1.0}
        for d, c in zip(dates, close)
    ]
    _upsert_daily_bars(rows)
    with tmp_db() as session:
        session.add(Instrument(symbol=symbol, name=name, type="stock"))
        session.commit()


def test_run_one_records_metrics(tmp_db, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "watchlist", "600001")
    _seed(tmp_db, "600001")
    row = _run_one("dual_ma", {"fast": 5, "slow": 20}, "600001", "2022-01-01", "")
    assert row is not None
    assert 0.0 <= row["win_rate"] <= 1.0
    assert "total_return" in row
    assert row["trade_count"] >= 0
    assert row["symbol"] == "600001"


def test_auto_backtest_batch(tmp_db, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "watchlist", "600001")
    _seed(tmp_db, "600001")
    stats = run_auto_backtests(symbols=["600001"], start="2022-01-01", max_workers=2)
    assert stats["ran"] >= 7  # 8 组预设中非组合的全部跑（无因子轮动单标的）
    rows = list_auto_backtests()
    assert len(rows) >= 7
    # 排序按 total_return 降序
    values = [r["total_return"] for r in rows]
    assert values == sorted(values, reverse=True)
    # 战绩含胜率与收益率
    assert all("win_rate" in r and "total_return" in r for r in rows)


def test_data_is_fresh_and_ensure(tmp_db, monkeypatch):
    """数据新鲜度判断与补齐（离线打桩）。"""
    from app.data import calendar
    from app.data.sync import data_is_fresh, ensure_fresh_data

    # 无数据 → 不新鲜
    assert data_is_fresh() is False
    # 打桩：同步函数不触网
    monkeypatch.setattr("app.data.sync.sync_watchlist_bars",
                        lambda symbols=None: {"updated": ["x"], "failed": []})
    monkeypatch.setattr("app.data.sync.sync_index_bars",
                        lambda symbols=None: {"updated": ["y"], "failed": []})
    monkeypatch.setattr("app.data.sentiment.sync_breadth",
                        lambda: {"ok": True})
    result = ensure_fresh_data()
    assert result["status"] == "synced"
    assert "bars" in result and "breadth" in result


def test_auto_backtest_skip_fresh(tmp_db, monkeypatch):
    """24 小时内同组合跳过（第二次运行不重复计算）。"""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "watchlist", "600001")
    _seed(tmp_db, "600001")
    run_auto_backtests(symbols=["600001"], start="2022-01-01", max_workers=2)
    stats2 = run_auto_backtests(symbols=["600001"], start="2022-01-01", max_workers=2)
    assert stats2["ran"] == 0
    assert stats2["skipped"] >= 7
