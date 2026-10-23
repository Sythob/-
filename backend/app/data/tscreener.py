"""做T 候选池筛选器：从本地日线库精确筛选适合日内回转的标的。

筛选标准（可参数化）：
- 流动性：近 5 日均成交额 ≥ 5 亿元
- 波动：近 5 日均振幅 ≥ 3%（做T 的利润空间）
- 换手率：近 5 日均值 3%~10%（筹码稳定）
- 趋势：收盘价在 20 日均线上方且均线向上（底仓安全底线）
- 排除：ST 股、指数、换手率数据缺失的标的（ETF 新浪源无换手率，v1 不纳入）

结果按综合评分排序（振幅 40% + 流动性 30% + 趋势 30%），TTL 缓存 10 分钟。
"""
import math
import threading
import time

import numpy as np
import pandas as pd

from app.data import database
from app.data.models import DailyBar, Instrument, TscreenWatch
from app.utils.logger import get_logger

logger = get_logger("data.tscreener")

_cache: tuple[float, list[dict]] = (0.0, [])
_lock = threading.Lock()


def screen_tsymbols(
    min_amount: float = 5e8,
    min_amplitude: float = 0.03,
    turnover_min: float = 3.0,   # 换手率统一为百分数（3 = 3%）
    turnover_max: float = 10.0,
    trend_window: int = 20,
    limit: int = 20,
    trend_strict: bool = False,  # 弱势市场门控：额外要求 MA60 向上
) -> list[dict]:
    """返回候选列表 [{symbol, name, amount_avg, amplitude_avg, turnover_avg, ret_20, score}]。"""
    global _cache
    with _lock:
        if _cache[1] and time.monotonic() - _cache[0] < 600:
            return _cache[1][:limit]

    with database.get_session() as session:
        symbols = [r[0] for r in session.query(DailyBar.symbol).distinct().all()]
        instruments = {r.symbol: r for r in session.query(Instrument).all()}

    candidates = []
    required = 60 if trend_strict else trend_window
    for symbol in symbols:
        inst = instruments.get(symbol)
        if inst is None or inst.type == "index" or inst.is_st:
            continue
        df = _load_recent(symbol, n=required + 5)
        if df.empty or len(df) < required:
            continue
        result = _evaluate(df, symbol, inst.name,
                           min_amount, min_amplitude, turnover_min, turnover_max,
                           trend_window, trend_strict=trend_strict)
        if result:
            candidates.append(result)

    candidates.sort(key=lambda x: x["score"], reverse=True)
    with _lock:
        _cache = (time.monotonic(), candidates)
    logger.info("做T候选池筛选完成：%d 个候选（共扫描 %d 只，严格趋势=%s）",
                len(candidates), len(symbols), trend_strict)
    return candidates[:limit]


def coarse_screen_quotes(quotes: pd.DataFrame, top_n: int = 50,
                         min_amount: float = 3e8,
                         min_amplitude: float = 0.03) -> list[dict]:
    """全市场快照粗筛：今日振幅/成交额合格、非 ST、非涨跌停（±9.5% 近似）。

    快照含 last_price/pct_change/open/high/low/pre_close/volume/amount/name 列。
    返回按综合评分排序的前 top_n 个候选（含 symbol/name）。
    """
    if quotes is None or quotes.empty:
        return []
    out = quotes.copy()
    out["amplitude_today"] = (pd.to_numeric(out["high"], errors="coerce")
                              - pd.to_numeric(out["low"], errors="coerce")) \
        / pd.to_numeric(out["pre_close"], errors="coerce").replace(0, 1)
    pct = pd.to_numeric(out["pct_change"], errors="coerce")
    amount = pd.to_numeric(out["amount"], errors="coerce")
    name = out["name"].astype(str)
    mask = (
        (amount >= min_amount)
        & (out["amplitude_today"] >= min_amplitude)
        & (pct.abs() < 9.5)
        & (~name.str.contains("ST", case=False, na=False))
    )
    out = out[mask].copy()
    if out.empty:
        return []
    out["score"] = out["amplitude_today"] * 0.6 + np.log10(amount) * 0.4
    out = out.sort_values("score", ascending=False).head(top_n)
    return [
        {"symbol": str(r["symbol"]), "name": str(r["name"]),
         "amplitude_today": round(float(r["amplitude_today"]) * 100, 2),
         "amount_today": float(r["amount"]), "pct_change": float(r["pct_change"])}
        for _, r in out.iterrows()
    ]


def _save_watch_zone(rows: list[dict]) -> int:
    """粗筛观察区落库（按 symbol upsert，同日重复刷新覆盖）。"""
    if not rows:
        return 0
    from datetime import datetime

    today = datetime.now().strftime("%Y-%m-%d")
    with database.get_session() as session:
        for r in rows:
            existing = session.get(TscreenWatch, r["symbol"])
            if existing is None:
                existing = TscreenWatch(symbol=r["symbol"])
                session.add(existing)
            existing.name = r["name"]
            existing.amplitude_today = r["amplitude_today"]
            existing.amount_today = r["amount_today"]
            existing.pct_change = r["pct_change"]
            existing.score = r.get("score", 0.0)
            existing.date = today
        session.commit()
    return len(rows)


