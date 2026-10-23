"""智能批量回测：内置策略 × 自选池标的全组合批量回测，记录胜率与收益率。

回测方法（预设）：
- 8 组策略配置：双均线×2、RSI 超跌、布林突破、海龟、波段、一夜持股、网格、多因子组合
- 默认区间 2021-01-01 至今（近 5 年，含牛熊周期）
- 同组合 24 小时内不重复跑（战绩表按 (策略,标的,参数,区间) 唯一）
"""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from app.data import database
from app.data.loader import load_daily
from app.data.models import AutoBacktest
from app.engine.backtester import BacktestConfig, BacktestEngine
from app.strategies import StrategyRegistry
from app.utils.logger import get_logger

logger = get_logger("engine.auto_backtest")

# 回测方法预设：策略名 + 参数
STRATEGY_PRESETS: list[tuple[str, dict]] = [
    ("dual_ma", {"fast": 5, "slow": 20}),
    ("dual_ma", {"fast": 10, "slow": 60}),
    ("rsi_reversal", {}),
    ("bollinger_breakout", {}),
    ("turtle_trend", {}),
    ("swing_trend", {}),
    ("overnight_hold", {}),
    ("grid_trading", {}),
]

DEFAULT_START = "2021-01-01"


def _run_one(strategy_name: str, params: dict, symbol: str, start: str, end: str) -> dict | None:
    """单组合回测，返回战绩行（数据不足返回 None）。"""
    df = load_daily(symbol, start=start, end=end, adjust="qfq")
    if df.empty or len(df) < 120:
        return None
    strategy_cls = StrategyRegistry.get(strategy_name)
    engine = BacktestEngine(
        {symbol: df}, strategy_cls, params=params or None,
        config=BacktestConfig(initial_capital=1_000_000),
    )
    result = engine.run()
    m = result.metrics
    return {
        "strategy": strategy_name,
        "symbol": symbol,
        "params_json": json.dumps(params, sort_keys=True),
        "start_date": start,
        "end_date": end or result.dates[-1],
        "win_rate": m.get("win_rate") or 0.0,
        "total_return": m["total_return"],
        "annualized_return": m["annualized_return"],
        "max_drawdown": m["max_drawdown"],
        "sharpe_ratio": m["sharpe_ratio"],
        "trade_count": int(m.get("trade_count") or 0),
    }


def _upsert_record(row: dict) -> None:
    with database.get_session() as session:
        existing = (
            session.query(AutoBacktest)
            .filter(
                AutoBacktest.strategy == row["strategy"],
                AutoBacktest.symbol == row["symbol"],
                AutoBacktest.params_json == row["params_json"],
                AutoBacktest.start_date == row["start_date"],
            )
            .first()
        )
        if existing is None:
            existing = AutoBacktest(**row)
            session.add(existing)
        else:
            for k, v in row.items():
                setattr(existing, k, v)
            existing.run_at = datetime.now()
        session.commit()


def reset_auto_backtests() -> int:
    """清空战绩表（数据修复后重跑用）。"""
    from app.data.models import AutoBacktest as AB

    with database.get_session() as session:
        n = session.query(AB).delete()
        session.commit()
    return n


def run_auto_backtests(symbols: list[str] | None = None, start: str = DEFAULT_START,
                       end: str = "", max_workers: int = 4, reset: bool = False) -> dict:
    """批量回测全部预设组合，返回统计。已有近 24 小时战绩的组合跳过。reset=True 先清空战绩。"""
    from app.config import get_settings

    if reset:
        reset_auto_backtests()
    symbols = symbols or [s for s in get_settings().watchlist_symbols if not s.startswith(("399", "000300"))]
    tasks = [(name, params, sym) for name, params in STRATEGY_PRESETS for sym in symbols]
    tasks += [("factor_rotation", {}, "组合")]  # 多因子组合回测（全池选股）

    with database.get_session() as session:
        cutoff = datetime.now() - timedelta(hours=24)
        fresh = set(
            session.query(AutoBacktest.strategy, AutoBacktest.symbol, AutoBacktest.params_json)
            .filter(AutoBacktest.run_at >= cutoff).all()
        )

    def run_task(task):
        name, params, sym = task
        key = (name, sym, json.dumps(params, sort_keys=True))
        if key in fresh:
            return ("skipped", name, sym)
        if sym == "组合":
            return ("portfolio", name, sym) if _run_portfolio(start, end) else ("failed", name, sym)
        row = _run_one(name, params, sym, start, end)
        if row is None:
            return ("no_data", name, sym)
        _upsert_record(row)
        return ("ran", name, sym)

    stats = {"ran": 0, "skipped": 0, "no_data": 0, "failed": 0, "portfolio": 0}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for outcome, _name, _sym in pool.map(run_task, tasks):
            stats[outcome] = stats.get(outcome, 0) + 1
    logger.info("智能批量回测完成: %s", stats)
    return stats


def _run_portfolio(start: str, end: str) -> bool:
    """多因子轮动组合回测（全自选池选股，等权分配）。"""
    from app.config import get_settings

    symbols = [s for s in get_settings().watchlist_symbols if not s.startswith(("399", "000300", "5", "15", "16", "18"))]
    data = {}
    for sym in symbols:
        df = load_daily(sym, start=start, end=end, adjust="qfq")
        if not df.empty:
            data[sym] = df
    if len(data) < 3:
        return False
    strategy_cls = StrategyRegistry.get("factor_rotation")
    engine = BacktestEngine(
        data, strategy_cls,
        config=BacktestConfig(initial_capital=1_000_000,
                              allocation="equal", target_positions=3),
    )
    result = engine.run()
    m = result.metrics
    _upsert_record({
        "strategy": "factor_rotation", "symbol": "组合",
        "params_json": json.dumps({"top_k": 3}, sort_keys=True),
        "start_date": start, "end_date": end or result.dates[-1],
        "win_rate": m.get("win_rate") or 0.0,
        "total_return": m["total_return"],
        "annualized_return": m["annualized_return"],
        "max_drawdown": m["max_drawdown"],
        "sharpe_ratio": m["sharpe_ratio"],
        "trade_count": int(m.get("trade_count") or 0),
    })
    return True


def list_auto_backtests(order_by: str = "total_return", limit: int = 200) -> list[dict]:
    """战绩表（含策略中文名与参数展示）。"""
    with database.get_session() as session:
        rows = session.query(AutoBacktest).all()
    metas = {m["name"]: m for m in StrategyRegistry.list_meta()}
    result = []
    for r in rows:
        result.append({
            "id": r.id,
            "strategy": r.strategy,
            "strategy_name": metas.get(r.strategy, {}).get("display_name", r.strategy),
            "symbol": r.symbol,
            "params": json.loads(r.params_json or "{}"),
            "start_date": r.start_date,
            "end_date": r.end_date,
            "win_rate": r.win_rate,
            "total_return": r.total_return,
            "annualized_return": r.annualized_return,
            "max_drawdown": r.max_drawdown,
            "sharpe_ratio": r.sharpe_ratio,
            "trade_count": r.trade_count,
            "run_at": r.run_at.strftime("%Y-%m-%d %H:%M"),
        })
    reverse = order_by not in ("max_drawdown",)
    result.sort(key=lambda x: (x.get(order_by) is None, x.get(order_by) or 0), reverse=reverse)
    return result[:limit]
