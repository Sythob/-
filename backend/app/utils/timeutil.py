"""A股交易时段工具（Asia/Shanghai）。"""
from datetime import datetime, time
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")


def now_cn() -> datetime:
    return datetime.now(TZ)


def in_trading_hours(dt: datetime | None = None) -> bool:
    """是否交易时段（含集合竞价前后的容差：09:25~11:35, 12:55~15:05）。"""
    dt = dt or now_cn()
    if dt.weekday() >= 5:
        return False
    t = dt.time()
    return (time(9, 25) <= t <= time(11, 35)) or (time(12, 55) <= t <= time(15, 5))


def time_after(hour: int, minute: int, dt: datetime | None = None) -> bool:
    """当前时间是否晚于给定时刻。"""
    dt = dt or now_cn()
    return dt.time() >= time(hour, minute)
