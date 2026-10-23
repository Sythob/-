"""行情/数据 API。"""
import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_

from app.config import get_settings
from app.data import database, loader
from app.data.base import get_source
from app.data.models import Instrument, News
from app.data.sync import INDEX_HISTORY_WHITELIST, sync_all
from app.utils import tasks

router = APIRouter(tags=["market"])


def _records(df: pd.DataFrame) -> list[dict]:
    """DataFrame → JSON 安全记录（NaN/None 规范化，FastAPI 禁 NaN）。"""
    return df.fillna(0).to_dict("records")


@router.get("/market/instruments")
def instruments(keyword: str = "", type: str = ""):
    with database.get_session() as session:
        query = session.query(Instrument)
        if keyword:
            like = f"%{keyword}%"
            query = query.filter(or_(Instrument.symbol.like(like), Instrument.name.like(like)))
        if type:
            query = query.filter(Instrument.type == type)
        rows = query.limit(50).all()
        return [
            {"symbol": r.symbol, "name": r.name, "exchange": r.exchange,
             "type": r.type, "board": r.board, "is_st": r.is_st}
            for r in rows
        ]


_minutes_cache: dict[str, tuple[float, list]] = {}


@router.get("/market/minutes/{symbol}")
def minutes(symbol: str):
    """当日分时数据（1 分钟粒度，30 秒 TTL 缓存）。"""
    import time

    from app.data.base import get_source

    hit = _minutes_cache.get(symbol)
    if hit and time.monotonic() - hit[0] < 30:
        return hit[1]
    df = get_source().get_today_minutes(symbol)
    result = _records(df)
    _minutes_cache[symbol] = (time.monotonic(), result)
    return result


@router.get("/market/bars/{symbol}")
def bars(symbol: str, interval: str = "daily", adjust: str = "qfq",
         start: str = "", end: str = ""):
    if interval == "daily":
        if adjust not in ("raw", "qfq", "hfq"):
            raise HTTPException(400, "adjust 仅支持 raw/qfq/hfq")
        df = loader.load_daily(symbol, start=start, end=end, adjust=adjust)
        return _records(df)
    if interval == "60m":
        df = loader.load_minute(symbol, interval="60m", start=start, end=end)
        return _records(df)
    raise HTTPException(400, "interval 仅支持 daily/60m")


@router.get("/market/quotes")
def quotes(symbols: str = ""):
    syms = [s.strip() for s in symbols.split(",") if s.strip()] or get_settings().watchlist_symbols
    try:
        df = get_source().get_realtime_quotes(syms)
    except Exception as exc:
        raise HTTPException(502, f"实时行情获取失败（akshare 可能限流）: {exc}") from exc
    return _records(df)


@router.get("/market/indices")
def indices():
    """四组指数（A股/港股/美股/全球），多源聚合 + 缓存；A股指数附 30 日走势（本地库）。"""
    from app.data.global_indices import get_grouped_indices

    result = get_grouped_indices()
    # A股指数 30 日走势迷你图数据（本地日线库，仅已有历史的指数）
    histories: dict[str, list[dict]] = {}
    try:
        from app.data.loader import load_daily

        for index in result["groups"][0]["indices"]:
            sym = index["symbol"].removeprefix("sh").removeprefix("sz")
            df = load_daily(sym, adjust="raw").tail(30)
            if not df.empty:
                histories[index["symbol"]] = [
                    {"date": r["trade_date"], "close": r["close"]}
                    for _, r in df.iterrows()
                ]
    except Exception:  # noqa: BLE001
        histories = {}
    result["histories"] = histories
    return result


@router.get("/market/news")
def news(symbol: str = "", limit: int = 50):
    with database.get_session() as session:
        query = session.query(News)
        if symbol:
            query = query.filter(News.symbol == symbol)
        rows = query.order_by(News.pub_time.desc()).limit(limit).all()
        return [
            {"id": r.id, "symbol": r.symbol, "source": r.source, "title": r.title,
             "content": r.content, "url": r.url, "pub_time": r.pub_time,
             "sentiment_score": r.sentiment_score, "sentiment_label": r.sentiment_label,
             "sentiment_reason": r.sentiment_reason}
            for r in rows
        ]


