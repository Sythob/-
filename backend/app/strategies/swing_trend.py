"""波段策略模板：趋势过滤 + 双买点 + 三选一止损 + 风险公式仓位。

设计（可全部参数化）：
- 趋势过滤：收盘在 N 日线上方且均线向上（默认 60 日，大趋势保护）
- 买点二选一：breakout（放量突破前 N 日最高）或 pullback（回踩均线后收阳拉起）
- 止损三选一：trailing（最高点回落 X%）/ atr（X 倍 ATR 追踪，引擎化止损）
  / ma（收盘跌破 N 日均线离场）
- 仓位：单笔风险 = 总资金 × risk_pct（默认 2%），股数 = 风险额 / 每股风险
- 卖出：止损触发（次日开盘成交）或收盘跌破 exit_ma

与引擎铁律一致：信号 t 收盘产生 → t+1 开盘成交，无未来函数。
"""
from app.ml.indicators import atr, ma
from app.strategies.base import Strategy


class SwingTrendStrategy(Strategy):
    name = "swing_trend"
    display_name = "波段趋势策略"
    description = "趋势过滤+突破/回调双买点+移动止损，仓位按 2% 风险公式自动计算。波段交易模板"
    params = {
        "trend_ma": 60,
        "entry_mode": "breakout",   # breakout / pullback
        "breakout_window": 20,
        "pullback_ma": 20,
        "stop_mode": "trailing",    # trailing / atr / ma
        "trailing_pct": 0.08,
        "atr_multiple": 2.0,
        "stop_ma": 20,
        "exit_ma": 20,
        "risk_pct": 0.02,           # 单笔风险占总资金比例
    }
    params_schema = [
        {"name": "trend_ma", "type": "int", "default": 60, "min": 20, "max": 250, "label": "趋势过滤均线"},
        {"name": "entry_mode", "type": "str", "default": "breakout", "label": "买点模式（breakout=突破 / pullback=回调）"},
        {"name": "breakout_window", "type": "int", "default": 20, "min": 5, "max": 60, "label": "突破窗口（日）"},
        {"name": "pullback_ma", "type": "int", "default": 20, "min": 5, "max": 60, "label": "回调买点均线"},
        {"name": "stop_mode", "type": "str", "default": "trailing", "label": "止损模式（trailing / atr / ma）"},
        {"name": "trailing_pct", "type": "float", "default": 0.08, "min": 0.03, "max": 0.2, "label": "移动止损回落比例"},
        {"name": "atr_multiple", "type": "float", "default": 2.0, "min": 1.0, "max": 4.0, "label": "ATR 止损倍数"},
        {"name": "stop_ma", "type": "int", "default": 20, "min": 5, "max": 60, "label": "均线止损周期"},
        {"name": "exit_ma", "type": "int", "default": 20, "min": 5, "max": 60, "label": "离场均线"},
        {"name": "risk_pct", "type": "float", "default": 0.02, "min": 0.005, "max": 0.1, "label": "单笔风险比例（如 0.02=2%）"},
    ]

    def on_init(self, data: dict) -> None:
        self._ctx: dict[str, dict] = {}
        p = self.params
        for sym, df in data.items():
            close = df["close"]
            trend = ma(close, p["trend_ma"])
            ctx = {
                "trend": dict(zip(df["trade_date"], trend)),
                "trend_up": dict(zip(df["trade_date"], trend > trend.shift(5))),
                "break_high": dict(zip(df["trade_date"],
                                       df["high"].rolling(p["breakout_window"]).max().shift(1))),
                "pull_ma": dict(zip(df["trade_date"], ma(close, p["pullback_ma"]))),
                "stop_ma": dict(zip(df["trade_date"], ma(close, p["stop_ma"]))),
                "exit_ma": dict(zip(df["trade_date"], ma(close, p["exit_ma"]))),
                "atr": dict(zip(df["trade_date"], atr(df["high"], df["low"], close, 14))),
                "open": dict(zip(df["trade_date"], df["open"])),
                "close": dict(zip(df["trade_date"], close)),
            }
            self._ctx[sym] = ctx

    def on_bar(self, bar) -> None:
        sym = bar.symbol
        ctx = self._ctx.get(sym)
        if ctx is None:
            return
        close = bar.close
        pos = self.position(sym)
        holding = pos is not None and pos.volume > 0
        p = self.params

        if not holding:
            if self._entry_signal(ctx, bar):
                volume = self._position_size(ctx, bar)
                if volume >= self.engine.lot_size:
                    self.buy(sym, volume=volume)
                    self._register_stop(ctx, bar)
        else:
            exit_ma = ctx["exit_ma"].get(bar.trade_date)
            if exit_ma is not None and close < exit_ma:
                self.sell(sym)
                self.cancel_stop(sym)

    # ---------------------------------------------------------------- 信号与仓位

    def _entry_signal(self, ctx: dict, bar) -> bool:
        """趋势过滤 + 买点模式判断。"""
        p = self.params
        trend = ctx["trend"].get(bar.trade_date)
        trend_up = ctx["trend_up"].get(bar.trade_date)
        if trend is None or trend_up is None or not trend_up or bar.close <= trend:
            return False
        if p["entry_mode"] == "pullback":
            pull_ma = ctx["pull_ma"].get(bar.trade_date)
            if pull_ma is None:
                return False
            # 回踩：今日低点触及均线附近（2% 内）且收阳
            touched = bar.low <= pull_ma * 1.02
            return touched and bar.close > bar.open
        # breakout：放量突破前 N 日最高
        break_high = ctx["break_high"].get(bar.trade_date)
        return break_high is not None and bar.close > break_high

    def _stop_price(self, ctx: dict, bar) -> float:
        """按止损模式计算止损价（买入信号日）。"""
        p = self.params
        if p["stop_mode"] == "atr":
            atr_val = ctx["atr"].get(bar.trade_date) or 0.0
            return bar.close - p["atr_multiple"] * atr_val
        if p["stop_mode"] == "ma":
            ma_val = ctx["stop_ma"].get(bar.trade_date)
            return ma_val if ma_val else bar.close * 0.95
        return bar.close * (1 - p["trailing_pct"])  # trailing

    def _position_size(self, ctx: dict, bar) -> int:
        """风险公式仓位：股数 = 总资金 × risk_pct ÷ 每股风险，取整手，现金封顶。

        止损距离很近时（如均线止损）风险公式会算出超大仓位，必须按可用现金封顶。
        """
        stop_price = self._stop_price(ctx, bar)
        risk_per_share = max(bar.close - stop_price, bar.close * 0.01)
        budget = self.engine.equity * self.params["risk_pct"]
        volume = int(budget / risk_per_share)
        affordable = int(self.engine.cash / bar.close) if bar.close > 0 else 0
        volume = min(volume, affordable)
        volume = volume // self.engine.lot_size * self.engine.lot_size
        return volume

    def _register_stop(self, ctx: dict, bar) -> None:
        """按止损模式挂引擎化止损（触发后次日开盘强制卖出）。"""
        p = self.params
        if p["stop_mode"] == "trailing":
            self.set_stop(bar.symbol, "trailing", trailing_pct=p["trailing_pct"])
        elif p["stop_mode"] == "atr":
            self.set_stop(bar.symbol, "atr", atr_multiple=p["atr_multiple"],
                          atr_value=ctx["atr"].get(bar.trade_date) or 0.0)
        # ma 模式不走引擎止损，on_bar 里收盘破位卖出
