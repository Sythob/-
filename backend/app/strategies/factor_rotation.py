"""多因子横截面轮动策略（类 Qlib 选股思路的简化实现）。

对标的池内所有股票计算多因子得分（动量/反转/低波/质量/量价，z-score 标准化后加权），
每隔 rebalance_days 个交易日调仓：卖出掉出名单的持仓，买入得分最高的 top_k 只。
建议配合回测页「仓位分配 = 等权」使用（target_positions 设为 top_k）。
"""
import numpy as np

from app.strategies.base import Strategy


def _zscore(series):
    return (series - series.mean()) / (series.std() + 1e-9)


class FactorRotationStrategy(Strategy):
    name = "factor_rotation"
    display_name = "多因子轮动策略"
    description = "动量/反转/低波/质量/量价五因子打分，定期调仓持有得分最高的 K 只（横截面选股）。建议仓位分配选「等权」"
    params = {
        "top_k": 3,
        "rebalance_days": 20,
        "momentum_weight": 0.3,
        "reversal_weight": 0.1,
        "volatility_weight": 0.2,
        "quality_weight": 0.2,
        "volume_weight": 0.2,
    }
    params_schema = [
        {"name": "top_k", "type": "int", "default": 3, "min": 1, "max": 10, "label": "持有数量 K"},
        {"name": "rebalance_days", "type": "int", "default": 20, "min": 5, "max": 60, "label": "调仓周期（交易日）"},
        {"name": "momentum_weight", "type": "float", "default": 0.3, "min": 0, "max": 1, "label": "动量权重"},
        {"name": "reversal_weight", "type": "float", "default": 0.1, "min": 0, "max": 1, "label": "反转权重"},
        {"name": "volatility_weight", "type": "float", "default": 0.2, "min": 0, "max": 1, "label": "低波权重"},
        {"name": "quality_weight", "type": "float", "default": 0.2, "min": 0, "max": 1, "label": "质量权重"},
        {"name": "volume_weight", "type": "float", "default": 0.2, "min": 0, "max": 1, "label": "量价权重"},
    ]

    def on_init(self, data: dict) -> None:
        p = self.params
        self._scores: dict[str, dict] = {}
        self._last_rebalance_date = ""
        for sym, df in data.items():
            close = df["close"]
            volume = df["volume"] if "volume" in df.columns else close * 1e6
            momentum = close.pct_change(20)
            reversal = -close.pct_change(5)
            low_vol = -close.pct_change().rolling(20).std()
            quality = close.pct_change().rolling(20).mean() / (close.pct_change().rolling(20).std() + 1e-9)
            volume_signal = volume.pct_change(5).rolling(5).mean()
            weights = [p["momentum_weight"], p["reversal_weight"], p["volatility_weight"],
                       p["quality_weight"], p["volume_weight"]]
            total_w = sum(weights) or 1.0
            score = (
                _zscore(momentum) * weights[0] + _zscore(reversal) * weights[1]
                + _zscore(low_vol) * weights[2] + _zscore(quality) * weights[3]
                + _zscore(volume_signal) * weights[4]
            ) / total_w
            self._scores[sym] = dict(zip(df["trade_date"], score))

    def on_bar(self, bar) -> None:
        date = bar.trade_date
        if date == self._last_rebalance_date:
            return  # 同日多标的只执行一次调仓
        dates = self.engine.dates
        if date not in dates:
            return
        idx = dates.index(date)
        if idx % self.params["rebalance_days"] != 0:
            return
        self._last_rebalance_date = date

        cross = {
            sym: scores.get(date)
            for sym, scores in self._scores.items()
            if scores.get(date) is not None and not np.isnan(scores.get(date))
        }
        if not cross:
            return
        top_k = sorted(cross, key=cross.get, reverse=True)[: self.params["top_k"]]

        # 卖出掉出名单的持仓（t+1 开盘成交）
        for sym, pos in list(self.engine.portfolio.positions.items()):
            if sym not in top_k and pos.volume > 0:
                self.sell(sym)
        # 买入名单中未持仓的（等权分配由引擎仓位模式处理）
        for sym in top_k:
            pos = self.position(sym)
            if pos is None or pos.volume <= 0:
                self.buy(sym)
