"""akshare 标准化纯函数测试（离线，不触网）。"""
import pandas as pd
import pytest

from app.data.akshare_source import (
    _normalize_hist,
    _normalize_sina_daily,
    _normalize_sina_spot,
    _normalize_spot,
    _normalize_tx_daily,
)
from app.data.base import BAR_COLUMNS


def _raw_hist() -> pd.DataFrame:
    return pd.DataFrame({
        "日期": ["2020-01-02", "2020-01-03"],
        "开盘": [10.0, 10.5], "最高": [11.0, 11.5], "最低": [9.5, 10.0], "收盘": [10.5, 11.0],
        "成交量": [1234, 5678],  # 手
        "成交额": [1.3e8, 6.2e8],
        "涨跌幅": [1.5, 4.76], "换手率": [0.8, None],
    })


def test_normalize_hist_units():
    out = _normalize_hist(_raw_hist(), None)
    assert list(out.columns) == BAR_COLUMNS
    assert out["volume"].tolist() == [123400.0, 567800.0]  # 手→股 ×100
    assert out["turnover_rate"].tolist() == [0.8, 0.0]     # 缺失值补 0
    assert out["adj_factor"].tolist() == [1.0, 1.0]        # 无因子时默认 1


def test_normalize_hist_factor_map():
    out = _normalize_hist(_raw_hist(), {"2020-01-02": 0.5, "2020-01-03": 0.25})
    assert out["adj_factor"].tolist() == [0.5, 0.25]


def test_normalize_hist_empty():
    out = _normalize_hist(pd.DataFrame(), None)
    assert out.empty
    assert list(out.columns) == BAR_COLUMNS


def test_normalize_spot_filter_and_units():
    raw = pd.DataFrame({
        "代码": ["600519", "000001"], "名称": ["贵州茅台", "平安银行"],
        "最新价": [1500.0, 12.0], "涨跌幅": [1.0, -1.0],
        "成交量": [30000, 500000], "成交额": [4.5e9, 6.0e8],
    })
    out = _normalize_spot(raw, symbols=["600519"])
    assert len(out) == 1
    assert out.iloc[0]["name"] == "贵州茅台"
    assert out.iloc[0]["volume"] == 3000000.0  # 手→股


def test_normalize_spot_no_filter():
    raw = pd.DataFrame({
        "代码": ["600519", "000001"], "名称": ["贵州茅台", "平安银行"],
        "最新价": [1500.0, 12.0], "涨跌幅": [1.0, -1.0],
        "成交量": [30000, 500000], "成交额": [4.5e9, 6.0e8],
    })
    out = _normalize_spot(raw, None)
    assert len(out) == 2


def test_normalize_sina_daily_units():
    """新浪日线：成交量已是「股」，不 ×100；涨跌幅由收盘价推算。"""
    raw = pd.DataFrame({
        "date": ["2020-01-02", "2020-01-03"],
        "open": [10.0, 10.5], "high": [11.0, 11.5], "low": [9.5, 10.0], "close": [10.5, 11.0],
        "volume": [1234567.0, 2345678.0],
        "amount": [1.3e8, 2.6e8], "turnover": [0.8, 1.2],
    })
    out = _normalize_sina_daily(raw, {"2020-01-02": 1.0, "2020-01-03": 1.0})
    assert list(out.columns) == BAR_COLUMNS
    assert out["volume"].tolist() == [1234567.0, 2345678.0]  # 不缩放
    assert out["pct_change"].iloc[1] == pytest.approx(round((11.0 / 10.5 - 1) * 100, 4))  # 实现按 4 位小数取整
    assert out["turnover_rate"].tolist() == [80.0, 120.0]  # 小数 ×100 统一为百分数
    assert out["adj_factor"].tolist() == [1.0, 1.0]


def test_normalize_sina_daily_factor_map():
    raw = pd.DataFrame({
        "date": ["2020-01-02"], "open": [10.0], "high": [11.0], "low": [9.0], "close": [10.5],
        "volume": [1e6], "amount": [1e7], "turnover": [1.0],
    })
    out = _normalize_sina_daily(raw, {"2020-01-02": 0.5})
    assert out["adj_factor"].tolist() == [0.5]


def test_normalize_tx_daily_units():
    """腾讯日线：volume 手→股 ×100、turnover 小数→百分数 ×100、列序兼容（close 在 high 前）。"""
    raw = pd.DataFrame({
        "date": ["2024-01-02", "2024-01-03"],
        "open": [10.0, 10.5], "close": [10.5, 11.0], "high": [10.6, 11.1], "low": [9.9, 10.4],
        "volume": [969082.0, 1111851.0],  # 手
        "turnover": [0.005, 0.0057],      # 小数
        "amount": [1.12e9, 1.28e9],
    })
    out = _normalize_tx_daily(raw, {"2024-01-02": 1.0, "2024-01-03": 1.0})
    assert list(out.columns) == BAR_COLUMNS
    assert out["volume"].tolist() == [96908200.0, 111185100.0]  # 手→股
    assert out["turnover_rate"].tolist() == [0.5, 0.57]  # 小数→百分数
    assert out["pct_change"].iloc[1] == pytest.approx(round((11.0 / 10.5 - 1) * 100, 4))
    assert out["adj_factor"].tolist() == [1.0, 1.0]


def test_normalize_tx_daily_missing_volume():
    """指数源无 volume/turnover 列时补 0。"""
    raw = pd.DataFrame({
        "date": ["2024-01-02"], "open": [3000.0], "close": [3010.0],
        "high": [3020.0], "low": [2990.0], "amount": [1e10],
    })
    out = _normalize_tx_daily(raw, {})
    assert out["volume"].tolist() == [0.0]
    assert out["turnover_rate"].tolist() == [0.0]
    assert out["close"].tolist() == [3010.0]


def test_normalize_sina_spot_strips_prefix():
    """新浪快照：代码带 sh/sz 前缀需剥离；成交量不缩放。"""
    raw = pd.DataFrame({
        "代码": ["sh600519", "sz000001"], "名称": ["贵州茅台", "平安银行"],
        "最新价": [1500.0, 12.0], "涨跌幅": [1.0, -1.0],
        "成交量": [30000.0, 500000.0],
        "成交额": [4.5e9, 6.0e8], "昨收": [1485.0, 12.12],
    })
    out = _normalize_sina_spot(raw, symbols=["600519"])
    assert len(out) == 1
    assert out.iloc[0]["symbol"] == "600519"  # 前缀已剥离
    assert out.iloc[0]["volume"] == 30000.0   # 不 ×100
    assert out.iloc[0]["name"] == "贵州茅台"
