"""Alpha158 因子库：Qlib 官方 Alpha158 公式的纯 pandas/numpy 移植。

公式来源：microsoft/qlib `qlib/contrib/data/loader.py` Alpha158DL.get_feature_config
（KBAR 9 个 + 价格/成交量 5 个 + 29 类滚动算子 × 5/10/20/30/60 窗口 = 159 列）。

铁律（与 features.py 一致）：t 时刻因子只使用 ≤t 的数据——KBAR/价格/成交量因子取当日，
滚动算子全部回看窗口，绝无未来值。tests/test_features_no_leakage.py 用截断重算方式校验。

性能说明：Rank/IMAX/IMIN 用 rolling.apply（numpy 内层），全市场训练时约占整体特征
计算的大部分时间，但仍在可接受范围（约 5000 股 × 800 根 ≈ 数分钟）。
"""
import numpy as np
import pandas as pd

_EPS = 1e-12
_WINDOWS = (5, 10, 20, 30, 60)


def _rolling_dot(y: np.ndarray, x: np.ndarray) -> np.ndarray:
    """滚动窗口点积：out[i] = Σ_{j=i-d+1}^{i} y[j] * x[j-(i-d+1)]（i ≥ d-1 有效）。

    等价于 convolve(y, reversed(x)) 的前 n 项，比 rolling.apply 快两个数量级。
    注意：卷积的因果方向——conv[i] 只涉及 y[i-d+1..i]（过去窗口），i < d-1 为
    不完整部分和（配合 rolling sum 的 NaN 一起被 dropna 剔除）。
    """
    conv = np.convolve(y, x[::-1])
    return conv[: len(y)]


