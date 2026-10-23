"""数据同步：证券主表、交易日历、自选池日线 upsert、新闻抓取。

增量策略：akshare 不支持按股增量取数 → 全量拉取 + INSERT OR REPLACE 幂等 upsert，
天然修复历史缺口。自选池（默认几十只）每日同步 1~2 分钟完成；全市场同步是周末手动选项。
"""
from app.config import get_settings
from app.data import calendar, database
from app.data.base import get_source
from app.data.loader import get_latest_bar
from app.data.models import DailyBar, Instrument, MinuteBar, News, TradeCalendar
from app.data.symbols import board_of, exchange_of
from app.utils.logger import get_logger
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

logger = get_logger("data.sync")

# 指数主表（始终入库）；指数历史仅同步白名单内的（避免 000001 上证指数与平安银行撞代码）
INDEX_INSTRUMENTS = [
    ("000001", "上证指数", "sh", "index", "", "active", "", 0),
    ("399001", "深证成指", "sz", "index", "", "active", "", 0),
    ("399006", "创业板指", "sz", "index", "", "active", "", 0),
    ("000300", "沪深300", "sh", "index", "", "active", "", 0),
]
# 可与个股代码共存、允许入库历史的指数（000300 沪深300 为回测基准）
INDEX_HISTORY_WHITELIST = {"000300", "399001", "399006"}

_NUMERIC_BAR_COLS = {
    "open", "high", "low", "close", "volume", "amount",
    "pct_change", "turnover_rate", "adj_factor",
}


def _clean_rows(rows: list[dict]) -> list[dict]:
    """数值列统一转 float（numpy 类型对 sqlite 驱动的兼容处理）。"""
    cleaned = []
    for row in rows:
        cleaned.append({
            k: (float(v) if k in _NUMERIC_BAR_COLS and v is not None else v)
            for k, v in row.items()
        })
    return cleaned


def _chunks(rows: list, size: int = 1000):
    """SQLite 单语句变量数有限（默认 999/32766），批量 upsert 必须分块。"""
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def _upsert_daily_bars(rows: list[dict]) -> int:
    """日线幂等 upsert：同 (symbol, trade_date) 后值覆盖前值。分块执行防变量超限。"""
    rows = _clean_rows(rows)
    if not rows:
        return 0
    with database.get_session() as session:
        for chunk in _chunks(rows):
            stmt = sqlite_insert(DailyBar).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["symbol", "trade_date"],
                set_={
                    "open": stmt.excluded.open,
                    "high": stmt.excluded.high,
                    "low": stmt.excluded.low,
                    "close": stmt.excluded.close,
                    "volume": stmt.excluded.volume,
                    "amount": stmt.excluded.amount,
                    "pct_change": stmt.excluded.pct_change,
                    "turnover_rate": stmt.excluded.turnover_rate,
                    "adj_factor": stmt.excluded.adj_factor,
                },
            )
            session.execute(stmt)
        session.commit()
    return len(rows)


def _upsert_calendar(rows: list[dict]) -> int:
    if not rows:
        return 0
    with database.get_session() as session:
        stmt = sqlite_insert(TradeCalendar).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["trade_date"], set_={"is_open": stmt.excluded.is_open}
        )
        session.execute(stmt)
        session.commit()
    return len(rows)


def _upsert_instruments(rows: list[dict]) -> int:
    dedup = {r["symbol"]: r for r in rows if r.get("symbol")}
    if not dedup:
        return 0
    values = list(dedup.values())
    with database.get_session() as session:
        for chunk in _chunks(values):
            stmt = sqlite_insert(Instrument).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["symbol"],
                set_={
                    "name": stmt.excluded.name,
                    "exchange": stmt.excluded.exchange,
                    "type": stmt.excluded.type,
                    "board": stmt.excluded.board,
                    "status": stmt.excluded.status,
                    "list_date": stmt.excluded.list_date,
                    "is_st": stmt.excluded.is_st,
                },
            )
            session.execute(stmt)
        session.commit()
    return len(dedup)


def sync_trade_calendar(source=None) -> int:
    source = source or get_source()
    df = source.get_trade_calendar()
    n = _upsert_calendar(df.to_dict("records"))
    calendar.invalidate_cache()
    logger.info("交易日历同步：%d 条", n)
    return n


def sync_instruments(full: bool = False, source=None) -> int:
    """证券主表：指数 + 自选池（快照补名称）；full=True 追加全市场 A股。"""
    source = source or get_source()
    rows = [
        {
            "symbol": r[0], "name": r[1], "exchange": r[2], "type": r[3],
            "board": r[4], "status": r[5], "list_date": r[6], "is_st": r[7],
        }
        for r in INDEX_INSTRUMENTS
    ]
    if full:
        try:
            df = source.get_instruments()
            rows += df.to_dict("records")
        except Exception as exc:
            logger.warning("全市场证券主表同步失败: %s", exc)
    try:
        quotes = source.get_realtime_quotes(get_settings().watchlist_symbols)
        if not quotes.empty:
            for _, q in quotes.iterrows():
                sym, name = str(q.get("symbol", "")), str(q.get("name", ""))
                if sym and name:
                    rows.append({
                        "symbol": sym, "name": name, "exchange": exchange_of(sym),
                        "type": "stock", "board": board_of(sym), "status": "active",
                        "list_date": "", "is_st": int("ST" in name.upper()),
                    })
    except Exception as exc:
        logger.warning("自选池名称获取失败: %s", exc)
    n = _upsert_instruments(rows)
    logger.info("证券主表同步：%d 条", n)
    return n


