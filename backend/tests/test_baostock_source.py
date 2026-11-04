"""baostock 兜底数据源测试：登录/查询 mock，验证日线与 60 分钟线规范化。"""
import pandas as pd
import pytest

from app.data.baostock_source import get_daily_bars, get_hour_bars


class _FakeRs:
    error_code = "0"
    error_msg = ""

    def __init__(self, rows):
        self._rows = iter(rows)

    def next(self):
        try:
            self._row = next(self._rows)
            return True
        except StopIteration:
            return False

    def get_row_data(self):
        return self._row


def _mock_bs(monkeypatch, rows):
    import types

    mod = types.ModuleType("baostock")
    mod.login = lambda: types.SimpleNamespace(error_code="0", error_msg="")
    mod.query_history_k_data_plus = lambda *a, **k: _FakeRs(rows)
    monkeypatch.setitem(__import__("sys").modules, "baostock", mod)
    import app.data.baostock_source as bs_src

    bs_src._logged_in = True  # 跳过登录


def test_daily_bars_normalize(monkeypatch):
    _mock_bs(monkeypatch, [
        ["2024-01-02", "10.0", "10.5", "9.8", "10.4", "1000000", "10400000", "2.5"],
        ["2024-01-03", "10.4", "10.6", "10.2", "10.5", "1200000", "12600000", "3.0"],
    ])
    df = get_daily_bars("600519", start="2024-01-01")
    assert list(df.columns) == ["trade_date", "open", "high", "low", "close",
                                "volume", "amount", "pct_change", "turnover_rate", "adj_factor"]
    assert df["trade_date"].tolist() == ["2024-01-02", "2024-01-03"]
    assert df["close"].tolist() == [10.4, 10.5]
    assert df["turnover_rate"].tolist() == [2.5, 3.0]
    assert df["pct_change"].iloc[1] == pytest.approx(0.96, abs=0.1)  # 10.5/10.4-1
    assert (df["adj_factor"] == 1.0).all()


def test_hour_bars_normalize(monkeypatch):
    _mock_bs(monkeypatch, [
        ["2024-01-02", "10:00", "10.0", "10.2", "9.9", "10.1", "5000", "50500"],
        ["2024-01-02", "11:00", "10.1", "10.3", "10.0", "10.2", "6000", "61200"],
    ])
    df = get_hour_bars("600519")
    assert df["dt"].tolist() == ["2024-01-02 10:00:00", "2024-01-02 11:00:00"]
    assert df["close"].tolist() == [10.1, 10.2]


def test_empty_result_returns_columns(monkeypatch):
    _mock_bs(monkeypatch, [])
    df = get_daily_bars("600519")
    assert df.empty
    assert "trade_date" in df.columns
