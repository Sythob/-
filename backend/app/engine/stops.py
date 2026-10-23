"""引擎化止损止盈：策略注册止损单，引擎逐日检查，触发后次日开盘强制卖出。

借鉴 vnpy 风控模块思路，但与撮合铁律一致：t 日收盘触发 → t+1 开盘成交（无未来函数）。
回测引擎与模拟盘引擎共用本模块。
"""
from dataclasses import dataclass, field
from enum import Enum

from app.engine.objects import BarData


class StopType(str, Enum):
    FIXED = "fixed"              # 固定价格止损
    TAKE_PROFIT = "take_profit"  # 固定价格止盈
    ATR = "atr"                  # ATR 倍数追踪止损（注册时传入 atr_value）
    TRAILING = "trailing"        # 移动止损：距持仓最高价回落 X%
    MAX_DRAWDOWN = "max_drawdown"  # 同 TRAILING（别名，语义一致）


@dataclass
class StopOrder:
    symbol: str
    stop_type: StopType
    trigger_price: float = 0.0   # FIXED / TAKE_PROFIT
    atr_multiple: float = 2.0    # ATR
    atr_value: float = 0.0       # ATR（注册时计算，避免引擎重复算指标）
    trailing_pct: float = 0.08   # TRAILING / MAX_DRAWDOWN（8%）
    highest: float = 0.0         # 动态：持仓期最高收盘价
    triggered: bool = False


def evaluate_stop(stop: StopOrder, close: float) -> bool:
    """收盘后评估是否触发。会更新 stop.highest（移动类）。"""
    if stop.triggered:
        return True
    stop.highest = max(stop.highest, close)
    if stop.stop_type == StopType.FIXED:
        return close <= stop.trigger_price
    if stop.stop_type == StopType.TAKE_PROFIT:
        return close >= stop.trigger_price
    if stop.stop_type == StopType.ATR:
        if stop.atr_value <= 0:
            return False
        trigger = stop.highest - stop.atr_multiple * stop.atr_value
        return close <= trigger
    if stop.stop_type in (StopType.TRAILING, StopType.MAX_DRAWDOWN):
        trigger = stop.highest * (1 - stop.trailing_pct)
        return close <= trigger
    return False
