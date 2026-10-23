"""A股费用模型：佣金（含最低 5 元）、印花税（仅卖出）、过户费、滑点。"""
from dataclasses import dataclass

from app.engine.objects import Direction


@dataclass
class CostModel:
    commission_rate: float = 0.00025   # 佣金费率
    commission_min: float = 5.0        # 最低佣金（元）
    stamp_tax_rate: float = 0.0005     # 印花税（仅卖出）
    transfer_fee_rate: float = 0.00001  # 过户费（双向）
    slippage: float = 0.01             # 滑点（元/股，买入加价、卖出减价）

    def commission(self, amount: float) -> float:
        if amount <= 0:
            return 0.0
        return max(amount * self.commission_rate, self.commission_min)

    def stamp_tax(self, amount: float, direction: Direction) -> float:
        if direction == Direction.SELL:
            return amount * self.stamp_tax_rate
        return 0.0

    def transfer_fee(self, amount: float) -> float:
        return amount * self.transfer_fee_rate

    def total_fee(self, price: float, volume: int, direction: Direction) -> float:
        """单笔成交总费用（佣金+印花税+过户费），四舍五入到分。"""
        amount = price * volume
        fee = self.commission(amount) + self.stamp_tax(amount, direction) + self.transfer_fee(amount)
        return round(fee, 2)

    def fill_price(self, price: float, direction: Direction) -> float:
        """含滑点的成交价：买入加滑点、卖出减滑点。"""
        if direction == Direction.BUY:
            return round(price + self.slippage, 4)
        return round(price - self.slippage, 4)
