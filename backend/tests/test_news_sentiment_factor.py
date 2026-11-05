"""新闻情绪因子测试：聚合/合并逻辑（离线，不加载 BERT 模型）。"""
import pandas as pd

from app.ml.news_sentiment_factor import (
    SENTIMENT_COLS,
    merge_sentiment_features,
    symbol_daily_sentiment,
)


def test_symbol_daily_sentiment_aggregates():
    rows = [
        {"symbol": "600519", "pub_time": "2026-10-20 10:00:00", "score": 0.8},
        {"symbol": "600519", "pub_time": "2026-10-20 14:00:00", "score": 0.2},
        {"symbol": "600519", "pub_time": "2026-10-21 09:30:00", "score": -0.4},
        {"symbol": "000001", "pub_time": "2026-10-20 11:00:00", "score": 0.6},
    ]
    df = symbol_daily_sentiment(rows)
    assert set(SENTIMENT_COLS) <= set(df.columns)
    m = df[df["symbol"] == "600519"].set_index("trade_date")
    assert m.loc["2026-10-20", "news_score_mean"] == 0.5
    assert m.loc["2026-10-20", "news_count"] == 2
    # 3 日滚动均值（min_periods=1）
    assert m.loc["2026-10-20", "news_score_3d"] == 0.5
    assert abs(m.loc["2026-10-21", "news_score_3d"] - (0.5 - 0.4) / 2) < 1e-9


def test_merge_fills_zero_when_no_news():
    feats = pd.DataFrame({
        "trade_date": ["2026-10-20", "2026-10-21", "2026-10-22"],
        "close": [100.0, 101.0, 102.0],
    })
    sent = symbol_daily_sentiment([
        {"symbol": "600519", "pub_time": "2026-10-20 10:00:00", "score": 0.8},
    ])
    out = merge_sentiment_features(feats, "600519", sent)
    assert out.loc[0, "news_score_mean"] == 0.8  # 有新闻
    assert out.loc[1, "news_score_mean"] == 0.0  # 无新闻 → 0（中性）
    assert out.loc[1, "news_count"] == 0
    # 原列保留
    assert out.loc[2, "close"] == 102.0
    # 无关标的 → 全 0
    out2 = merge_sentiment_features(feats, "000001", sent)
    assert (out2["news_score_mean"] == 0.0).all()
