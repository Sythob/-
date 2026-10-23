"""LightGBM 信号策略：用已训练模型对 bar t 的特征打分（特征只用 ≤t 数据），
概率高于买入阈值买入，低于卖出阈值或持仓超期卖出。演示 ML 闭环。
"""
from app.ml.features import build_features
from app.ml.persistence import load_model_by_name
from app.strategies.base import Strategy


class MLSignalStrategy(Strategy):
    name = "ml_signal"
    display_name = "LightGBM 信号策略"
    description = "用已训练的 LightGBM 模型对每日特征打分（预测次日上涨概率），阈值触发交易。需先在 ML 页面训练模型。"
    params = {"model_name": "", "buy_threshold": 0.55, "sell_threshold": 0.45, "max_hold_days": 20}
    params_schema = [
        {"name": "model_name", "type": "str", "default": "", "label": "模型名称"},
        {"name": "buy_threshold", "type": "float", "default": 0.55, "min": 0.5, "max": 0.9, "label": "买入概率阈值"},
        {"name": "sell_threshold", "type": "float", "default": 0.45, "min": 0.1, "max": 0.5, "label": "卖出概率阈值"},
        {"name": "max_hold_days", "type": "int", "default": 20, "min": 1, "max": 60, "label": "最长持仓天数"},
    ]

    def on_init(self, data: dict) -> None:
        self._probs: dict[str, dict] = {}
        self._entry: dict[str, str] = {}
        if not self.params["model_name"]:
            return  # 未配置模型：策略空转，不产生交易
        loaded = load_model_by_name(self.params["model_name"])
        if loaded is None:
            raise ValueError(f"模型不存在: {self.params['model_name']}")
        model, _row = loaded
        self._model = model
        for symbol, df in data.items():
            feats = build_features(df)
            feats = feats.dropna(subset=self._model.feature_names)
            if feats.empty:
                continue
            proba = self._model.predict_proba(feats)
            self._probs[symbol] = dict(zip(feats["trade_date"], proba))

    def on_bar(self, bar) -> None:
        prob = self._probs.get(bar.symbol, {}).get(bar.trade_date)
        if prob is None:
            return
        pos = self.position(bar.symbol)
        holding = pos is not None and pos.volume > 0
        if not holding:
            if prob > self.params["buy_threshold"]:
                self.buy(bar.symbol)
                self._entry[bar.symbol] = bar.trade_date
        else:
            if prob < self.params["sell_threshold"] or self._hold_days_exceeded(bar.symbol, bar.trade_date):
                self.sell(bar.symbol)
                self._entry.pop(bar.symbol, None)

    def _hold_days_exceeded(self, symbol: str, today: str) -> bool:
        entry = self._entry.get(symbol, "")
        if not entry or entry not in self.engine.dates:
            return False
        return self.engine.dates.index(today) - self.engine.dates.index(entry) >= self.params["max_hold_days"]