def _position_symbols() -> list[str]:
    """模拟账户当前持仓代码（同步默认范围自动带上，保证持仓估值用最新收盘价）。"""
    try:
        from app.paper import account as account_service

        return [p["symbol"] for p in account_service.account_summary()["positions"]]
    except Exception:  # noqa: BLE001
        return []


def _default_sync_symbols() -> list[str]:
    """默认同步范围：自选池 + 模拟持仓（去重，自选池顺序优先）。"""
    symbols = list(get_settings().watchlist_symbols)
    for sym in _position_symbols():
        if sym not in symbols:
            symbols.append(sym)
    return symbols


def sync_watchlist_bars(symbols: list[str] | None = None, source=None) -> dict:
    """日线增量（默认：自选池+模拟持仓）：已是最新交易日则跳过，否则全量拉取 + upsert。"""
    source = source or get_source()
    symbols = symbols or _default_sync_symbols()
    latest_date = calendar.latest_trade_date()
    stats = {"updated": [], "skipped": [], "failed": []}
    for symbol in symbols:
        try:
            latest = get_latest_bar(symbol)
            if latest and latest["trade_date"] >= latest_date:
                stats["skipped"].append(symbol)
                continue
            df = source.get_daily_bars(symbol)
            if df is None or df.empty:
                stats["failed"].append(symbol)
                continue
            rows = df.to_dict("records")
            for row in rows:  # 数据源标准化不含 symbol 列，入库前补齐
                row["symbol"] = symbol
            _upsert_daily_bars(rows)
            stats["updated"].append(symbol)
            logger.info("同步 %s：%d 根日线", symbol, len(df))
        except Exception as exc:
            logger.warning("同步失败 %s: %s", symbol, exc)
            stats["failed"].append(symbol)
    return stats


def sync_index_bars(symbols: list[str] | None = None, source=None) -> dict:
    """指数日线同步（仅白名单：沪深300 等，供回测基准对比）。"""
    source = source or get_source()
    symbols = symbols or sorted(INDEX_HISTORY_WHITELIST)
    stats = {"updated": [], "skipped": [], "failed": []}
    latest_date = calendar.latest_trade_date()
    for symbol in symbols:
        if symbol not in INDEX_HISTORY_WHITELIST:
            continue
        try:
            latest = get_latest_bar(symbol)
            if latest and latest["trade_date"] >= latest_date:
                stats["skipped"].append(symbol)
                continue
            df = source.get_index_daily_bars(symbol)
            if df is None or df.empty:
                stats["failed"].append(symbol)
                continue
            rows = df.to_dict("records")
            for row in rows:
                row["symbol"] = symbol
            _upsert_daily_bars(rows)
            stats["updated"].append(symbol)
        except Exception as exc:
            logger.warning("指数同步失败 %s: %s", symbol, exc)
            stats["failed"].append(symbol)
    return stats


def _upsert_minute_bars(symbol: str, interval: str, rows: list[dict]) -> int:
    """分钟/小时线幂等 upsert。"""
    cleaned = []
    for row in rows:
        cleaned.append({
            "symbol": symbol, "interval": interval, "dt": str(row.get("dt", "")),
            "open": float(row.get("open") or 0.0), "high": float(row.get("high") or 0.0),
            "low": float(row.get("low") or 0.0), "close": float(row.get("close") or 0.0),
            "volume": float(row.get("volume") or 0.0),
        })
    cleaned = [r for r in cleaned if r["dt"]]
    if not cleaned:
        return 0
    with database.get_session() as session:
        for chunk in _chunks(cleaned):
            stmt = sqlite_insert(MinuteBar).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["symbol", "dt", "interval"],
                set_={
                    "open": stmt.excluded.open, "high": stmt.excluded.high,
                    "low": stmt.excluded.low, "close": stmt.excluded.close,
                    "volume": stmt.excluded.volume,
                },
            )
            session.execute(stmt)
        session.commit()
    return len(cleaned)


def sync_hour_bars(symbols: list[str] | None = None, source=None) -> dict:
    """60 分钟线积累（默认：自选池+模拟持仓；新浪源只保留近端数据，需每日积累）。"""
    source = source or get_source()
    symbols = symbols or _default_sync_symbols()
    stats = {"updated": [], "failed": []}
    for symbol in symbols:
        try:
            df = source.get_hour_bars(symbol)
            if df is None or df.empty:
                stats["failed"].append(symbol)
                continue
            n = _upsert_minute_bars(symbol, "60m", df.to_dict("records"))
            stats["updated"].append(symbol)
            logger.info("同步 %s 60分钟线：%d 根", symbol, n)
        except Exception as exc:
            logger.warning("60分钟线同步失败 %s: %s", symbol, exc)
            stats["failed"].append(symbol)
    return stats


