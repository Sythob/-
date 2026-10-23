"""复权铁律测试：最新因子法 qfq 手算对拍 + 毛刺清洗。"""
import pandas as pd

from app.data import loader
from app.data.sync import _upsert_daily_bars


def test_sanitize_factors_spike():
    """除息日毛刺（1.0→7.6→1.0）应被修复为连续序列。"""
    raw = pd.Series([1.0, 1.0, 7.6, 1.0, 1.0])
    out = loader.sanitize_factors(raw)
    assert out.tolist() == [1.0, 1.0, 1.0, 1.0, 1.0]


def test_sanitize_factors_keeps_legit_dividend():
    """正常的分红调整（小幅跳变 <50%）保留。"""
    raw = pd.Series([1.0, 1.0, 1.02, 1.02, 1.02])
    out = loader.sanitize_factors(raw)
    assert out.tolist() == [1.0, 1.0, 1.02, 1.02, 1.02]


def test_sanitize_factors_handles_bad_values():
    raw = pd.Series([0.0, 1.0, 1.0])
    out = loader.sanitize_factors(raw)
    assert out.tolist() == [1.0, 1.0, 1.0]


def test_filter_non_trading_days(tmp_db):
    """节假日垃圾行被交易日历过滤（日历为空时不过滤）。"""
    from app.data.sync import _upsert_calendar, _upsert_daily_bars

    # 2024-01-03 为交易日，2024-01-31 假设为非交易日（不在日历中）
    _upsert_calendar([{"trade_date": "2024-01-03", "is_open": 1}])
    _upsert_daily_bars([
        {"symbol": "600519", "trade_date": "2024-01-03", "open": 100, "high": 102,
         "low": 99, "close": 101, "volume": 1e6, "amount": 1e8,
         "pct_change": 1.0, "turnover_rate": 1.0, "adj_factor": 1.0},
        {"symbol": "600519", "trade_date": "2024-01-31", "open": 50, "high": 52,
         "low": 48, "close": 51, "volume": 1e6, "amount": 1e8,
         "pct_change": 0.0, "turnover_rate": 1.0, "adj_factor": 1.0},
    ])
    df = loader.load_daily("600519", adjust="raw")
    assert len(df) == 1
    assert df["trade_date"].tolist() == ["2024-01-03"]  # 非交易日垃圾行被剔除


def _seed(session):
    # 模拟 10 送 10 除权：第二日 adj_factor 减半
    bars = [
        {"symbol": "600519", "trade_date": "2020-01-02", "open": 10.0, "high": 11.0,
         "low": 9.0, "close": 10.0, "volume": 1000, "amount": 10000,
         "pct_change": 0, "turnover_rate": 1, "adj_factor": 1.0},
        {"symbol": "600519", "trade_date": "2020-01-03", "open": 5.0, "high": 6.0,
         "low": 4.0, "close": 5.0, "volume": 1000, "amount": 5000,
         "pct_change": 0, "turnover_rate": 1, "adj_factor": 0.5},
    ]
    _upsert_daily_bars(bars)


def test_qfq_latest_factor(tmp_db):
    _seed(tmp_db)
    df = loader.load_daily("600519", adjust="qfq")
    # 最新因子 0.5：qfq = raw × factor / 0.5
    assert df.iloc[0]["close"] == 20.0  # 10 × 1.0 / 0.5
    assert df.iloc[1]["close"] == 5.0   # 5 × 0.5 / 0.5


def test_raw_untouched(tmp_db):
    _seed(tmp_db)
    df = loader.load_daily("600519", adjust="raw")
    assert df.iloc[0]["close"] == 10.0
    assert df.iloc[1]["close"] == 5.0


def test_hfq(tmp_db):
    _seed(tmp_db)
    df = loader.load_daily("600519", adjust="hfq")
    assert df.iloc[0]["close"] == 10.0
    assert df.iloc[1]["close"] == 2.5  # 5 × 0.5


def test_date_filter(tmp_db):
    _seed(tmp_db)
    df = loader.load_daily("600519", start="2020-01-03")
    assert len(df) == 1
    assert df.iloc[0]["trade_date"] == "2020-01-03"


def test_empty_symbol(tmp_db):
    df = loader.load_daily("999999", adjust="qfq")
    assert df.empty
