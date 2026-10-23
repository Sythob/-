"""命令行回测。

用法（在 backend 目录下）：
  .venv\\Scripts\\python scripts/run_backtest.py --strategy dual_ma --symbols 600519 --start 2020-01-01 --end 2023-12-31
  .venv\\Scripts\\python scripts/run_backtest.py --strategy rsi_reversal --symbols 600519,000001 --capital 1000000
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data import database
from app.data.loader import load_daily
from app.engine.backtester import BacktestConfig, BacktestEngine
from app.strategies import StrategyRegistry


def main():
    parser = argparse.ArgumentParser(description="唯我量化命令行回测")
    parser.add_argument("--strategy", default="dual_ma", help=f"策略名：{list(StrategyRegistry.all())}")
    parser.add_argument("--symbols", default="600519", help="逗号分隔股票代码")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2023-12-31")
    parser.add_argument("--capital", type=float, default=1_000_000.0)
    parser.add_argument("--params", default="", help='策略参数 JSON，如 {"fast": 10, "slow": 60}')
    parser.add_argument("--benchmark", action="store_true", help="加载沪深300 做同期对比")
    args = parser.parse_args()

    database.init_db()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    params = json.loads(args.params) if args.params else None

    data = {}
    for symbol in symbols:
        df = load_daily(symbol, start=args.start, end=args.end, adjust="qfq")
        if df.empty:
            print(f"[警告] {symbol} 无数据，跳过")
            continue
        data[symbol] = df
    if not data:
        print("无可用行情数据，请先运行 scripts/sync_data.py")
        sys.exit(1)

    benchmark = None
    if args.benchmark:
        benchmark = load_daily("000300", start=args.start, end=args.end, adjust="qfq")

    strategy_cls = StrategyRegistry.get(args.strategy)
    engine = BacktestEngine(
        data, strategy_cls, params=params,
        config=BacktestConfig(initial_capital=args.capital),
        benchmark=benchmark,
    )
    result = engine.run()

    print(f"\n===== 回测结果：{strategy_cls.display_name} 参数={engine.strategy.params} =====")
    print(f"标的: {symbols}  区间: {args.start} ~ {args.end}")
    m = result.metrics
    print(f"初始资金: {m['initial_capital']:,.0f}  期末资产: {m['final_equity']:,.0f}")
    print(f"总收益率: {m['total_return']:+.2%}  年化收益率: {m['annualized_return']:+.2%}")
    print(f"最大回撤: {m['max_drawdown']:.2%}  夏普比率: {m['sharpe_ratio']:.2f}")
    print(f"交易次数: {m['trade_count']}  胜率: {m['win_rate']:.1%}  盈亏比: {m['profit_loss_ratio']:.2f}")
    print(f"平均持仓天数: {m['avg_holding_days']:.1f}  换手率: {m['turnover_rate']:.2f}")
    if "benchmark_return" in m:
        print(f"沪深300 同期: {m['benchmark_return']:+.2%}  超额收益: {m['excess_return']:+.2%}")


if __name__ == "__main__":
    main()