def sync_news(symbols: list[str] | None = None, limit: int = 20, source=None) -> int:
    """自选池新闻抓取，按 url 去重（已存在的跳过）。"""
    source = source or get_source()
    symbols = symbols or get_settings().watchlist_symbols
    inserted = 0
    with database.get_session() as session:
        existing = {u for (u,) in session.query(News.url).all() if u}
        for symbol in symbols:
            try:
                df = source.get_news(symbol, limit=limit)
            except Exception as exc:
                logger.warning("新闻抓取失败 %s: %s", symbol, exc)
                continue
            new_rows = []
            for _, r in df.iterrows():
                url = str(r.get("url", ""))
                if url and url not in existing:
                    new_rows.append(News(
                        symbol=symbol,
                        source=str(r.get("source", "")),
                        title=str(r.get("title", "")),
                        content=str(r.get("content", "")),
                        url=url,
                        pub_time=str(r.get("pub_time", "")),
                    ))
                    existing.add(url)
            if new_rows:
                session.add_all(new_rows)
                session.commit()
                inserted += len(new_rows)
    logger.info("新闻同步：新增 %d 条", inserted)
    return inserted


def _is_etf(symbol: str) -> bool:
    return symbol.startswith(("5", "15", "16", "18"))


def data_is_fresh(symbols: list[str] | None = None) -> bool:
    """今日数据是否已就位（自选池日线 + 指数 + 广度 + 新闻）。

    ETF 数据源（新浪 fund_etf_hist_sina）天然滞后一日发布，给予 1 天宽限。
    """
    from datetime import datetime, timedelta

    symbols = symbols or _default_sync_symbols()
    latest_trade = calendar.latest_trade_date()
    latest_dt = datetime.strptime(latest_trade, "%Y-%m-%d")
    etf_grace = (latest_dt - timedelta(days=1)).strftime("%Y-%m-%d")
    for sym in symbols:
        bar = get_latest_bar(sym)
        threshold = etf_grace if _is_etf(sym) else latest_trade
        if bar is None or bar["trade_date"] < threshold:
            return False
    for sym in sorted(INDEX_HISTORY_WHITELIST):
        bar = get_latest_bar(sym)
        if bar is None or bar["trade_date"] < latest_trade:
            return False
    try:
        from app.data.sentiment import latest_breadth

        breadth = latest_breadth()
        if not breadth or breadth[0]["date"] < latest_trade:
            return False
    except Exception:  # noqa: BLE001
        return False
    with database.get_session() as session:
        latest_news = session.query(News.pub_time).order_by(News.pub_time.desc()).first()
    if latest_news is None or latest_news[0][:10] < latest_trade:
        return False
    return True


def ensure_fresh_data() -> dict:
    """补齐今日数据（幂等）：已是最新则跳过；否则同步自选池+指数+广度+新闻。

    失败的标的隔 10 秒自动重试一轮（限流恢复后自愈）。
    """
    import time as _time

    if data_is_fresh():
        return {"status": "fresh", "synced": []}
    stats_bars = sync_watchlist_bars()
    stats_idx = sync_index_bars()
    if stats_bars["failed"] or stats_idx["failed"]:
        _time.sleep(10)
        retry_bars = sync_watchlist_bars(stats_bars["failed"])
        retry_idx = sync_index_bars(stats_idx["failed"])
        stats_bars["failed"] = retry_bars["failed"]
        stats_idx["failed"] = retry_idx["failed"]
    breadth = {"ok": False}
    try:
        from app.data.sentiment import sync_breadth

        breadth = sync_breadth()
    except Exception as exc:  # noqa: BLE001
        logger.warning("广度同步失败: %s", exc)
        breadth = {"ok": False, "error": str(exc)[:80]}
    n_news = 0
    try:
        n_news = sync_news()
    except Exception as exc:  # noqa: BLE001
        logger.warning("新闻同步失败: %s", exc)
    return {
        "status": "synced",
        "bars": {"updated": stats_bars["updated"], "failed": stats_bars["failed"]},
        "index_bars": {"updated": stats_idx["updated"], "failed": stats_idx["failed"]},
        "breadth": breadth,
        "news": n_news,
    }


def sync_all(symbols: list[str] | None = None, full: bool = False, with_news: bool = False) -> dict:
    """一键全量同步。"""
    database.init_db()
    n_cal = sync_trade_calendar()
    n_inst = sync_instruments(full=full)
    stats_bars = sync_watchlist_bars(symbols)
    stats_idx = sync_index_bars()
    n_news = sync_news(symbols) if with_news else 0
    breadth = {"ok": False}
    try:
        from app.data.sentiment import sync_breadth

        breadth = sync_breadth()  # 市场广度（快照失败不影响整体同步）
    except Exception as exc:  # noqa: BLE001
        logger.warning("广度同步失败: %s", exc)
        breadth = {"ok": False, "error": str(exc)[:80]}
    return {
        "calendar": n_cal,
        "instruments": n_inst,
        "bars": stats_bars,
        "index_bars": stats_idx,
        "news": n_news,
        "breadth": breadth,
    }