def _slope_rsqr_resi(s: pd.Series, d: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    """滚动窗口内 y 对时间轴 (0..d-1) 的线性回归：斜率 / R² / 最新残差（向量化）。"""
    x = np.arange(d, dtype=float)
    sx = float(x.sum())
    sxx = float((x * x).sum())
    denom = d * sxx - sx * sx
    y = s.to_numpy(dtype=float)
    sum_y = s.rolling(d).sum()
    sum_xy = pd.Series(_rolling_dot(y, x), index=s.index)
    slope = (d * sum_xy - sx * sum_y) / denom
    sum_y2 = (s * s).rolling(d).sum()
    ss_tot = sum_y2 - sum_y * sum_y / d  # 总离差平方和
    ss_reg = slope * slope * denom / d  # 回归平方和
    rsqr = ss_reg / ss_tot.replace(0, np.nan)
    intercept = (sum_y - slope * sx) / d
    resi = s - (slope * (d - 1) + intercept)
    return slope, rsqr, resi


def _rank_last(w: np.ndarray) -> float:
    """Qlib Rank 定义：当前值在窗口中的分位（严格小于比例，最小值 rank=0）。"""
    return float(np.mean(w < w[-1]))


def _idxmax(w: np.ndarray) -> float:
    return float(np.argmax(w))


def _idxmin(w: np.ndarray) -> float:
    return float(np.argmin(w))


def build_alpha158(df: pd.DataFrame) -> pd.DataFrame:
    """输入日线（open/high/low/close/volume/amount，trade_date 升序）→ 159 列因子。"""
    cols: dict[str, pd.Series] = {}
    open_, high, low, close = df["open"], df["high"], df["low"], df["close"]
    volume = df["volume"].replace(0, np.nan)
    vwap = df["amount"] / volume
    vwap = vwap.fillna((high + low + close) / 3)  # 无量（停牌）→ 典型价近似

    # ---- KBAR（9 个，分母 $open / 高低价差）----
    cols["KMID"] = (close - open_) / open_
    cols["KLEN"] = (high - low) / open_
    cols["KMID2"] = (close - open_) / (high - low + _EPS)
    hi_oc = np.maximum(open_, close)
    lo_oc = np.minimum(open_, close)
    cols["KUP"] = (high - hi_oc) / open_
    cols["KUP2"] = (high - hi_oc) / (high - low + _EPS)
    cols["KLOW"] = (lo_oc - low) / open_
    cols["KLOW2"] = (lo_oc - low) / (high - low + _EPS)
    cols["KSFT"] = (2 * close - high - low) / open_
    cols["KSFT2"] = (2 * close - high - low) / (high - low + _EPS)

    # ---- 价格/成交量（窗口 0）----
    cols["OPEN0"] = open_ / close
    cols["HIGH0"] = high / close
    cols["LOW0"] = low / close
    cols["VWAP0"] = vwap / close
    cols["VOLUME0"] = volume.fillna(0) / (volume.fillna(0) + _EPS)

    prev_close = close.shift(1)
    ret1 = close / prev_close - 1
    log_vol = np.log(volume.fillna(0) + 1)
    vol_ret = volume.fillna(0) / volume.fillna(0).shift(1).replace(0, np.nan)
    log_vol_ret = np.log(vol_ret.fillna(0) + 1)

    def _add(name: str, s: pd.Series) -> None:
        cols[name] = s

    for d in _WINDOWS:
        # ROC：d 日前收盘 / 当日收盘
        _add(f"ROC{d}", close.shift(d) / close)
        # MA / STD
        _add(f"MA{d}", close.rolling(d).mean() / close)
        _add(f"STD{d}", close.rolling(d).std() / close)
        # BETA（斜率）/ RSQR / RESI
        slope, rsqr, resi = _slope_rsqr_resi(close, d)
        _add(f"BETA{d}", slope / close)
        _add(f"RSQR{d}", rsqr)
        _add(f"RESI{d}", resi / close)
        # MAX / MIN / 分位数
        _add(f"MAX{d}", high.rolling(d).max() / close)
        _add(f"MIN{d}", low.rolling(d).min() / close)
        _add(f"QTLU{d}", close.rolling(d).quantile(0.8) / close)
        _add(f"QTLD{d}", close.rolling(d).quantile(0.2) / close)
        # RANK：当前收盘在窗口中的分位
        _add(f"RANK{d}", close.rolling(d).apply(_rank_last, raw=True))
        # RSV：价格位置
        _add(f"RSV{d}", (close - low.rolling(d).min())
             / (high.rolling(d).max() - low.rolling(d).min() + _EPS))
        # IMAX / IMIN / IMXD：距窗口内最高/最低价的天数（Aroon 类）
        imax = high.rolling(d).apply(_idxmax, raw=True)
        imin = low.rolling(d).apply(_idxmin, raw=True)
        _add(f"IMAX{d}", imax / d)
        _add(f"IMIN{d}", imin / d)
        _add(f"IMXD{d}", (imax - imin) / d)
        # CORR：收盘与对数成交量的相关性
        _add(f"CORR{d}", close.rolling(d).corr(log_vol))
        # CORD：价格变化率与成交量变化率的相关性
        _add(f"CORD{d}", ret1.rolling(d).corr(log_vol_ret))
        # CNTP / CNTN / CNTD：窗口内上涨/下跌天数占比
        up = (close > prev_close).astype(float)
        down = (close < prev_close).astype(float)
        _add(f"CNTP{d}", up.rolling(d).mean())
        _add(f"CNTN{d}", down.rolling(d).mean())
        _add(f"CNTD{d}", (up - down).rolling(d).mean())
        # SUMP / SUMN / SUMD：涨跌贡献比（RSI 类，Qlib 原式用价格差）
        pdiff = close.diff()
        gain = pdiff.clip(lower=0).rolling(d).sum()
        loss = (-pdiff.clip(upper=0)).rolling(d).sum()
        total = pdiff.abs().rolling(d).sum() + _EPS
        _add(f"SUMP{d}", gain / total)
        _add(f"SUMN{d}", loss / total)
        _add(f"SUMD{d}", (gain - loss) / total)
        # VMA / VSTD：成交量均线/波动
        vol_f = volume.fillna(0)
        _add(f"VMA{d}", vol_f.rolling(d).mean() / (vol_f + _EPS))
        _add(f"VSTD{d}", vol_f.rolling(d).std() / (vol_f + _EPS))
        # WVMA：成交量加权的价格变化波动
        abs_ret_vol = ret1.abs() * vol_f
        _add(f"WVMA{d}", abs_ret_vol.rolling(d).std()
             / (abs_ret_vol.rolling(d).mean() + _EPS))
        # VSUMP / VSUMN / VSUMD：成交量增减贡献比（RSI 类）
        vdiff = vol_f.diff()
        vgain = vdiff.clip(lower=0).rolling(d).sum()
        vloss = (-vdiff.clip(upper=0)).rolling(d).sum()
        vtotal = vdiff.abs().rolling(d).sum() + _EPS
        _add(f"VSUMP{d}", vgain / vtotal)
        _add(f"VSUMN{d}", vloss / vtotal)
        _add(f"VSUMD{d}", (vgain - vloss) / vtotal)

    return pd.concat(cols, axis=1)
