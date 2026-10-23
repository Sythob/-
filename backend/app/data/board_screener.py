"""打板候选池：筛选当日涨停/接近涨停的候选，标注连板高度与封板质量。

打板是极高风险操作，本筛选只提供候选与客观标注，不构成操作建议。

筛选标准（经典打板选手共识）：
- 今日涨幅 ≥ 7%（接近涨停）；排除 ST
- 连板高度：连续涨停天数（主板 9.5%、创业/科创 19.5%、北交所 29.5% 近似判定）
- 封板质量：换手率适中（5~15% 的换手板优于一字板）、成交额充足
- 评分：首板/2板优先（分数 10/(连板数+1)），换手充分加分，一字板与 3 板以上降分并标注风险
"""
import threading
import time
from datetime import datetime

import pandas as pd

from app.data import database
from app.data.models import BoardWatch, DailyBar, Instrument
from app.utils.logger import get_logger

logger = get_logger("data.board_screener")

_cache: tuple[float, list[dict]] = (0.0, [])
_lock = threading.Lock()


def limit_threshold(symbol: str) -> float:
    """各板块涨停近似阈值（%）。"""
    if symbol.startswith(("30", "68")):
        return 19.5  # 创业板/科创板
    if symbol.startswith(("8", "4", "92")):
        return 29.5  # 北交所
    return 9.5  # 主板


def calc_streak(pct_series: list[float], threshold: float) -> int:
    """连续涨停天数（从最新往回数）。"""
    streak = 0
    for pct in reversed(pct_series):
        if pct is not None and pct >= threshold - 0.3:
            streak += 1
        else:
            break
    return streak


def _load_recent_pct(symbol: str, n: int = 15) -> list[float]:
    with database.get_session() as session:
        rows = (
            session.query(DailyBar.pct_change)
            .filter(DailyBar.symbol == symbol)
            .order_by(DailyBar.trade_date.desc())
            .limit(n).all()
        )
    return [r[0] for r in reversed(rows)]


def _recent_pct_before(symbol: str, as_of: str, n: int = 15) -> list[float]:
    """as_of 之前（不含当天）的涨幅序列——连板历史，今日涨幅由快照单独提供。"""
    with database.get_session() as session:
        rows = (
            session.query(DailyBar.pct_change)
            .filter(DailyBar.symbol == symbol, DailyBar.trade_date < as_of)
            .order_by(DailyBar.trade_date.desc())
            .limit(n).all()
        )
    return [r[0] for r in reversed(rows)]


def screen_board_candidates(quotes: pd.DataFrame, top_n: int = 30,
                            turnover_map: dict[str, float] | None = None,
                            as_of: str | None = None) -> list[dict]:
    """快照粗筛 + 本地连板/换手标注。

    换手率优先用 turnover_map（腾讯批量报价，一次请求拿全部候选）；
    连板高度 = as_of 之前的本地历史涨幅 + 今日快照涨幅（无需逐股补日线）。
    """
    if quotes is None or quotes.empty:
        return []
    as_of = as_of or datetime.now().strftime("%Y-%m-%d")
    turnover_map = turnover_map or {}
    pct = pd.to_numeric(quotes["pct_change"], errors="coerce")
    name = quotes["name"].astype(str)
    mask = (
        (pct >= 7.0)
        & (~name.str.contains("ST", case=False, na=False))
    )
    coarse = quotes[mask]
    if coarse.empty:
        return []

    results = []
    for _, r in coarse.iterrows():
        symbol = str(r["symbol"])
        threshold = limit_threshold(symbol)
        today_pct = float(r["pct_change"])
        # 连板：as_of 之前的本地历史 + 今日快照涨幅
        history = _recent_pct_before(symbol, as_of)
        streak = calc_streak(history + [today_pct], threshold)
        is_limit = today_pct >= threshold - 0.3
        # 换手：腾讯批量快照优先，本地日线兜底
        turnover = turnover_map.get(symbol)
        if turnover is None:
            turnover = _recent_turnover(symbol)
        amount = float(r.get("amount") or 0.0)
        # 评分：首板/2板优先 + 换手板加分 + 高位降分
        score = 10.0 / (streak + 1)
        if 5.0 <= turnover <= 15.0:
            score += 2.0
        elif 0 < turnover < 3.0:
            score -= 3.0  # 一字板买不进
        if streak >= 3:
            score -= 2.0  # 高位风险
        if not is_limit:
            score -= 1.0  # 未封板
        if turnover <= 0:
            risk = "数据待补"
        elif streak >= 3:
            risk = "高位风险"
        elif turnover < 3:
            risk = "一字板"
        else:
            risk = "正常"
        results.append({
            "symbol": symbol,
            "name": str(r["name"]),
            "pct_change": round(today_pct, 2),
            "streak": streak,
            "is_limit": is_limit,
            "turnover": round(turnover, 2),
            "amount": amount,
            "score": round(score, 2),
            "risk": risk,
        })
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_n]


