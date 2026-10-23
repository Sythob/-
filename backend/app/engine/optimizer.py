"""参数网格优化：遍历参数组合跑回测，返回排序结果 + 过拟合提示。

线程池并行（引擎与策略实例均独立，无共享状态）。
"""
import itertools
from concurrent.futures import ThreadPoolExecutor

from app.engine.backtester import BacktestConfig, BacktestEngine
from app.utils.logger import get_logger

logger = get_logger("engine.optimizer")

RESULT_METRICS = (
    "total_return", "annualized_return", "max_drawdown",
    "sharpe_ratio", "win_rate", "trade_count",
)


def grid_search(
    data: dict,
    strategy_cls,
    grid: dict[str, list],
    config: BacktestConfig | None = None,
    base_params: dict | None = None,
    metric: str = "total_return",
    max_workers: int = 4,
) -> dict:
    """遍历 grid 中每个参数组合回测，按 metric 排序（max_drawdown 升序，其余降序）。"""
    config = config or BacktestConfig()
    keys = list(grid)
    combos = [dict(zip(keys, values)) for values in itertools.product(*(grid[k] for k in keys))]
    if not combos:
        raise ValueError("参数网格为空")
    if len(combos) > 200:
        raise ValueError(f"参数组合过多（{len(combos)}），请缩小网格（≤200）")

    def run_one(combo: dict) -> dict:
        params = {**base_params, **combo} if base_params else dict(combo)
        engine = BacktestEngine(data, strategy_cls, params=params, config=config)
        result = engine.run()
        return {
            "params": params,
            "metrics": {k: result.metrics.get(k) for k in RESULT_METRICS},
        }

    with ThreadPoolExecutor(max_workers=min(max_workers, len(combos))) as pool:
        results = list(pool.map(run_one, combos))

    reverse = metric != "max_drawdown"
    results.sort(key=lambda r: (r["metrics"].get(metric) is None, r["metrics"].get(metric) or 0),
                 reverse=reverse)

    warning = ""
    if len(results) >= 2 and metric in ("total_return", "annualized_return"):
        best = results[0]["metrics"].get(metric) or 0
        second = results[1]["metrics"].get(metric) or 0
        if best > 0 and second > 0 and best > second * 1.5:
            warning = "最优参数大幅领先次优（>50%），在测试区间可能存在过拟合，建议缩短区间或交叉验证后使用"

    return {"metric": metric, "total": len(results), "results": results, "warning": warning}
