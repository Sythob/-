"""绩效指标纯函数：年化收益、最大回撤、夏普、胜率、盈亏比、换手率等。"""
import math

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 250


def total_return(equity: pd.Series, initial_capital: float) -> float:
    return equity.iloc[-1] / initial_capital - 1 if len(equity) else 0.0


def annualized_return(equity: pd.Series, initial_capital: float) -> float:
    """年化收益率（按交易日数折算）。"""
    if len(equity) < 2 or initial_capital <= 0:
        return 0.0
    final_ratio = equity.iloc[-1] / initial_capital
    if final_ratio <= 0:
        return -1.0
    return final_ratio ** (TRADING_DAYS_PER_YEAR / len(equity)) - 1


def max_drawdown(equity: pd.Series) -> dict:
    """最大回撤及区间。equity 索引为日期。"""
    if len(equity) < 2:
        return {"mdd": 0.0, "peak_date": "", "trough_date": ""}
    running_max = equity.cummax()
    drawdown = equity / running_max - 1
    trough_idx = drawdown.idxmin()
    mdd = float(drawdown.min())
    peak_idx = equity.loc[:trough_idx].idxmax()

    def _fmt(idx) -> str:
        ts = pd.Timestamp(idx)
        return ts.strftime("%Y-%m-%d")

    return {
        "mdd": mdd,
        "peak_date": _fmt(peak_idx),
        "trough_date": _fmt(trough_idx),
    }


def sharpe_ratio(equity: pd.Series) -> float:
    """夏普比率：日收益、无风险利率 0、√250 年化。"""
    daily = equity.pct_change().dropna()
    if len(daily) < 2 or daily.std() == 0:
        return 0.0
    return float(daily.mean() / daily.std() * math.sqrt(TRADING_DAYS_PER_YEAR))


def round_trip_stats(trades: list) -> dict:
    """卖出成交的已实现盈亏统计：胜率、盈亏比（无亏损时盈亏比为 None）。"""
    pnls = [t.pnl for t in trades if t.direction.value == "sell" and t.pnl != 0]
    if not pnls:
        return {"win_rate": 0.0, "profit_loss_ratio": None, "round_trips": 0}
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = len(wins) / len(pnls)
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = abs(float(np.mean(losses))) if losses else 0.0
    if avg_loss > 0:
        pl_ratio = round(avg_win / avg_loss, 4)
    else:
        pl_ratio = None if avg_win > 0 else 0.0  # 无亏损且无盈利→0；全胜→None（不可比）
    return {"win_rate": win_rate, "profit_loss_ratio": pl_ratio, "round_trips": len(pnls)}


def avg_holding_days(trades: list) -> float:
    """平均持仓天数：按标的对买卖 FIFO 配对，取交易日差。"""
    buys: dict[str, list] = {}
    holding_days: list[int] = []
    for t in trades:
        if t.direction.value == "buy":
            buys.setdefault(t.symbol, []).append((t.trade_time, t.volume))
        else:
            remaining = t.volume
            queue = buys.get(t.symbol, [])
            while remaining > 0 and queue:
                buy_time, buy_vol = queue[0]
                matched = min(remaining, buy_vol)
                d0 = pd.to_datetime(buy_time)
                d1 = pd.to_datetime(t.trade_time)
                holding_days.append((d1 - d0).days)
                if matched == buy_vol:
                    queue.pop(0)
                else:
                    queue[0] = (buy_time, buy_vol - matched)
                remaining -= matched
    return float(np.mean(holding_days)) if holding_days else 0.0


def turnover_rate(trades: list, equity: pd.Series) -> float:
    """换手率 = Σ成交额 / 平均权益。"""
    if equity.empty or equity.mean() == 0:
        return 0.0
    amount = sum(t.price * t.volume for t in trades)
    return float(amount / equity.mean())


def sortino_ratio(equity: pd.Series) -> float:
    """索提诺比率：下行标准差（rf=0），√250 年化。"""
    daily = equity.pct_change().dropna()
    if len(daily) < 2:
        return 0.0
    downside = daily[daily < 0]
    if len(downside) < 2 or downside.std() == 0:
        return 0.0
    return float(daily.mean() / downside.std() * math.sqrt(TRADING_DAYS_PER_YEAR))


def calmar_ratio(equity: pd.Series, initial_capital: float) -> float:
    """卡玛比率 = 年化收益 / |最大回撤|。"""
    ann = annualized_return(equity, initial_capital)
    mdd = abs(max_drawdown(equity)["mdd"])
    if mdd == 0:
        return 0.0
    return float(ann / mdd)


def _bench_daily_returns(benchmark: pd.DataFrame) -> pd.Series:
    bench = benchmark.set_index("trade_date")["close"]
    bench.index = pd.to_datetime(bench.index)  # 与净值序列的 DatetimeIndex 对齐
    return bench.pct_change().dropna()


