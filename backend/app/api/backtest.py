"""回测 API：提交后台运行，轮询状态取结果（指标/净值/成交落库）。"""
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.engine.auto_backtest import DEFAULT_START

from app.config import get_settings
from app.data import database, loader
from app.data.models import (
    BacktestMetric, BacktestRun, EquityCurve, TradeRecord,
)
from app.engine.backtester import BacktestConfig, BacktestEngine
from app.strategies import StrategyRegistry
from app.utils import tasks
from app.utils.logger import get_logger

router = APIRouter(tags=["backtest"])
logger = get_logger("api.backtest")


class BacktestRequest(BaseModel):
    strategy: str = "dual_ma"
    symbols: list[str] = []
    start: str = "2020-01-01"
    end: str = ""
    capital: float = 1_000_000
    params: dict = {}
    benchmark: bool = False
    # 仓位分配（多标的组合）
    allocation: str = "full"  # full/equal/fixed/kelly
    fixed_fraction: float = 0.3
    target_positions: int = 5
    kelly_win_rate: float = 0.5
    kelly_payoff: float = 1.0
    # 数据周期
    interval: str = "daily"  # daily / 60m
    # 风控
    max_loss_per_trade_pct: float = 0.0
    max_daily_loss_pct: float = 0.0


def _set_status(run_id: int, status: str, error: str = "") -> None:
    with database.get_session() as session:
        row = session.get(BacktestRun, run_id)
        if row:
            row.status = status
            row.error = error
            session.commit()


def _run_backtest_task(run_id: int, req: BacktestRequest) -> None:
    _set_status(run_id, "running")
    try:
        data = {}
        for sym in req.symbols:
            if req.interval == "60m":
                df = loader.load_minute(sym, interval="60m", start=req.start, end=req.end)
            else:
                df = loader.load_daily(sym, start=req.start, end=req.end, adjust="qfq")
            if not df.empty:
                data[sym] = df
        if not data:
            raise ValueError("所有标的均无行情数据，请先同步数据")

        benchmark = None
        if req.benchmark and req.interval == "daily":
            benchmark = loader.load_daily("000300", start=req.start, end=req.end, adjust="qfq")

        engine = BacktestEngine(
            data, StrategyRegistry.get(req.strategy), params=req.params,
            config=BacktestConfig(
                initial_capital=req.capital,
                allocation=req.allocation,
                fixed_fraction=req.fixed_fraction,
                target_positions=req.target_positions,
                kelly_win_rate=req.kelly_win_rate,
                kelly_payoff=req.kelly_payoff,
                max_loss_per_trade_pct=req.max_loss_per_trade_pct,
                max_daily_loss_pct=req.max_daily_loss_pct,
            ),
            benchmark=benchmark,
        )
        result = engine.run()

        with database.get_session() as session:
            for name, value in result.metrics.items():
                if isinstance(value, (int, float)):
                    session.add(BacktestMetric(run_id=run_id, metric_name=name, value=float(value)))
            for e in result.dates:
                session.add(EquityCurve(
                    scope_type="backtest", scope_id=run_id, date=e,
                    equity=result.equity[result.dates.index(e)],
                    cash=result.cash[result.dates.index(e)],
                    return_rate=result.return_rates[result.dates.index(e)],
                ))
            for t in result.trades:
                session.add(TradeRecord(
                    run_id=run_id, symbol=t.symbol, direction=t.direction.value,
                    price=t.price, volume=t.volume, trade_time=t.trade_time,
                    commission=t.commission, slippage=t.slippage, pnl=t.pnl,
                ))
            session.commit()
        _set_status(run_id, "done")
    except Exception as exc:  # noqa: BLE001 后台任务必须兜底
        logger.exception("回测失败 run_id=%s", run_id)
        _set_status(run_id, "failed", str(exc))


