"""交易日历工具：优先读本地 trade_calendar 表；表为空时退化为工作日判断。"""
import threading
import time
from datetime import date, datetime

from app.data import database
from app.data.models import TradeCalendar

_dates_cache: tuple[float, list[str]] = (0.0, [])
_cache_lock = threading.Lock()


def load_trade_dates() -> list[str]:
    """升序返回已入库的交易日（带 5 分钟缓存，高频调用友好）。"""
    global _dates_cache
    with _cache_lock:
        if _dates_cache[1] and time.monotonic() - _dates_cache[0] < 300:
            return _dates_cache[1]
    with database.get_session() as session:
        rows = session.query(TradeCalendar.trade_date).order_by(TradeCalendar.trade_date).all()
        dates = [r[0] for r in rows]
    with _cache_lock:
        _dates_cache = (time.monotonic(), dates)
    return dates


def invalidate_cache() -> None:
    """日历更新后调用（同步任务结束时）。"""
    global _dates_cache
    with _cache_lock:
        _dates_cache = (0.0, [])


def trade_date_set() -> set[str]:
    """交易日集合（供行情数据过滤非交易日垃圾行）。"""
    return set(load_trade_dates())


def _as_date(d: date | datetime | str | None) -> date:
    if d is None:
        return datetime.now().date()
    if isinstance(d, str):
        return datetime.strptime(d, "%Y-%m-%d").date()
    if isinstance(d, datetime):
        return d.date()
    return d


def is_trade_day(d: date | datetime | str | None = None) -> bool:
    """是否交易日。日历未同步时按周一至周五近似。"""
    day = _as_date(d)
    dates = load_trade_dates()
    if dates:
        return day.strftime("%Y-%m-%d") in set(dates)
    return day.weekday() < 5


def latest_trade_date() -> str:
    """最近一个「不晚于今天」的交易日（日历可能含未来日期，需过滤）。"""
    today = datetime.now().strftime("%Y-%m-%d")
    dates = [d for d in load_trade_dates() if d <= today]
    return dates[-1] if dates else today
