"""网格交易策略：价格每下跌 grid_pct 买入一手，每上涨 grid_pct 卖出一手。

适合震荡行情；单边行情会持续加仓或踏空，注意配合风控使用。
"""
from app.strategies.base import Strategy


class GridTradingStrategy(Strategy):
    name = "grid_trading"
    display_name = "网格交易策略"
    description = "围绕最近成交价做网格：跌 grid% 买一手、涨 grid% 卖一手。震荡市适用，单边市需配合止损"
    params = {"grid_pct": 0.03, "volume_per_grid": 100, "max_layers": 5}
    params_schema = [
        {"name": "grid_pct", "type": "float", "default": 0.03, "min": 0.01, "max": 0.1, "label": "网格间距（如 0.03 = 3%）"},
        {"name": "volume_per_grid", "type": "int", "default": 100, "min": 100, "max": 10000, "label": "每格数量（股）"},
        {"name": "max_layers", "type": "int", "default": 5, "min": 1, "max": 20, "label": "最大加仓层数"},
    ]

    def on_init(self, data: dict) -> None:
        self._anchor: dict[str, float] = {}   # 最近成交基准价
        self._layers: dict[str, int] = {}     # 当前层数
        for sym in data:
            self._anchor[sym] = float(data[sym]["close"].iloc[0])
            self._layers[sym] = 0

    def on_bar(self, bar) -> None:
        sym = bar.symbol
        anchor = self._anchor.get(sym, bar.close)
        g = self.params["grid_pct"]
        pos = self.position(sym)
        holding = pos is not None and pos.volume > 0

        if bar.close <= anchor * (1 - g):
            if self._layers.get(sym, 0) < self.params["max_layers"]:
                order = self.buy(sym, volume=self.params["volume_per_grid"])
                if order is not None and order.status.value != "rejected":
                    self._anchor[sym] = bar.close
                    self._layers[sym] = self._layers.get(sym, 0) + 1
        elif bar.close >= anchor * (1 + g):
            if holding:
                sell_vol = min(self.params["volume_per_grid"],
                               self.position(sym).available_volume)
                order = self.sell(sym, volume=sell_vol)
                if order is not None and order.status.value != "rejected":
                    self._anchor[sym] = bar.close
                    self._layers[sym] = max(self._layers.get(sym, 0) - 1, 0)
