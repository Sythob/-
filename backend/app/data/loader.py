"""行情加载：原始价 + 复权因子 → raw/qfq/hfq。

前复权采用「最新因子法」：qfq = raw × adj_factor / latest_adj_factor。
因为库存的是原始价，增量 upsert 永远不会产生历史断点。
"""
import pandas as pd

from app.data import database
from app.data.models import DailyBar, MinuteBar

_COLS = ["trade_date", "open", "high", "low", "close", "volume", "amount", "pct_change", "turnover_rate"]


def sanitize_factors(factors: pd.Series) -> pd.Series:
    """清洗复权因子序列的毛刺。

    新浪 hfq 因子在除息日会出现单日异常跳变（如 1.0 → 7.6 → 1.0），
    导致前复权价格虚涨数倍。规则：相对前值跳变超过 ±50% 视为毛刺，用前值修复
    （后复权因子应近似单调不减）。
    """
    out = factors.astype(float).copy()
    prev = None
    for i, v in enumerate(out.values):
        if pd.isna(v) or v <= 0:
            out.iloc[i] = prev if prev else 1.0
            continue
        if prev is not None and (v > prev * 1.5 or v < prev * 0.5):
            out.iloc[i] = prev  # 毛刺：沿用前值
        else:
            prev = v
    return out


def _rows_to_df(rows) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=_COLS)
    return pd.DataFrame(
        [
            {
                "trade_date": r.trade_date,
                "open": r.open, "high": r.high, "low": r.low, "close": r.close,
                "volume": r.volume, "amount": r.amount,
                "pct_change": r.pct_change, "turnover_rate": r.turnover_rate,
                "adj_factor": r.adj_factor,
            }
            for r in rows
        ]
    )


def load_daily(symbol: str, start: str = "", end: str = "", adjust: str = "qfq") -> pd.DataFrame:
    """读取日线。adjust: raw(不复权) / qfq(前复权，默认) / hfq(后复权)。"""
    with database.get_session() as session:
        query = session.query(DailyBar).filter(DailyBar.symbol == symbol)
        if start:
            query = query.filter(DailyBar.trade_date >= start)
        if end:
            query = query.filter(DailyBar.trade_date <= end)
        rows = query.order_by(DailyBar.trade_date).all()

    df = _rows_to_df(rows)
    if df.empty:
        return df[_COLS]

    # 过滤非交易日垃圾行（新浪源会在节假日插入错误价格的伪K线；日历为空时跳过过滤）
    from app.data.calendar import trade_date_set

    trade_dates = trade_date_set()
    if trade_dates:
        df = df[df["trade_date"].isin(trade_dates)].reset_index(drop=True)

    df["adj_factor"] = sanitize_factors(df["adj_factor"])  # 修复除息日毛刺
    latest_factor = df["adj_factor"].iloc[-1] or 1.0
    if adjust in ("qfq", "hfq"):
        factor = df["adj_factor"] / latest_factor if adjust == "qfq" else df["adj_factor"]
        for col in ("open", "high", "low", "close"):
            df[col] = (df[col] * factor).round(4)
    return df[_COLS]


def load_minute(symbol: str, interval: str = "60m", start: str = "", end: str = "") -> pd.DataFrame:
    """读取分钟/小时线。返回列: trade_date(=dt)/open/high/low/close/volume（引擎可直接使用）。"""
    with database.get_session() as session:
        query = session.query(MinuteBar).filter(
            MinuteBar.symbol == symbol, MinuteBar.interval == interval
        )
        if start:
            query = query.filter(MinuteBar.dt >= start)
        if end:
            query = query.filter(MinuteBar.dt <= end)
        rows = query.order_by(MinuteBar.dt).all()
    if not rows:
        return pd.DataFrame(columns=["trade_date", "open", "high", "low", "close", "volume"])
    return pd.DataFrame([
        {"trade_date": r.dt, "open": r.open, "high": r.high, "low": r.low,
         "close": r.close, "volume": r.volume}
        for r in rows
    ])


def get_latest_bar(symbol: str) -> dict | None:
    """该标的最近一根日线（增量同步判断/AI 报告数据用）。"""
    with database.get_session() as session:
        row = (
            session.query(DailyBar)
            .filter(DailyBar.symbol == symbol)
            .order_by(DailyBar.trade_date.desc())
            .first()
        )
        if row is None:
            return None
        return {
            "trade_date": row.trade_date,
            "close": row.close,
            "pct_change": row.pct_change,
            "amount": row.amount,
            "adj_factor": row.adj_factor,
        }


def get_latest_adj_factor(symbol: str) -> float:
    bar = get_latest_bar(symbol)
    return (bar or {}).get("adj_factor") or 1.0
