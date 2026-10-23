"""防未来函数重算校验：t 处特征与「截断到 t 的数据」重算结果完全一致。"""
import numpy as np
import pandas as pd
import pytest

from app.ml.features import build_features, feature_columns


def _make_df(n: int = 60, seed: int = 42) -> pd.DataFrame:
    """随机游走 + 结尾暴涨（若存在泄漏，截断重算会立即暴露差异）。"""
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    close[-5:] *= 2.5  # 结尾暴涨：任何前视都会放大
    open_ = close * (1 + rng.normal(0, 0.005, n))
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.01, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.01, n))
    volume = rng.uniform(1e6, 5e6, n)
    dates = pd.date_range("2020-01-02", periods=n, freq="B").strftime("%Y-%m-%d")
    return pd.DataFrame({
        "trade_date": dates, "open": open_, "high": high, "low": low, "close": close,
        "volume": volume, "amount": volume * close, "pct_change": np.r_[0, np.diff(close) / close[:-1] * 100],
        "turnover_rate": rng.uniform(0.5, 3.0, n),
    })


def test_features_no_lookahead():
    df = _make_df(n=100)
    full = build_features(df)
    feats = feature_columns(full)
    full = full.dropna(subset=feats)
    assert len(full) > 30

    # 有效行要求：最长回看窗口 60 根 + 标签存在（非末行）
    for t in (70, 80, 85):
        truncated = build_features(df.iloc[: t + 1]).dropna(subset=feats)
        row_full = full[full["trade_date"] == df["trade_date"].iloc[t]]
        row_trunc = truncated[truncated["trade_date"] == df["trade_date"].iloc[t]]
        assert len(row_full) == 1 and len(row_trunc) == 1
        for col in feats:
            assert row_full[col].iloc[0] == pytest.approx(
                row_trunc[col].iloc[0], nan_ok=True
            ), f"特征 {col} 在 t={t} 处存在未来函数"


def test_label_is_future_aware():
    """label 必须使用 t+1 收益（这是唯一允许使用未来的列）。"""
    df = _make_df(10)
    out = build_features(df)
    # 已知 close 单调上涨 → 除最后一行外 label 全为 1
    up = pd.DataFrame({
        "trade_date": pd.date_range("2020-01-02", periods=5, freq="B").strftime("%Y-%m-%d"),
        "open": [10, 11, 12, 13, 14], "high": [10.5, 11.5, 12.5, 13.5, 14.5],
        "low": [9.5, 10.5, 11.5, 12.5, 13.5], "close": [10, 11, 12, 13, 14],
        "volume": [1e6] * 5, "amount": [1e7] * 5, "pct_change": [0, 10, 9.09, 8.33, 7.69],
        "turnover_rate": [1.0] * 5,
    })
    out = build_features(up)
    assert out["label"].iloc[:4].tolist() == [1, 1, 1, 1]
    assert np.isnan(out["label"].iloc[4]) or out["label"].iloc[4] == 0  # 末行无 t+1
