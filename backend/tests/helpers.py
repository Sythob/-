"""引擎测试辅助：合成行情 DataFrame。"""
import pandas as pd


def make_daily(symbol: str = "600519", rows: list | None = None) -> pd.DataFrame:
    """rows: [(date, open, high, low, close, pct_change), ...]"""
    if rows is None:
        rows = [
            ("2020-01-02", 10.0, 10.5, 9.8, 10.2, 2.0),
            ("2020-01-03", 10.2, 10.8, 10.0, 10.5, 2.94),
            ("2020-01-06", 10.5, 11.0, 10.3, 10.8, 2.86),
            ("2020-01-07", 10.8, 10.9, 10.0, 10.1, -6.48),
        ]
    return pd.DataFrame(
        [
            {"trade_date": d, "open": o, "high": h, "low": l, "close": c,
             "volume": 1000000, "amount": 1e7, "pct_change": p}
            for d, o, h, l, c, p in rows
        ]
    )
