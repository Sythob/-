"""引擎核心数据对象。"""
from dataclasses import dataclass, field
from enum import Enum


class Direction(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    SUBMITTED = "submitted"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class BarData:
    symbol: str
    trade_date: str  # YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    amount: float = 0.0
    pct_change: float = 0.0  # 百分数（10.0 = +10%）
    limit_up: bool = False   # 涨停（拒买）
    limit_down: bool = False  # 跌停（拒卖）


@dataclass
class OrderData:
    symbol: str
    direction: Direction
    price_type: str = "market"  # market / limit
    price: float = 0.0          # 委托价（市价单为 0，成交时按行情价）
    volume: int = 0             # 委托量（股）；0 表示市价全仓买入（成交时按资金计算）
    order_id: int = 0
    status: OrderStatus = OrderStatus.SUBMITTED
    filled_price: float = 0.0
    created_date: str = ""
    reject_reason: str = ""
    fill_on_close: bool = False  # True=当日收盘撮合（尾盘单，需策略声明 close 模式）；当日未成交即撤销


@dataclass
class TradeData:
    symbol: str
    direction: Direction
    price: float
    volume: int
    trade_time: str
    commission: float = 0.0
    slippage: float = 0.0
    pnl: float = 0.0  # 卖出成交的已实现盈亏


@dataclass
class PositionData:
    symbol: str
    volume: int = 0             # 总持仓（股）
    available_volume: int = 0   # 可卖（股，T+1 约束）
    avg_cost: float = 0.0       # 加权买入均价（不含费用）
    realized_pnl: float = 0.0


@dataclass
class AccountData:
    cash: float
    frozen: float = 0.0
    initial_capital: float = 0.0
