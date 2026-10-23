# 策略开发指南

## Strategy 基类契约

```python
from app.strategies.base import Strategy

class MyStrategy(Strategy):
    name = "my_strategy"            # 英文小写+下划线，唯一
    display_name = "我的策略"        # UI 显示名
    description = "一句话说明"
    interval = "daily"              # daily / hour
    params = {"fast": 5}            # 默认参数
    params_schema = [               # UI 自动生成表单
        {"name": "fast", "type": "int", "default": 5, "min": 2, "max": 60, "label": "快线周期"},
    ]

    def on_init(self, data):        # data = {symbol: DataFrame(交易日升序)}
        # 预热：预计算指标/信号，on_bar 里只查表不重算

    def on_bar(self, bar):          # 每交易日每标的调用一次
        # bar: symbol/trade_date/open/high/low/close/volume/pct_change
        # 信号只基于 bar 的收盘信息；订单次日开盘成交（引擎保证）
        if 条件:
            self.buy(symbol, volume=100)          # volume=None → 市价全仓
        else:
            self.sell(symbol, volume=None)        # volume=None → 全部可卖

    def on_stop(self):              # 可选
        pass
```

可用的便捷接口：`self.buy/sell(symbol, volume, price_type, price)`、
`self.position(symbol)`、`self.cash`、`self.equity`。

可用指标库 `app.ml.indicators`：ma, ema, rsi, macd, kdj, boll, atr, roc, cci, wr, volume_ratio, volume_ma_ratio。

## 回测引擎规则（策略必须了解）

1. **信号 t 收盘产生 → t+1 开盘价 ± 滑点成交**（防未来函数铁律）
2. **T+1**：当日买入收盘结算后解锁，次日才可卖；当日买当日卖必被拒单
3. **整手**：买入自动向下取整到 100 股；卖出非整手直接拒单
4. **涨跌停**：涨停日买单不成交、跌停日卖单不成交（阈值主板 10%，ST/创业板差异见 data-notes）
5. **市价单不隔夜**：当日开盘未成交即撤销；限价单最多挂 5 个交易日
6. **停牌**：无 bar 的日期跳过，持仓按最后收盘价估值
7. **费用**：佣金 0.025%（最低 5 元）、卖出印花税 0.05%、过户费 0.001%、滑点 0.01 元/股（可配）

## 自然语言生成策略

策略页输入中文描述，系统执行：LLM 生成代码 → import 白名单与语法校验 →
60 根样本 bar 空跑 → 通过后落盘 `backend/app/strategies/generated/` 并入库。

约束：只能 import pandas / app.strategies.base / app.ml.indicators；
禁用 eval/exec/open 等危险函数；仅在回测/模拟环境运行，不接触真实资金。

## 机器学习信号

1. `scripts/train_model.py` 训练（特征见 `app/ml/features.py`，约 30 维技术特征，
   严格防未来函数；Walk-Forward 各折 AUC 报告落库）
2. 策略选「LightGBM 信号策略」，`model_name` 填模型名
3. 阈值含义：模型输出 = 次日上涨概率；> buy_threshold 买入，< sell_threshold 卖出

## 常见坑

- **未来函数**：任何用到未来数据的地方都会让回测虚高。特征只用 rolling/shift；
  信号产生与成交之间隔一个交易日；验证集必须严格晚于训练集。
- **过拟合**：参数越多的策略越容易过拟合。多看 Walk-Forward 样本外表现与回撤，
  别只看总收益。
- **复权**：默认前复权（最新因子法）。同一策略回测与模拟盘都用 qfq，口径一致。