def alpha_beta(equity: pd.Series, benchmark: pd.DataFrame) -> dict:
    """相对基准的 Alpha/Beta（日收益线性回归，alpha 年化）。"""
    strat = equity.pct_change().dropna()
    bench = _bench_daily_returns(benchmark)
    joined = pd.concat([strat.rename("s"), bench.rename("b")], axis=1, join="inner").dropna()
    if len(joined) < 20:
        return {"alpha": None, "beta": None}
    beta = float(joined["s"].cov(joined["b"]) / joined["b"].var())
    alpha = float((joined["s"].mean() - beta * joined["b"].mean()) * TRADING_DAYS_PER_YEAR)
    return {"alpha": alpha, "beta": beta}


def information_ratio(equity: pd.Series, benchmark: pd.DataFrame) -> float:
    """信息比率 = 年化主动收益 / 主动风险（跟踪误差）。"""
    strat = equity.pct_change().dropna()
    bench = _bench_daily_returns(benchmark)
    joined = pd.concat([strat.rename("s"), bench.rename("b")], axis=1, join="inner").dropna()
    if len(joined) < 20:
        return 0.0
    active = joined["s"] - joined["b"]
    if active.std() == 0:
        return 0.0
    return float(active.mean() / active.std() * math.sqrt(TRADING_DAYS_PER_YEAR))


def streak_stats(trades: list) -> dict:
    """最大连赢/连亏（按卖出已实现盈亏顺序）。"""
    max_wins = max_losses = cur_wins = cur_losses = 0
    for t in trades:
        if t.direction.value != "sell" or t.pnl == 0:
            continue
        if t.pnl > 0:
            cur_wins += 1
            cur_losses = 0
            max_wins = max(max_wins, cur_wins)
        else:
            cur_losses += 1
            cur_wins = 0
            max_losses = max(max_losses, cur_losses)
    return {"max_consecutive_wins": max_wins, "max_consecutive_losses": max_losses}


def rolling_sharpe(equity: pd.Series, window: int = 60) -> dict:
    """滚动夏普（60 日窗口），供图表展示。"""
    daily = equity.pct_change().dropna()
    if len(daily) < window:
        return {"dates": [], "values": []}
    rolled = daily.rolling(window).apply(
        lambda x: x.mean() / x.std() * math.sqrt(TRADING_DAYS_PER_YEAR) if x.std() > 0 else 0.0,
        raw=True,
    ).dropna()
    return {
        "dates": [ts.strftime("%Y-%m-%d") for ts in rolled.index],
        "values": [round(float(v), 3) for v in rolled.values],
    }


def compute_metrics(
    dates: list[str],
    equity_values: list[float],
    initial_capital: float,
    trades: list,
    benchmark: pd.DataFrame | None = None,
) -> dict:
    """汇总全部绩效指标。benchmark 为基准日线（需 trade_date 与 close 列）。"""
    equity = pd.Series(equity_values, index=pd.to_datetime(dates), dtype=float)
    mdd = max_drawdown(equity)
    rt = round_trip_stats(trades)

    metrics = {
        "initial_capital": initial_capital,
        "final_equity": float(equity.iloc[-1]) if len(equity) else initial_capital,
        "total_return": total_return(equity, initial_capital),
        "annualized_return": annualized_return(equity, initial_capital),
        "max_drawdown": mdd["mdd"],
        "max_drawdown_start": mdd["peak_date"],
        "max_drawdown_end": mdd["trough_date"],
        "sharpe_ratio": sharpe_ratio(equity),
        "sortino_ratio": sortino_ratio(equity),
        "calmar_ratio": calmar_ratio(equity, initial_capital),
        "win_rate": rt["win_rate"],
        "profit_loss_ratio": rt["profit_loss_ratio"],
        "trade_count": len(trades),
        "round_trips": rt["round_trips"],
        "avg_holding_days": avg_holding_days(trades),
        "turnover_rate": turnover_rate(trades, equity),
    }
    metrics.update(streak_stats(trades))
    metrics["rolling_sharpe"] = rolling_sharpe(equity)

    if benchmark is not None and not benchmark.empty and len(equity) >= 2:
        bench = benchmark.set_index("trade_date")["close"]
        bench = bench[(bench.index >= dates[0]) & (bench.index <= dates[-1])]
        if len(bench) >= 2:
            bench_return = float(bench.iloc[-1] / bench.iloc[0] - 1)
            metrics["benchmark_return"] = bench_return
            metrics["excess_return"] = metrics["total_return"] - bench_return
            metrics.update(alpha_beta(equity, benchmark))
            metrics["information_ratio"] = information_ratio(equity, benchmark)
    return metrics
