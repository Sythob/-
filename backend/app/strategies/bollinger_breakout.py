"""布林带突破策略：收盘突破上轨买入，跌破中轨卖出。"""
from app.ml.indicators import boll
from app.strategies.base import Strategy


class BollingerBreakoutStrategy(Strategy):
    name = "bollinger_breakout"
    display_name = "布林带突破策略"
    description = "收盘价突破布林上轨买入，跌破中轨卖出。适合波动扩张行情"
    params = {"period": 20, "k": 2.0}
    params_schema = [
        {"name": "period", "type": "int", "default": 20, "min": 10, "max": 60, "label": "布林周期"},
        {"name": "k", "type": "float", "default": 2.0, "min": 1.5, "max": 3.0, "label": "标准差倍数"},
    ]

    def on_init(self, data: dict) -> None:
        self._bands: dict[str, dict] = {}
        for sym, df in data.items():
            b = boll(df["close"], self.params["period"], self.params["k"])
            self._bands[sym] = {
                "upper": dict(zip(df["trade_date"], b["upper"])),
                "mid": dict(zip(df["trade_date"], b["mid"])),
            }

    def on_bar(self, bar) -> None:
        upper = self._bands[bar.symbol]["upper"].get(bar.trade_date)
        mid = self._bands[bar.symbol]["mid"].get(bar.trade_date)
        if upper is None or mid is None:
            return
        pos = self.position(bar.symbol)
        holding = pos is not None and pos.volume > 0

        if not holding and bar.close > upper:
            self.buy(bar.symbol)
        elif holding and bar.close < mid:
            self.sell(bar.symbol)
