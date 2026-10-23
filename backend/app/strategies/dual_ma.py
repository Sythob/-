"""双均线策略：快线上穿慢线（金叉）全仓买入，下穿（死叉）清仓。教学基线策略。"""
from app.strategies.base import Strategy


class DualMAStrategy(Strategy):
    name = "dual_ma"
    display_name = "双均线策略"
    description = "MA快线上穿慢线（金叉）全仓买入，下穿（死叉）清仓。经典趋势跟踪教学策略。"
    params = {"fast": 5, "slow": 20}
    params_schema = [
        {"name": "fast", "type": "int", "default": 5, "min": 2, "max": 60, "label": "快线周期"},
        {"name": "slow", "type": "int", "default": 20, "min": 10, "max": 250, "label": "慢线周期"},
    ]

    def on_init(self, data: dict) -> None:
        self._signals: dict[str, dict] = {}
        for symbol, df in data.items():
            df = df.copy()
            df["ma_fast"] = df["close"].rolling(self.params["fast"]).mean()
            df["ma_slow"] = df["close"].rolling(self.params["slow"]).mean()
            df["golden"] = (df["ma_fast"] > df["ma_slow"]) & (
                df["ma_fast"].shift(1) <= df["ma_slow"].shift(1)
            )
            df["dead"] = (df["ma_fast"] < df["ma_slow"]) & (
                df["ma_fast"].shift(1) >= df["ma_slow"].shift(1)
            )
            self._signals[symbol] = df.set_index("trade_date")

    def on_bar(self, bar) -> None:
        row = self._signals[bar.symbol].loc[bar.trade_date]
        if row["golden"] and self.position(bar.symbol) is None:
            self.buy(bar.symbol)  # 市价全仓
        elif row["dead"] and self.position(bar.symbol) is not None:
            self.sell(bar.symbol)  # 全部可卖