class SyncRequest(BaseModel):
    symbols: list[str] = []
    news: bool = False


@router.post("/market/sync")
def sync(req: SyncRequest):
    task_id = tasks.submit(sync_all, req.symbols or None, False, req.news)
    return {"task_id": task_id}


@router.get("/market/sentiment")
def sentiment():
    """市场情绪：涨跌停/涨跌家数广度 + 两融余额。"""
    from app.data.sentiment import get_sentiment_overview

    return get_sentiment_overview()


@router.post("/market/ensure_fresh")
def ensure_fresh():
    """确保今日数据就位（幂等）：数据已新则秒回，否则后台同步+生成日报。"""
    from app.data import calendar
    from app.data.sync import ensure_fresh_data
    from app.utils import tasks

    if not calendar.is_trade_day():
        return {"status": "holiday"}

    def _ensure_and_report():
        result = ensure_fresh_data()
        if result["status"] == "synced":
            import asyncio

            from app.llm.tasks.daily_report import generate_daily_report
            from app.llm.tasks.news_sentiment import score_news

            try:
                asyncio.run(score_news(30))
            except Exception:  # noqa: BLE001
                logger.warning("新闻情绪打分失败（不影响使用）")
            asyncio.run(generate_daily_report())
        return result

    task_id = tasks.submit(_ensure_and_report)
    return {"task_id": task_id}


@router.get("/market/sectors")
def sectors():
    """行业板块行情（新浪源，按涨跌幅排序）。"""
    from app.data.sectors import get_sectors

    return get_sectors()


@router.get("/market/sectors/{name}/stocks")
def sector_stocks(name: str):
    """板块成分股列表（按涨跌幅排序）。"""
    from app.data.market_extra import get_sector_detail

    try:
        return get_sector_detail(name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"板块成分股获取失败：{exc}") from exc


@router.get("/market/lhb")
def lhb():
    """最近交易日龙虎榜（新浪源）。"""
    from app.data.market_extra import get_lhb

    try:
        return get_lhb()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"龙虎榜获取失败：{exc}") from exc


@router.get("/market/fund-flow")
def fund_flow(kind: str = "industry"):
    """资金流向排行（东财即时）：kind=industry 行业 / concept 概念。"""
    if kind not in ("industry", "concept"):
        raise HTTPException(400, "kind 仅支持 industry/concept")
    from app.data.market_extra import get_fund_flow

    try:
        return get_fund_flow(kind)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"资金流向获取失败：{exc}") from exc


@router.get("/market/northbound")
def northbound():
    """北向资金：当日汇总 + 近 30 日历史（东财源；2024-08 起盘中数据停发）。"""
    from app.data.market_extra import get_northbound

    try:
        return get_northbound()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"北向资金获取失败：{exc}") from exc


@router.get("/market/tscreen")
def tscreen(limit: int = 20):
    """做T候选池：精选 + 观察区 + 市场状态门控（弱势自动收紧趋势条件）。"""
    from app.data.sentiment import market_regime
    from app.data.tscreener import load_watch_zone, screen_tsymbols

    state = market_regime()
    return {
        "candidates": screen_tsymbols(limit=min(limit, 50), trend_strict=state["trend_strict"]),
        "watch_zone": load_watch_zone(),
        "market_state": state,
    }


@router.get("/market/boardscreen")
def boardscreen():
    """打板候选池（当日涨停/近涨停候选，含连板高度与风险标注）。"""
    from app.data.board_screener import load_board_watch

    return load_board_watch()


@router.post("/market/boardscreen/refresh")
def boardscreen_refresh():
    """打板候选刷新：快照粗筛 → 定向同步 → 连板/换手标注（约 2~4 分钟，后台运行）。"""
    from app.data.board_screener import refresh_board_screen
    from app.utils import tasks

    task_id = tasks.submit(refresh_board_screen)
    return {"task_id": task_id}


@router.post("/market/tscreen/refresh")
def tscreen_refresh():
    """全市场做T候选刷新：快照粗筛 → 定向同步 → 精筛（约 3~6 分钟，后台运行）。"""
    from app.data.tscreener import refresh_market_tscreen
    from app.utils import tasks

    task_id = tasks.submit(refresh_market_tscreen)
    return {"task_id": task_id}
