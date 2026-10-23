"""upsert 幂等性与日历工具测试。"""
from app.data import calendar
from app.data.models import DailyBar
from app.data.sync import _upsert_calendar, _upsert_daily_bars


def _row(**overrides):
    row = {"symbol": "600519", "trade_date": "2020-01-02", "open": 10, "high": 11,
           "low": 9, "close": 10, "volume": 1000, "amount": 10000,
           "pct_change": 0, "turnover_rate": 1, "adj_factor": 1.0}
    row.update(overrides)
    return row


def test_upsert_idempotent(tmp_db):
    _upsert_daily_bars([_row()])
    _upsert_daily_bars([_row(close=10.5)])  # 同键后值覆盖
    with tmp_db() as session:
        rows = session.query(DailyBar).all()
        assert len(rows) == 1
        assert rows[0].close == 10.5


def test_upsert_multiple_symbols(tmp_db):
    _upsert_daily_bars([_row(), _row(symbol="000001", trade_date="2020-01-02")])
    with tmp_db() as session:
        assert session.query(DailyBar).count() == 2


def test_upsert_large_batch_chunked(tmp_db):
    """全量历史同步（数千行）必须分块写入，否则触发 SQLite 变量数上限。"""
    import pandas as pd

    dates = pd.date_range("2015-01-05", periods=2500, freq="B").strftime("%Y-%m-%d")
    rows = [_row(trade_date=d, close=10.0 + i * 0.001) for i, d in enumerate(dates)]
    n = _upsert_daily_bars(rows)
    assert n == 2500
    with tmp_db() as session:
        assert session.query(DailyBar).count() == 2500


def test_calendar_weekday_fallback(tmp_db):
    """日历表为空时退化为工作日判断。"""
    assert calendar.is_trade_day("2024-01-06") is False  # 周六
    assert calendar.is_trade_day("2024-01-05") is True   # 周五


def test_calendar_from_db(tmp_db):
    _upsert_calendar([{"trade_date": "2024-01-06", "is_open": 1}])  # 假设周六开市
    assert calendar.is_trade_day("2024-01-06") is True
    assert calendar.latest_trade_date() == "2024-01-06"
