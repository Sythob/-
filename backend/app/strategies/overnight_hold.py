"""一夜持股策略：尾盘买入强势股，次日开盘卖出（隔夜溢价战术）。

引擎 fill_style="close"：信号在 t 收盘产生，按 t 收盘价 ± 滑点成交（尾盘买入的真实近似，
信息与成交同步、无未来函数）；买入同时挂次日开盘市价卖单——完美还原「隔夜持股」。

尾盘选股信号（全部参数化）：
- 当日涨幅在 [min_pct, max_pct]（太弱无惯性；逼近涨停买不进且次日风险大）
- 收盘位置靠上：(收盘-最低)/(最高-最低) ≥ close_position（上影线短，收盘强势）
- 量比放大 ≥ volume_ratio_min（资金进场）
- 收盘站上 trend_ma 均线（趋势保护）

注意：涨停封死日尾盘买不进（引擎涨跌停拒单）；卖出为次日开盘无条件市价单。
"""
from app.ml.indicators import ma
from app.strategies.base import Strategy


class OvernightHoldStrategy(Strategy):
    name = "overnight_hold"
    display_name = "一夜持股策略"
    description = "尾盘买入强势股（收盘成交），次日开盘无条件卖出。经典隔夜溢价战术"
    fill_style = "close"

    params = {
        "min_pct": 3.0,
        "max_pct": 7.0,
        "close_position": 0.7,
        "volume_ratio_min": 1.5,
        "trend_ma": 20,
        "position_pct": 0.2,
    }
    params_schema = [
        {"name": "min_pct", "type": "float", "default": 3.0, "min": 1.0, "max": 8.0, "label": "当日涨幅下限（%）"},
        {"name": "max_pct", "type": "float", "default": 7.0, "min": 4.0, "max": 9.5, "label": "当日涨幅上限（%）"},
        {"name": "close_position", "type": "float", "default": 0.7, "min": 0.5, "max": 1.0, "label": "收盘位置（收盘-最低)/(最高-最低)"},
        {"name": "volume_ratio_min", "type": "float", "default": 1.5, "min": 1.0, "max": 5.0, "label": "量比下限"},
        {"name": "trend_ma", "type": "int", "default": 20, "min": 5, "max": 60, "label": "趋势均线"},
        {"name": "position_pct", "type": "float", "default": 0.2, "min": 0.05, "max": 0.5, "label": "单笔仓位比例"},
    ]

    def on_init(self, data: dict) -> None:
        self._ctx: dict[str, dict] = {}
        for sym, df in data.items():
            close, volume = df["close"], df["volume"]
            prev_vol = volume.shift(1).rolling(5).mean()
            self._ctx[sym] = {
                "ma": dict(zip(df["trade_date"], ma(close, self.params["trend_ma"]))),
                "volume_ratio": dict(zip(df["trade_date"], volume / prev_vol)),
            }

    def on_bar(self, bar) -> None:
        sym = bar.symbol
        pos = self.position(sym)
        if pos is not None and pos.volume > 0:
            return  # 已持仓（卖出单在买入时已挂，次日开盘成交）
        p = self.params
        ma_val = self._ctx[sym]["ma"].get(bar.trade_date)
        vol_ratio = self._ctx[sym]["volume_ratio"].get(bar.trade_date)
        if ma_val is None or vol_ratio is None:
            return

        pct = bar.pct_change
        close_position = (bar.close - bar.low) / max(bar.high - bar.low, 1e-9)
        if not (p["min_pct"] <= pct <= p["max_pct"]):
            return
        if close_position < p["close_position"]:
            return
        if vol_ratio < p["volume_ratio_min"]:
            return
        if bar.close <= ma_val:
            return

        # 仓位：总资产 × position_pct ÷ 收盘价，取整手，现金封顶
        volume = int(self.engine.equity * p["position_pct"] / bar.close)
        volume = min(volume, int(self.engine.cash / bar.close)) if bar.close > 0 else 0
        volume = volume // self.engine.lot_size * self.engine.lot_size
        if volume <= 0:
            return
        self.buy(sym, volume=volume, fill_on_close=True)  # 尾盘单：当日收盘成交
        self.sell(sym, volume=volume)                     # 普通单：次日开盘卖出（隔夜）