def load_watch_zone() -> list[dict]:
    """观察区列表（最近一次全市场扫描的粗筛结果，按评分排序）。"""
    with database.get_session() as session:
        rows = session.query(TscreenWatch).order_by(TscreenWatch.score.desc()).all()
        return [
            {"symbol": r.symbol, "name": r.name,
             "amplitude_today": round(r.amplitude_today, 2),
             "amount_today": r.amount_today,
             "pct_change": r.pct_change, "date": r.date}
            for r in rows
        ]


def refresh_market_tscreen(top_n: int = 50) -> dict:
    """全市场做T候选刷新：快照粗筛 → 补主表 → 定向同步日线 → 本地精筛。

    约 3~6 分钟（网络绑定），由 API 后台任务或每日收盘调度触发。
    """
    from app.data.base import get_source
    from app.data.sync import _upsert_instruments, sync_watchlist_bars
    from app.data.symbols import board_of, exchange_of

    quotes = get_source().get_realtime_quotes(None)  # 全市场快照（含新浪回退，有 TTL 缓存）
    coarse = coarse_screen_quotes(quotes, top_n=top_n)
    if not coarse:
        return {"coarse": 0, "synced": {}, "candidates": []}

    # 补证券主表（快照自带名称/涨跌幅，主表信息用于精筛的 ST/类型过滤）
    instrument_rows = [
        {"symbol": c["symbol"], "name": c["name"], "exchange": exchange_of(c["symbol"]),
         "type": "stock", "board": board_of(c["symbol"]), "status": "active",
         "list_date": "", "is_st": 0}
        for c in coarse
    ]
    _upsert_instruments(instrument_rows)

    # 定向同步候选的日线（已有且为最新的会跳过）
    stats = sync_watchlist_bars([c["symbol"] for c in coarse])

    _save_watch_zone(coarse)
    candidates = screen_tsymbols(limit=50)
    return {"coarse": len(coarse), "synced": stats,
            "candidates": candidates, "watch_zone": coarse}


def _load_recent(symbol: str, n: int = 25) -> pd.DataFrame:
    with database.get_session() as session:
        rows = (
            session.query(DailyBar)
            .filter(DailyBar.symbol == symbol)
            .order_by(DailyBar.trade_date.desc())
            .limit(n).all()
        )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([
        {"trade_date": r.trade_date, "open": r.open, "high": r.high, "low": r.low,
         "close": r.close, "volume": r.volume, "amount": r.amount,
         "turnover_rate": r.turnover_rate}
        for r in rows
    ]).sort_values("trade_date").reset_index(drop=True)
    return df


def _evaluate(df: pd.DataFrame, symbol: str, name: str,
              min_amount: float, min_amplitude: float,
              turnover_min: float, turnover_max: float, trend_window: int,
              trend_strict: bool = False) -> dict | None:
    prev_close = df["close"].shift(1)
    amplitude = (df["high"] - df["low"]) / prev_close

    amount_avg = float(df["amount"].tail(5).mean())
    if amount_avg < min_amount:
        return None
    amplitude_avg = float(amplitude.tail(5).mean())
    if amplitude_avg < min_amplitude:
        return None

    turnover_avg = float(df["turnover_rate"].tail(5).mean())
    if turnover_avg > 0:  # 数据缺失（=0）时跳过该条件
        if not (turnover_min <= turnover_avg <= turnover_max):
            return None

    close = df["close"]
    ma20 = close.rolling(trend_window).mean()
    trend_up = bool(close.iloc[-1] > ma20.iloc[-1]) and bool(ma20.iloc[-1] > ma20.iloc[-6])
    if not trend_up:
        return None
    if trend_strict:  # 弱势市场门控：额外要求 60 日线向上且价格站上
        ma60 = close.rolling(60).mean()
        if not (close.iloc[-1] > ma60.iloc[-1] and bool(ma60.iloc[-1] > ma60.iloc[-6])):
            return None

    ret_20 = float(close.iloc[-1] / close.iloc[-trend_window] - 1) if len(close) >= trend_window else 0.0
    score = (
        amplitude_avg * 100 * 0.4
        + math.log10(max(amount_avg, 1.0)) / 10 * 0.3
        + (0.3 if ret_20 > 0 else 0.0)
    )
    return {
        "symbol": symbol,
        "name": name,
        "amount_avg": round(amount_avg, 0),
        "amplitude_avg": round(amplitude_avg * 100, 2),
        "turnover_avg": round(turnover_avg, 2),
        "ret_20": round(ret_20 * 100, 2),
        "score": round(score, 3),
    }