@router.post("/backtest/run")
def run_backtest(req: BacktestRequest):
    symbols = req.symbols or get_settings().watchlist_symbols
    if not symbols:
        raise HTTPException(400, "请提供标的列表")
    try:
        StrategyRegistry.get(req.strategy)
    except KeyError:
        raise HTTPException(400, f"策略不存在: {req.strategy}") from None

    with database.get_session() as session:
        row = BacktestRun(
            strategy_name=req.strategy,
            params_json=json.dumps(req.params),
            universe_json=json.dumps(symbols),
            start_date=req.start, end_date=req.end,
            capital=req.capital, status="pending",
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        run_id = row.id

    tasks.submit(_run_backtest_task, run_id, req)
    return {"run_id": run_id, "status": "pending"}


class AutoBacktestRequest(BaseModel):
    reset: bool = False


@router.post("/backtest/auto")
def run_auto(req: AutoBacktestRequest | None = None):
    """智能批量回测：全部策略预设 × 自选池标的（约 2~5 分钟，后台运行）。

    请求体可选：{\"reset\": true} 表示清空旧战绩重跑。
    """
    from app.engine.auto_backtest import run_auto_backtests

    reset = req.reset if req else False
    task_id = tasks.submit(run_auto_backtests, None, DEFAULT_START, "", 4, reset)
    return {"task_id": task_id}


@router.get("/backtest/auto")
def list_auto(order_by: str = "total_return", limit: int = 200):
    """智能回测战绩表（策略×标的，含胜率/收益率）。"""
    from app.engine.auto_backtest import list_auto_backtests

    return list_auto_backtests(order_by=order_by, limit=limit)


@router.get("/backtest/runs")
def list_runs():
    with database.get_session() as session:
        rows = session.query(BacktestRun).order_by(BacktestRun.id.desc()).limit(50).all()
        return [
            {"id": r.id, "strategy_name": r.strategy_name, "universe": json.loads(r.universe_json or "[]"),
             "start_date": r.start_date, "end_date": r.end_date, "capital": r.capital,
             "status": r.status, "error": r.error, "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S")}
            for r in rows
        ]


@router.get("/backtest/runs/{run_id}/report")
def get_run_report(run_id: int):
    """生成/返回自包含 HTML 绩效报告（浏览器直接打开，含月度热力图等）。"""
    from fastapi.responses import HTMLResponse

    from app.engine.report import generate_report_html

    detail = get_run(run_id)
    if detail["status"] != "done":
        raise HTTPException(400, f"回测未完成（当前状态 {detail['status']}），无法生成报告")
    html = generate_report_html(detail)
    return HTMLResponse(content=html)


class OptimizeRequest(BaseModel):
    strategy: str = "dual_ma"
    symbols: list[str] = []
    start: str = "2020-01-01"
    end: str = ""
    capital: float = 1_000_000
    base_params: dict = {}
    grid: dict = {}
    metric: str = "total_return"
    allocation: str = "full"


@router.post("/backtest/optimize")
def optimize(req: OptimizeRequest):
    """参数网格优化：后台跑全部组合，返回 task_id（轮询 /api/system/tasks/{id} 取结果）。"""
    if not req.grid:
        raise HTTPException(400, "请提供参数网格 grid（如 {\"fast\": [5, 10], \"slow\": [20, 60]}）")
    symbols = req.symbols or get_settings().watchlist_symbols
    try:
        strategy_cls = StrategyRegistry.get(req.strategy)
    except KeyError:
        raise HTTPException(400, f"策略不存在: {req.strategy}") from None

    def _run_optimize():
        data = {}
        for sym in symbols:
            df = loader.load_daily(sym, start=req.start, end=req.end, adjust="qfq")
            if not df.empty:
                data[sym] = df
        if not data:
            raise ValueError("所有标的均无行情数据，请先同步数据")
        from app.engine.optimizer import grid_search

        return grid_search(
            data, strategy_cls, req.grid,
            config=BacktestConfig(initial_capital=req.capital, allocation=req.allocation),
            base_params=req.base_params, metric=req.metric,
        )

    task_id = tasks.submit(_run_optimize)
    return {"task_id": task_id}


@router.get("/backtest/runs/{run_id}")
def get_run(run_id: int):
    with database.get_session() as session:
        row = session.get(BacktestRun, run_id)
        if row is None:
            raise HTTPException(404, "回测记录不存在")
        metrics = {
            m.metric_name: m.value
            for m in session.query(BacktestMetric).filter(BacktestMetric.run_id == run_id).all()
        }
        equity = [
            {"date": e.date, "equity": e.equity, "cash": e.cash, "return_rate": e.return_rate}
            for e in session.query(EquityCurve)
            .filter(EquityCurve.scope_type == "backtest", EquityCurve.scope_id == run_id)
            .order_by(EquityCurve.date).all()
        ]
        trades = [
            {"symbol": t.symbol, "direction": t.direction, "price": t.price,
             "volume": t.volume, "trade_time": t.trade_time,
             "commission": t.commission, "pnl": t.pnl}
            for t in session.query(TradeRecord).filter(TradeRecord.run_id == run_id)
            .order_by(TradeRecord.trade_time).all()
        ]
        return {
            "id": row.id, "strategy_name": row.strategy_name,
            "params": json.loads(row.params_json or "{}"),
            "universe": json.loads(row.universe_json or "[]"),
            "start_date": row.start_date, "end_date": row.end_date,
            "capital": row.capital, "status": row.status, "error": row.error,
            "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "metrics": metrics, "equity": equity, "trades": trades,
        }
