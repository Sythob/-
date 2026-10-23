"""撮合逻辑：回测与模拟盘共用。

回测铁律：信号在 t 收盘产生 → t+1 开盘价 ± 滑点成交（防未来函数）。
涨跌停：涨停日买单不成交、跌停日卖单不成交（可配置开关）。
限价单（保守模型）：仅当参考价穿越委托价时按委托价成交，避免日内假设。
"""
from app.engine.costs import CostModel
from app.engine.objects import Direction, OrderData


def match_order(
    order: OrderData,
    price: float,
    cost_model: CostModel,
    limit_up: bool = False,
    limit_down: bool = False,
) -> tuple[float, float] | None:
    """尝试撮合。price 为参考价（回测=开盘价，模拟盘=最新快照价）。

    返回 (成交价, 手续费)；None 表示未成交（限价未穿越/涨跌停阻挡）。
    """
    if limit_up and order.direction == Direction.BUY:
        return None
    if limit_down and order.direction == Direction.SELL:
        return None

    if order.price_type == "limit":
        if order.direction == Direction.BUY and price > order.price:
            return None
        if order.direction == Direction.SELL and price < order.price:
            return None
        fill_price = order.price
    else:
        fill_price = price

    fill_price = cost_model.fill_price(fill_price, order.direction)
    fee = cost_model.total_fee(fill_price, order.volume, order.direction)
    return fill_price, fee
