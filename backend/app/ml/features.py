"""特征工程：约 30 维技术特征。

防未来函数铁律：t 时刻特征只使用 ≤t 的数据（全部由 rolling/shift 等回看算子构造，
绝无未来值）。tests/test_features_no_leakage.py 用「截断重算」方式校验。

标签：t+1 日收益 > 0（最后一行无标签）。特征与标签之外的列见 RESERVED_COLS。
"""
import numpy as np
import pandas as pd

from app.ml.indicators import (
    atr, boll, cci, kdj, ma, macd, roc, rsi, volume_ma_ratio, volume_ratio, wr,
)

RESERVED_COLS = {
    "trade_date", "label", "next_ret",
    "open", "high", "low", "close", "volume", "amount", "pct_change", "turnover_rate",
}


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """输入日线（open/high/low/close/volume/turnover_rate/pct_change，trade_date 升序），
    输出特征表（含 label/next_ret）。"""
    out = pd.DataFrame(index=df.index)
    out["trade_date"] = df["trade_date"].astype(str)
    close, high, low = df["close"], df["high"], df["low"]
    open_, volume = df["open"], df["volume"]
    turnover = df["turnover_rate"] if "turnover_rate" in df.columns else pd.Series(0.0, index=df.index)

    # ---- 动量/收益 ----
    for n in (1, 3, 5, 10, 20):
        out[f"ret_{n}d"] = close.pct_change(n)

    # ---- 均线偏离 ----
    for n in (5, 10, 20, 60):
        out[f"bias_ma{n}"] = close / ma(close, n) - 1

    # ---- 摆动指标 ----
    out["rsi_14"] = rsi(close, 14)
    m = macd(close)
    out["macd_hist"] = m["hist"] / close
    k = kdj(high, low, close)
    out["kdj_k"], out["kdj_d"], out["kdj_j"] = k["k"], k["d"], k["j"]
    out["cci_14"] = cci(high, low, close, 14)
    out["wr_14"] = wr(high, low, close, 14)
    out["roc_12"] = roc(close, 12)

    # ---- 波动 ----
    out["atr_ratio"] = atr(high, low, close, 14) / close
    b = boll(close)
    out["boll_width"] = (b["upper"] - b["lower"]) / b["mid"]
    out["vol_20"] = close.pct_change().rolling(20).std()

    # ---- 量能 ----
    out["vol_ratio_5"] = volume_ratio(volume, 5)
    out["vol_ma_ratio"] = volume_ma_ratio(volume, 5, 10)
    out["turnover_z"] = (turnover - turnover.rolling(20).mean()) / turnover.rolling(20).std()

    # ---- 价格形态 ----
    prev_close = close.shift(1)
    out["amplitude"] = (high - low) / prev_close
    out["gap"] = open_ / prev_close - 1
    out["pct_change"] = df["pct_change"] if "pct_change" in df.columns else close.pct_change() * 100

    # ---- 类 Qlib 风格扩展因子 ----
    out["rsi_bias"] = rsi(close, 14) - 50                          # RSI 偏离度
    out["price_position"] = (close - low.rolling(20).min()) / \
        (high.rolling(20).max() - low.rolling(20).min()).replace(0, np.nan)  # 20日价格位置
    out["ret_60d"] = close.pct_change(60)                          # 中期动量
    out["reversal_10"] = close.pct_change(10)                      # 短期反转（10日）
    out["gap_mean"] = (open_ / prev_close - 1).rolling(10).mean()  # 跳空惯性
    out["intraday_vol"] = ((high - low) / prev_close).rolling(10).mean()  # 盘中波动均值
    out["vol_skew"] = close.pct_change().rolling(20).skew()        # 收益偏度
    out["volume_trend"] = volume.rolling(10).mean() / \
        volume.rolling(30).mean().replace(0, np.nan) - 1           # 量能趋势

    # ---- 标签 ----
    out["next_ret"] = close.shift(-1) / close - 1
    out["label"] = (out["next_ret"] > 0).astype(int)
    return out


def feature_columns(df: pd.DataFrame) -> list[str]:
    """特征列 = 全部列 - 保留列（trade_date/label/原始行情等）。"""
    return [c for c in df.columns if c not in RESERVED_COLS]
