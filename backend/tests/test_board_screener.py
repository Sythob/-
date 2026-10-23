"""打板候选池测试（离线）。"""
import pandas as pd

from app.data.board_screener import (
    calc_streak,
    limit_threshold,
    screen_board_candidates,
)
from app.data.sync import _upsert_daily_bars


def test_limit_threshold_by_board():
    assert limit_threshold("600519") == 9.5   # 主板
    assert limit_threshold("000001") == 9.5   # 主板
    assert limit_threshold("300750") == 19.5  # 创业板
    assert limit_threshold("688981") == 19.5  # 科创板
    assert limit_threshold("830799") == 29.5  # 北交所


def test_calc_streak():
    # 序列为时间升序（旧→新），连板从最新往回数
    assert calc_streak([1.0, 10.0, 10.0, 10.0], 9.5) == 3
    assert calc_streak([9.8, 10.0, 10.0], 9.5) == 3  # 含今日在内
    assert calc_streak([5.0, 10.0], 9.5) == 1
    assert calc_streak([1.0, 2.0], 9.5) == 0


def test_screen_filters_and_scores(tmp_db):
    """快照粗筛：涨幅≥7%、排除 ST；连板标注与评分排序。"""
    from app.data.models import Instrument

    with tmp_db() as session:
        session.add(Instrument(symbol="600001", name="首板股", type="stock"))
        session.add(Instrument(symbol="600002", name="二板股", type="stock"))
        session.commit()
    # 600001 今日涨停（首板），600002 昨日+今日涨停（2板）
    _upsert_daily_bars([
        {"symbol": "600001", "trade_date": "2024-01-05", "open": 10, "high": 10.5,
         "low": 9.8, "close": 10.4, "volume": 1e6, "amount": 6e8,
         "pct_change": 10.0, "turnover_rate": 8.0, "adj_factor": 1.0},
        {"symbol": "600002", "trade_date": "2024-01-04", "open": 10, "high": 10.5,
         "low": 9.8, "close": 10.4, "volume": 1e6, "amount": 6e8,
         "pct_change": 10.0, "turnover_rate": 8.0, "adj_factor": 1.0},
        {"symbol": "600002", "trade_date": "2024-01-05", "open": 10, "high": 10.5,
         "low": 9.8, "close": 10.4, "volume": 1e6, "amount": 6e8,
         "pct_change": 10.0, "turnover_rate": 8.0, "adj_factor": 1.0},
    ])
    quotes = pd.DataFrame([
        {"symbol": "600001", "name": "首板股", "pct_change": 10.0, "amount": 6e8, "last_price": 10.4},
        {"symbol": "600002", "name": "二板股", "pct_change": 10.0, "amount": 6e8, "last_price": 10.4},
        {"symbol": "600003", "name": "*ST烂票", "pct_change": 10.0, "amount": 6e8, "last_price": 3.0},
        {"symbol": "600004", "name": "普通股", "pct_change": 3.0, "amount": 6e8, "last_price": 10.0},
    ])
    # as_of=测试数据里的"今日"（2024-01-05）：连板历史取其之前的日线
    result = screen_board_candidates(quotes, top_n=10, as_of="2024-01-05")
    symbols = {r["symbol"] for r in result}
    assert "600001" in symbols and "600002" in symbols
    assert "600003" not in symbols  # ST 排除
    assert "600004" not in symbols  # 涨幅不足
    first = {r["symbol"]: r for r in result}["600001"]
    second = {r["symbol"]: r for r in result}["600002"]
    assert first["streak"] == 1
    assert second["streak"] == 2
    assert first["score"] > second["score"]  # 首板评分高于 2 板
    assert first["risk"] == "正常"
