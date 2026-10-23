"""组合账户：资金、持仓、T+1 可用量、FIFO 买入批次。

约定：avg_cost 为纯加权买入价（不含费用）；费用在成交时从现金直接扣减；
卖出已实现盈亏 = (卖价 - FIFO 配对买价) × 数量 - 卖出费用。
"""
from app.engine.objects import AccountData, Direction, PositionData


class Portfolio:
    def __init__(self, initial_capital: float, lot_size: int = 100):
        self.account = AccountData(cash=initial_capital, initial_capital=initial_capital)
        self.positions: dict[str, PositionData] = {}
        self.lot_size = lot_size
        self._lots: dict[str, list[list[int, float]]] = {}  # FIFO 买入批次 [volume, price]
        self.last_close: dict[str, float] = {}

    # ---- 持仓/资金查询 ----

    def get_position(self, symbol: str) -> PositionData | None:
        return self.positions.get(symbol)

    def equity(self) -> float:
        """总资产 = 现金 + Σ 持仓市值（按最后收盘价估值）。"""
        value = self.account.cash
        for sym, pos in self.positions.items():
            value += pos.volume * self.last_close.get(sym, 0.0)
        return round(value, 2)

    # ---- 成交结算 ----

    def buy_fill(self, symbol: str, price: float, volume: int, fee: float) -> None:
        self.account.cash -= price * volume + fee
        pos = self.positions.setdefault(symbol, PositionData(symbol=symbol))
        total_cost = pos.avg_cost * pos.volume + price * volume
        pos.volume += volume
        pos.avg_cost = round(total_cost / pos.volume, 6)
        # T+1：当日买入不进 available，收盘结算后解锁
        self._lots.setdefault(symbol, []).append([volume, price])

    def sell_fill(self, symbol: str, price: float, volume: int, fee: float) -> float:
        """按 FIFO 配对计算已实现盈亏，返回 pnl。"""
        pos = self.positions[symbol]
        pos.volume -= volume
        pos.available_volume -= volume
        self.account.cash += price * volume - fee

        matched_cost, remaining = 0.0, volume
        lots = self._lots.setdefault(symbol, [])
        while remaining > 0 and lots:
            lot_vol, lot_price = lots[0]
            matched = min(remaining, lot_vol)
            matched_cost += matched * lot_price
            if matched == lot_vol:
                lots.pop(0)
            else:
                lots[0] = [lot_vol - matched, lot_price]
            remaining -= matched

        avg_buy = matched_cost / volume
        pnl = round((price - avg_buy) * volume - fee, 2)
        pos.realized_pnl = round(pos.realized_pnl + pnl, 2)
        return pnl

    # ---- 结算 ----

    def settle_t1(self) -> None:
        """每日收盘结算：T+1 解锁（当日买入的股份转为可卖）。"""
        for pos in self.positions.values():
            pos.available_volume = pos.volume

    def update_last_close(self, prices: dict[str, float]) -> None:
        self.last_close.update(prices)

    # ---- 下单校验 ----

    def can_buy(self, price: float, volume: int, fee: float) -> bool:
        return self.account.cash >= price * volume + fee

    def can_sell(self, symbol: str, volume: int) -> tuple[bool, str]:
        pos = self.positions.get(symbol)
        if pos is None or pos.volume <= 0:
            return False, "无持仓"
        if volume > pos.available_volume:
            return False, "可卖数量不足（T+1）"
        if volume % self.lot_size != 0:
            return False, "卖出数量必须是整手"
        return True, ""

    def max_affordable_volume(self, price: float, fee_fn: callable) -> int:
        """市价全仓买入的最大整手数量：先按价估算，再按费用逐步下调至现金足够。"""
        lot = self.lot_size
        rough = int(self.account.cash / max(price, 1e-9))
        vol = rough // lot * lot
        while vol > 0:
            if price * vol + fee_fn(vol) <= self.account.cash:
                return vol
            vol -= lot
        return 0
