"""RSI 超跌反弹策略：RSI 低于阈值买入，高于阈值或持仓超期卖出。"""
from app.ml.indicators import rsi
from app.strategies.base import Strategy


class RSIReversalStrategy(Strategy):
    name = "rsi_reversal"
    display_name = "RSI 超跌反弹策略"
    description = "RSI 跌破买入阈值买入，回升至卖出阈值或持仓超期卖出。均值回归类策略。"
    params = {"period": 14, "buy_threshold": 30, "sell_threshold": 60, "max_hold_days": 10}
    params_schema = [
        {"name": "period", "type": "int", "default": 14, "min": 5, "max": 50, "label": "RSI 周期"},
        {"name": "buy_threshold", "type": "float", "default": 30, "min": 5, "max": 45, "label": "买入阈值（RSI 低于此值买入）"},
        {"name": "sell_threshold", "type": "float", "default": 60, "min": 50, "max": 80, "label": "卖出阈值（RSI 高于此值卖出）"},
        {"name": "max_hold_days", "type": "int", "default": 10, "min": 1, "max": 60, "label": "最长持仓天数"},
    ]

    def on_init(self, data: dict) -> None:
        self._rsi: dict[str, dict] = {}
        self._entry_date: dict[str, str] = {}
        for symbol, df in data.items():
            values = rsi(df["close"], self.params["period"])
            self._rsi[symbol] = dict(zip(df["trade_date"], values))

    def on_bar(self, bar) -> None:
        value = self._rsi[bar.symbol].get(bar.trade_date)
        if value is None:
            return
        pos = self.position(bar.symbol)
        holding = pos is not None and pos.volume > 0
        if not holding:
            if value < self.params["buy_threshold"]:
                self.buy(bar.symbol)
                self._entry_date[bar.symbol] = bar.trade_date
        else:
            if value > self.params["sell_threshold"] or self._hold_days_exceeded(bar.symbol, bar.trade_date):
                self.sell(bar.symbol)
                self._entry_date.pop(bar.symbol, None)

    def _hold_days_exceeded(self, symbol: str, today: str) -> bool:
        entry = self._entry_date.get(symbol, "")
        if not entry or entry not in self.engine.dates:
            return False
        return self.engine.dates.index(today) - self.engine.dates.index(entry) >= self.params["max_hold_days"]
