"""自研技术指标库：纯 pandas 向量化实现（约 15 个常用指标）。

不依赖已停更的 pandas-ta；每个指标配手工对拍测试（tests/test_indicators.py）。
输入均为 pandas.Series / DataFrame，输出对齐原索引。
"""
import numpy as np
import pandas as pd


def ma(close: pd.Series, n: int) -> pd.Series:
    """简单移动平均。"""
    return close.rolling(n).mean()


def ema(close: pd.Series, n: int) -> pd.Series:
    """指数移动平均（span 制）。"""
    return close.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder RSI：无亏损时=100，无盈利时=0，预热期（首日）为 NaN。"""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # ignore_na=True：跳过首个 NaN，避免 NaN 传染整条序列
    avg_gain = gain.ewm(alpha=1 / n, adjust=False, ignore_na=True).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False, ignore_na=True).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(avg_loss != 0, 100.0)  # 无亏损 → RSI=100（避免 NaN）


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """MACD：返回 dif/dea/hist 三列 DataFrame。"""
    dif = ema(close, fast) - ema(close, slow)
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return pd.DataFrame({"dif": dif, "dea": dea, "hist": hist}, index=close.index)


def kdj(high: pd.Series, low: pd.Series, close: pd.Series,
        n: int = 9, m1: int = 3, m2: int = 3) -> pd.DataFrame:
    """KDJ：返回 k/d/j 三列 DataFrame。"""
    low_n = low.rolling(n, min_periods=1).min()
    high_n = high.rolling(n, min_periods=1).max()
    rsv = (close - low_n) / (high_n - low_n).replace(0, np.nan) * 100
    rsv = rsv.fillna(50.0)
    k = rsv.ewm(alpha=1 / m1, adjust=False).mean()
    d = k.ewm(alpha=1 / m2, adjust=False).mean()
    j = 3 * k - 2 * d
    return pd.DataFrame({"k": k, "d": d, "j": j}, index=close.index)


def boll(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    """布林带：返回 upper/mid/lower 三列 DataFrame。"""
    mid = close.rolling(n).mean()
    std = close.rolling(n).std(ddof=0)
    return pd.DataFrame({"upper": mid + k * std, "mid": mid, "lower": mid - k * std},
                        index=close.index)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder ATR（平均真实波幅）。"""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def roc(close: pd.Series, n: int = 12) -> pd.Series:
    """变动率（%）"""
    return (close / close.shift(n) - 1) * 100


def cci(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """顺势指标。"""
    tp = (high + low + close) / 3
    ma_tp = tp.rolling(n).mean()
    md = (tp - ma_tp).abs().rolling(n).mean()
    return (tp - ma_tp) / (0.015 * md.replace(0, np.nan))


def wr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """威廉指标（%R，0~100，80 以上超卖）。"""
    high_n = high.rolling(n).max()
    low_n = low.rolling(n).min()
    return (high_n - close) / (high_n - low_n).replace(0, np.nan) * 100


def volume_ratio(volume: pd.Series, n: int = 5) -> pd.Series:
    """量比 = 当日成交量 / 前 n 日均量。"""
    return volume / volume.shift(1).rolling(n).mean()


def volume_ma_ratio(volume: pd.Series, short: int = 5, long: int = 10) -> pd.Series:
    """量能均线比 = 短均量 / 长均量。"""
    return volume.rolling(short).mean() / volume.rolling(long).mean()
