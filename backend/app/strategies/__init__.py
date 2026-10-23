"""策略注册：新策略实现后在此 import 并 register 即可被回测/UI 发现。"""
from app.strategies.base import StrategyRegistry
from app.strategies.bollinger_breakout import BollingerBreakoutStrategy
from app.strategies.dual_ma import DualMAStrategy
from app.strategies.factor_rotation import FactorRotationStrategy
from app.strategies.grid_trading import GridTradingStrategy
from app.strategies.ml_signal import MLSignalStrategy
from app.strategies.overnight_hold import OvernightHoldStrategy
from app.strategies.rsi_reversal import RSIReversalStrategy
from app.strategies.swing_trend import SwingTrendStrategy
from app.strategies.turtle_trend import TurtleTrendStrategy

StrategyRegistry.register(DualMAStrategy)
StrategyRegistry.register(RSIReversalStrategy)
StrategyRegistry.register(MLSignalStrategy)
StrategyRegistry.register(FactorRotationStrategy)
StrategyRegistry.register(GridTradingStrategy)
StrategyRegistry.register(TurtleTrendStrategy)
StrategyRegistry.register(BollingerBreakoutStrategy)
StrategyRegistry.register(SwingTrendStrategy)
StrategyRegistry.register(OvernightHoldStrategy)

__all__ = ["StrategyRegistry"]
