"""策略基类与注册表。

契约：
- params：默认参数；params_schema 描述每个参数（含中文 label），UI 据此自动生成表单；
- on_init(data)：预热（预计算指标列等），data = {symbol: DataFrame(trade_date 升序)}；
- on_bar(bar)：每个交易日每标的调用一次，用 bar 的 t 收盘信息产生信号；
  调用 engine.buy/sell 下的订单在 t+1 开盘成交（防未来函数铁律）；
- interval：策略周期（daily / hour）。
"""
from abc import ABC, abstractmethod

from app.engine.objects import BarData


class Strategy(ABC):
    name: str = ""
    display_name: str = ""
    description: str = ""
    params: dict = {}
    params_schema: list[dict] = []
    interval: str = "daily"
    # 成交模式：next_open（默认，信号 t → t+1 开盘成交，防未来函数铁律）
    #          close（信号与成交同为 t 收盘，尾盘买入近似；仅限确需尾盘成交的策略声明）
    fill_style: str = "next_open"

    def __init__(self, engine, params: dict | None = None):
        self.engine = engine
        self.params = {**self.params, **(params or {})}

    # ---- 生命周期（子类覆写）----

    def on_init(self, data: dict) -> None:
        pass

    @abstractmethod
    def on_bar(self, bar: BarData) -> None:
        pass

    def on_stop(self) -> None:
        pass

    # ---- 交易接口 ----

    def buy(self, symbol, volume=None, price_type="market", price=0.0, fill_on_close=False):
        return self.engine.buy(symbol, volume=volume, price_type=price_type, price=price,
                               fill_on_close=fill_on_close)

    def sell(self, symbol, volume=None, price_type="market", price=0.0, fill_on_close=False):
        return self.engine.sell(symbol, volume=volume, price_type=price_type, price=price,
                                fill_on_close=fill_on_close)

    def position(self, symbol):
        return self.engine.position(symbol)

    def set_stop(self, symbol, stop_type="trailing", *, trigger_price=0.0,
                 atr_multiple=2.0, atr_value=0.0, trailing_pct=0.08):
        """注册止损/止盈：fixed/take_profit/atr/trailing/max_drawdown。"""
        return self.engine.set_stop(
            symbol, stop_type, trigger_price=trigger_price, atr_multiple=atr_multiple,
            atr_value=atr_value, trailing_pct=trailing_pct,
        )

    def cancel_stop(self, symbol):
        return self.engine.cancel_stop(symbol)

    @property
    def cash(self) -> float:
        return self.engine.cash

    @property
    def equity(self) -> float:
        return self.engine.equity


class StrategyRegistry:
    _strategies: dict[str, type[Strategy]] = {}

    @classmethod
    def register(cls, strategy_cls: type[Strategy]) -> None:
        if not strategy_cls.name:
            raise ValueError(f"策略 {strategy_cls} 缺少 name")
        cls._strategies[strategy_cls.name] = strategy_cls

    @classmethod
    def get(cls, name: str) -> type[Strategy]:
        if name in cls._strategies:
            return cls._strategies[name]
        # 动态加载自然语言生成的策略（strategies/generated/<name>.py）
        import importlib

        try:
            module = importlib.import_module(f"app.strategies.generated.{name}")
        except ImportError:
            raise KeyError(f"策略不存在: {name}，可用: {list(cls._strategies)}") from None
        for value in vars(module).values():
            if isinstance(value, type) and issubclass(value, Strategy) and value is not Strategy:
                if getattr(value, "name", "") == name:
                    return value
        raise KeyError(f"策略模块中未找到类: {name}")

    @classmethod
    def all(cls) -> dict[str, type[Strategy]]:
        return dict(cls._strategies)

    @classmethod
    def list_meta(cls) -> list[dict]:
        """策略元信息（UI 列表与表单用）。"""
        metas = []
        for name, s in cls._strategies.items():
            metas.append({
                "name": name,
                "display_name": s.display_name or name,
                "description": s.description,
                "interval": s.interval,
                "params": s.params,
                "params_schema": s.params_schema,
            })
        return metas
