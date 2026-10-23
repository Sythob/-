"""技术指标手算对拍。"""
import numpy as np
import pandas as pd
import pytest

from app.ml.indicators import atr, boll, cci, ema, kdj, ma, macd, roc, rsi, volume_ratio, wr


def test_ma():
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    out = ma(s, 2)
    assert np.isnan(out.iloc[0])
    assert out.iloc[1] == pytest.approx(1.5)
    assert out.iloc[3] == pytest.approx(3.5)


def test_ema_manual():
    """span=2 → α=2/3，逐点手算。"""
    s = pd.Series([1.0, 2.0, 3.0, 2.0, 1.0])
    out = ema(s, 2)
    expected = [1.0, 5 / 3, 23 / 9, 59 / 27, 113 / 81]
    for i, e in enumerate(expected):
        assert out.iloc[i] == pytest.approx(e)


def test_rsi_extremes():
    up = rsi(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]), 3)
    assert (up.fillna(100) == 100).all()  # 全程上涨 → RSI=100
    down = rsi(pd.Series([5.0, 4.0, 3.0, 2.0, 1.0]), 3)
    assert (down.dropna() == 0).all()  # 全程下跌 → RSI=0


def test_macd_flat_zero():
    m = macd(pd.Series([10.0] * 20))
    assert (m["dif"].dropna() == 0).all()
    assert (m["hist"].dropna() == 0).all()


def test_kdj_flat():
    n = 20
    flat = pd.Series([10.0] * n)
    k = kdj(flat, flat, flat)
    assert (k["k"].dropna() == 50).all()
    assert (k["d"].dropna() == 50).all()
    assert (k["j"].dropna() == 50).all()


def test_boll_manual():
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    b = boll(s, 2, k=2.0)
    # n=2: mid=[NaN,1.5,2.5,3.5] std(ddof=0)=[NaN,0.5,0.5,0.5]
    assert b["mid"].iloc[2] == pytest.approx(2.5)
    assert b["upper"].iloc[2] == pytest.approx(3.5)
    assert b["lower"].iloc[2] == pytest.approx(1.5)


def test_atr_constant_range():
    n = 20
    close = pd.Series(range(1, n + 1), dtype=float)
    high = close + 1.0
    low = close - 1.0
    out = atr(high, low, close, 14)
    assert (out.dropna() == 2.0).all()  # 恒定 2 元波幅


def test_roc():
    s = pd.Series([1.0, 2.0, 4.0, 8.0])
    out = roc(s, 2)
    assert out.iloc[2] == pytest.approx(300.0)  # 4/1-1
    assert out.iloc[3] == pytest.approx(300.0)  # 8/2-1


def test_wr():
    n = 3
    high = pd.Series([10.0] * n)
    low = pd.Series([0.0] * n)
    close = pd.Series([2.0, 2.0, 2.0])
    out = wr(high, low, close, 2)
    assert out.iloc[1] == pytest.approx(80.0)  # (10-2)/10*100


def test_cci_flat_no_crash():
    n = 20
    flat = pd.Series([10.0] * n)
    out = cci(flat, flat, flat, 14)
    assert out.isna().all()  # 无波动 → 分母为 0 → NaN（不崩溃）


def test_volume_ratio():
    v = pd.Series([100.0, 100.0, 100.0, 300.0, 100.0])
    out = volume_ratio(v, 2)
    # 第 3 根（index 3）：当日 300 / 前两根均值 100 = 3.0
    assert out.iloc[3] == pytest.approx(3.0)
