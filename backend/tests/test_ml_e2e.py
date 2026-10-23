"""ML 端到端：造数入库 → 训练 → MLSignal 策略回测（全部离线）。"""
import numpy as np
import pandas as pd

from app.data.sync import _upsert_daily_bars
from app.engine.backtester import BacktestConfig, BacktestEngine
from app.ml.persistence import list_models
from app.ml.train import train_pipeline
from app.strategies.ml_signal import MLSignalStrategy


def _seed_random_walk(tmp_db, symbol: str = "600001", n: int = 200, seed: int = 7):
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0005, 0.02, n)
    close = 100 * np.exp(np.cumsum(rets))
    open_ = close * (1 + rng.normal(0, 0.003, n))
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.008, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.008, n))
    volume = rng.uniform(1e6, 5e6, n)
    dates = pd.date_range("2020-01-02", periods=n, freq="B").strftime("%Y-%m-%d")
    rows = [
        {"symbol": symbol, "trade_date": d, "open": o, "high": h, "low": l, "close": c,
         "volume": v, "amount": v * c, "pct_change": p, "turnover_rate": t, "adj_factor": 1.0}
        for d, o, h, l, c, v, p, t in zip(
            dates, open_, high, low, close, volume,
            np.r_[0, np.diff(close) / close[:-1] * 100],
            rng.uniform(0.5, 3.0, n),  # 换手率不能恒定，否则 turnover_z 除以零
        )
    ]
    _upsert_daily_bars(rows)
    return dates


def test_train_and_backtest_ml_signal(tmp_db):
    dates = _seed_random_walk(tmp_db, n=300)
    n = len(dates)

    # 训练（小窗口适配合成数据）
    report = train_pipeline(
        ["600001"], train_days=120, test_days=30, embargo_days=1, name="test_model"
    )
    assert report["model_id"] > 0
    assert 0 < report["oos_auc"] < 1 or report["oos_auc"] == 0.5  # 随机数据 AUC 接近 0.5
    assert len(report["folds"]) >= 1
    assert len(report["features"]) > 20

    # 模型登记与文件落盘
    models = list_models()
    assert models and models[0]["name"] == "test_model"

    # MLSignal 策略回测端到端
    from app.data.loader import load_daily

    data = {"600001": load_daily("600001", adjust="qfq")}
    engine = BacktestEngine(
        data, MLSignalStrategy,
        params={"model_name": "test_model", "buy_threshold": 0.55, "sell_threshold": 0.45},
        config=BacktestConfig(initial_capital=1_000_000),
    )
    result = engine.run()
    assert len(result.dates) == n
    assert "total_return" in result.metrics
    # 随机数据 + 阈值 0.55 → 大概率很少或无交易，但引擎必须正常跑完
    assert result.metrics["trade_count"] >= 0


def test_ml_signal_without_model_idles(tmp_db):
    """未配置模型时策略空转，不报错、不交易。"""
    _seed_random_walk(tmp_db, n=60)
    from app.data.loader import load_daily

    engine = BacktestEngine(
        {"600001": load_daily("600001", adjust="qfq")}, MLSignalStrategy,
        params={"model_name": ""}, config=BacktestConfig(initial_capital=1_000_000),
    )
    result = engine.run()
    assert result.metrics["trade_count"] == 0
