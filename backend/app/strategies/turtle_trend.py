"""海龟趋势策略：唐奇安通道突破入场，反向通道出场 + ATR 追踪止损（引擎化止损）。"""
from app.ml.indicators import atr
from app.strategies.base import Strategy


class TurtleTrendStrategy(Strategy):
    name = "turtle_trend"
    display_name = "海龟趋势策略"
    description = "价格突破前 N 日最高价买入，跌破前 M 日最低价卖出，持仓期挂 2 倍 ATR 追踪止损"
    params = {"entry_days": 20, "exit_days": 10, "atr_multiple": 2.0}
    params_schema = [
        {"name": "entry_days", "type": "int", "default": 20, "min": 10, "max": 60, "label": "入场通道（日）"},
        {"name": "exit_days", "type": "int", "default": 10, "min": 5, "max": 30, "label": "出场通道（日）"},
        {"name": "atr_multiple", "type": "float", "default": 2.0, "min": 1, "max": 4, "label": "ATR 止损倍数"},
    ]

    def on_init(self, data: dict) -> None:
        self._channel: dict[str, dict] = {}
        self._atr_value: dict[str, dict] = {}
        for sym, df in data.items():
            high_n = df["high"].rolling(self.params["entry_days"]).max().shift(1)
            low_m = df["low"].rolling(self.params["exit_days"]).min().shift(1)
            a = atr(df["high"], df["low"], df["close"], 14)
            self._channel[sym] = {
                "high_n": dict(zip(df["trade_date"], high_n)),
                "low_m": dict(zip(df["trade_date"], low_m)),
            }
            self._atr_value[sym] = dict(zip(df["trade_date"], a))

    def on_bar(self, bar) -> None:
        sym = bar.symbol
        high_n = self._channel[sym]["high_n"].get(bar.trade_date)
        low_m = self._channel[sym]["low_m"].get(bar.trade_date)
        if high_n is None or low_m is None:
            return
        pos = self.position(sym)
        holding = pos is not None and pos.volume > 0

        if not holding and bar.close > high_n:
            self.buy(sym)
            atr_now = self._atr_value[sym].get(bar.trade_date, 0.0)
            # 引擎化止损：持仓最高点回落 2×ATR 触发，次日开盘强制卖出
            self.set_stop(sym, "atr", atr_multiple=self.params["atr_multiple"], atr_value=atr_now)
        elif holding and bar.close < low_m:
            self.sell(sym)
            self.cancel_stop(sym)