def _recent_turnover(symbol: str) -> float:
    with database.get_session() as session:
        rows = (
            session.query(DailyBar.turnover_rate)
            .filter(DailyBar.symbol == symbol)
            .order_by(DailyBar.trade_date.desc())
            .limit(5).all()
        )
    values = [r[0] for r in rows if r[0]]
    return sum(values) / len(values) if values else 0.0


def _save_board_watch(rows: list[dict]) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    with database.get_session() as session:
        # 清理历史日期的旧候选，只保留当日（避免陈旧数据残留展示）
        session.query(BoardWatch).filter(BoardWatch.date != today).delete()
        if not rows:
            session.commit()
            return 0
        for r in rows:
            existing = session.get(BoardWatch, r["symbol"])
            if existing is None:
                existing = BoardWatch(symbol=r["symbol"])
                session.add(existing)
            existing.name = r["name"]
            existing.pct_change = r["pct_change"]
            existing.streak = r["streak"]
            existing.turnover = r["turnover"]
            existing.amount = r["amount"]
            existing.score = r["score"]
            existing.risk = r["risk"]
            existing.date = today
        session.commit()
    return len(rows)


def load_board_watch() -> list[dict]:
    with database.get_session() as session:
        latest = session.query(BoardWatch.date).order_by(BoardWatch.date.desc()).first()
        if latest is None:
            return []
        rows = (
            session.query(BoardWatch)
            .filter(BoardWatch.date == latest[0])
            .order_by(BoardWatch.score.desc()).all()
        )
        return [
            {"symbol": r.symbol, "name": r.name, "pct_change": r.pct_change,
             "streak": r.streak, "turnover": r.turnover, "amount": r.amount,
             "score": r.score, "risk": r.risk, "date": r.date}
            for r in rows
        ]


def refresh_board_screen(top_n: int = 30) -> dict:
    """打板候选刷新：快照粗筛 → 腾讯批量换手率 → 连板/换手标注 → 落库。

    全程无逐股补日线（约 40 秒）；仅对「本地无任何日线」的新股补一次历史，
    连板高度 = 本地历史 + 今日快照涨幅。
    """
    from app.data.base import get_source
    from app.data.sync import _upsert_instruments, sync_watchlist_bars
    from app.data.symbols import board_of, exchange_of

    quotes = get_source().get_realtime_quotes(None)
    if quotes is None or quotes.empty:
        logger.warning("打板刷新中止：全市场快照为空（数据源不可用）")
        return {"coarse": 0, "candidates": [], "reason": "快照为空"}
    coarse = quotes[
        pd.to_numeric(quotes["pct_change"], errors="coerce").fillna(0) >= 7.0
    ]
    if coarse.empty:
        logger.info("打板刷新完成：今日无涨幅≥7% 的股票")
        _save_board_watch([])
        return {"coarse": 0, "candidates": [], "reason": "无候选"}
    symbols = [str(s) for s in coarse["symbol"]]
    # 换手率：腾讯批量报价（一次请求 60 只）
    turnover_map = get_source().get_turnover_batch(symbols)
    candidates = screen_board_candidates(quotes, top_n=top_n, turnover_map=turnover_map)
    if not candidates:
        logger.warning("打板刷新中止：粗筛后无候选")
        return {"coarse": len(symbols), "candidates": []}

    _upsert_instruments([
        {"symbol": c["symbol"], "name": c["name"], "exchange": exchange_of(c["symbol"]),
         "type": "stock", "board": board_of(c["symbol"]), "status": "active",
         "list_date": "", "is_st": 0}
        for c in candidates
    ])
    # 仅本地完全无日线的新股补一次历史（连板计算需要）；
    # 北交所（4/8/9 开头）数据源普遍不可用，跳过避免拖慢刷新（连板按快照单板算）
    new_symbols = [
        c["symbol"] for c in candidates
        if not _load_recent_pct(c["symbol"]) and not c["symbol"].startswith(("4", "8", "9"))
    ]
    if new_symbols:
        sync_watchlist_bars(new_symbols)
    _save_board_watch(candidates)
    logger.info("打板候选刷新完成：%d 个候选（换手率覆盖 %d/%d）",
                len(candidates), sum(1 for c in candidates if c["turnover"] > 0), len(candidates))
    return {"coarse": len(symbols), "candidates": len(candidates)}
    return {"coarse": len(candidates), "candidates": candidates,
            "synced": stats["updated"], "sync_failed": stats["failed"]}
