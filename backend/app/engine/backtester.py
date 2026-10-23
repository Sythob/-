"""事件驱动回测引擎（全系统正确性核心）。

铁律（防未来函数）：
1. 信号在 t 收盘产生（on_bar 使用 bar_t 的全部 OHLC）→ 订单入队；
2. 订单在下一根 bar（t+1）的开盘价 ± 滑点撮合；
3. T+1：当日买入的股份在当日收盘结算后解锁，次日才可卖。

A股规则：整手 100 股（买入自动向下取整；卖出非整手直接拒单）、
无做空、涨跌停拒单（可配置）、停牌跳过（持仓按最后收盘价估值）、
市价单当日开盘未成交即撤销（不隔夜）、限价单最长挂 N 日。

流程（每根全局时间轴 bar）：
  1. 用 t 开盘价撮合挂单 → 2. on_bar(t) 产生新订单 → 3. 收盘结算（T+1 解锁 + 记净值）
"""
from dataclasses import dataclass, field

import pandas as pd

from app.config import get_settings
from app.engine.costs import CostModel
from app.engine.matching import match_order
from app.engine.metrics import compute_metrics
from app.engine.objects import BarData, Direction, OrderData, OrderStatus, TradeData
from app.engine.portfolio import Portfolio


@dataclass
class BacktestConfig:
    initial_capital: float = 1_000_000.0
    commission_rate: float | None = None
    commission_min: float | None = None
    stamp_tax_rate: float | None = None
    transfer_fee_rate: float | None = None
    slippage: float | None = None
    lot_size: int = 100
    price_limit_ratio: float | None = None
    limit_check: bool = True
    max_limit_order_life: int = 5  # 限价单最长挂单天数
    # ---- 仓位分配（多标的组合回测）----
    allocation: str = "full"       # full(全仓) / equal(等权) / fixed(固定比例) / kelly(半凯利)
    fixed_fraction: float = 0.3    # fixed 模式：单笔占总资产比例
    target_positions: int = 5      # equal 模式：目标持仓数
    kelly_win_rate: float = 0.5    # kelly 模式：历史胜率估计
    kelly_payoff: float = 1.0      # kelly 模式：盈亏比估计
    # ---- 亏损限额风控（0 = 关闭）----
    max_loss_per_trade_pct: float = 0.0   # 单笔亏损超过 X% 强制卖出
    max_daily_loss_pct: float = 0.0       # 权益较前日收盘跌超 X% → 次日暂停开仓


@dataclass
class BacktestResult:
    dates: list[str] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    cash: list[float] = field(default_factory=list)
    return_rates: list[float] = field(default_factory=list)
    trades: list[TradeData] = field(default_factory=list)
    orders: list[OrderData] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


class BacktestEngine:
    def __init__(self, data: dict[str, pd.DataFrame], strategy_class, params: dict | None = None,
                 config: BacktestConfig | None = None, benchmark: pd.DataFrame | None = None):
        """
        data: {symbol: DataFrame(trade_date 升序, 需 open/high/low/close/volume/pct_change)}
        benchmark: 基准日线（trade_date + close，用于同期对比，可空）
        """
        self.config = config or BacktestConfig()
        settings = get_settings()
        self.cost_model = CostModel(
            commission_rate=self.config.commission_rate or settings.commission_rate,
            commission_min=self.config.commission_min or settings.commission_min,
            stamp_tax_rate=self.config.stamp_tax_rate or settings.stamp_tax_rate,
            transfer_fee_rate=self.config.transfer_fee_rate or settings.transfer_fee_rate,
            slippage=self.config.slippage if self.config.slippage is not None else settings.slippage,
        )
        self.lot_size = self.config.lot_size
        limit_ratio = self.config.price_limit_ratio or settings.price_limit_ratio

        self.portfolio = Portfolio(self.config.initial_capital, lot_size=self.lot_size)
        self.strategy = strategy_class(self, params or {})
        self.benchmark = benchmark

        self._stops: dict[str, "StopOrder"] = {}  # 引擎化止损止盈
        self._halted = False  # 触发单日亏损限额后暂停开仓
        self._bars: dict[str, dict[str, BarData]] = {}
        self._limit_threshold = limit_ratio * 100 - 0.2  # 9.8%（容忍取整误差）
        for symbol, df in data.items():
            symbol_bars = {}
            for _, row in df.iterrows():
                pct = float(row.get("pct_change") or 0.0)
                symbol_bars[str(row["trade_date"])] = BarData(
                    symbol=symbol,
                    trade_date=str(row["trade_date"]),
                    open=float(row["open"]), high=float(row["high"]),
                    low=float(row["low"]), close=float(row["close"]),
                    volume=float(row.get("volume") or 0.0),
                    amount=float(row.get("amount") or 0.0),
                    pct_change=pct,
                    limit_up=self.config.limit_check and pct >= self._limit_threshold,
                    limit_down=self.config.limit_check and pct <= -self._limit_threshold,
                )
            self._bars[symbol] = symbol_bars

        self.dates: list[str] = []
        self.current_date: str = ""
        self.pending: list[OrderData] = []       # 等待撮合的委托
        self.order_history: list[OrderData] = []
        self.trades: list[TradeData] = []
        self.equity_curve: list[dict] = []
        self._order_id = 0

    # ------------------------------------------------------------ 策略侧接口

    def buy(self, symbol: str, volume: int | None = None, price_type: str = "market",
            price: float = 0.0, fill_on_close: bool = False) -> OrderData | None:
        """下买单。volume=None 表示市价全仓（成交时按可用资金计算）；给定则向下取整到整手。

        fill_on_close=True 为尾盘单（当日收盘撮合，策略需声明 fill_style="close"）。
        """
        if volume is not None:
            volume = int(volume) // self.lot_size * self.lot_size
            if volume <= 0:
                return self._reject(symbol, Direction.BUY, 0, "买入数量不足一手")
        return self._submit(symbol, Direction.BUY, volume or 0, price_type, price, fill_on_close)

    def sell(self, symbol: str, volume: int | None = None, price_type: str = "market",
             price: float = 0.0, fill_on_close: bool = False) -> OrderData | None:
        """下卖单。volume=None 表示全部可卖持仓。"""
        pos = self.portfolio.get_position(symbol)
        if volume is None:
            if pos is None or pos.available_volume <= 0:
                return self._reject(symbol, Direction.SELL, 0, "无可卖持仓")
            volume = pos.available_volume
        volume = int(volume)
        if volume % self.lot_size != 0:
            return self._reject(symbol, Direction.SELL, volume, "卖出数量必须是整手（100 股）")
        if volume <= 0:
            return self._reject(symbol, Direction.SELL, 0, "卖出数量无效")
        return self._submit(symbol, Direction.SELL, volume, price_type, price, fill_on_close)

    def _submit(self, symbol, direction, volume, price_type, price,
                fill_on_close: bool = False) -> OrderData:
        self._order_id += 1
        order = OrderData(
            symbol=symbol, direction=direction, price_type=price_type, price=price,
            volume=volume, order_id=self._order_id, created_date=self.current_date,
            fill_on_close=fill_on_close,
        )
        self.pending.append(order)
        self.order_history.append(order)
        return order

    def position(self, symbol: str):
        return self.portfolio.get_position(symbol)

    def set_stop(self, symbol: str, stop_type: str, *, trigger_price: float = 0.0,
                 atr_multiple: float = 2.0, atr_value: float = 0.0,
                 trailing_pct: float = 0.08) -> None:
        """注册止损/止盈（持仓期间生效；触发后次日开盘强制卖出）。"""
        from app.engine.stops import StopOrder, StopType

        try:
            stop_type_enum = StopType(stop_type)
        except ValueError:
            raise ValueError(f"未知止损类型: {stop_type}") from None
        self._stops[symbol] = StopOrder(
            symbol=symbol, stop_type=stop_type_enum, trigger_price=trigger_price,
            atr_multiple=atr_multiple, atr_value=atr_value, trailing_pct=trailing_pct,
        )

    def cancel_stop(self, symbol: str) -> None:
        self._stops.pop(symbol, None)

    @property
    def cash(self) -> float:
        return self.portfolio.account.cash

    @property
    def equity(self) -> float:
        return self.portfolio.equity()

    def _reject(self, symbol, direction, volume, reason) -> OrderData | None:
        self._order_id += 1
        order = OrderData(
            symbol=symbol, direction=direction, volume=volume,
            order_id=self._order_id, status=OrderStatus.REJECTED,
            created_date=self.current_date, reject_reason=reason,
        )
        self.order_history.append(order)
        return order

    # ------------------------------------------------------------ 主循环

    def run(self) -> BacktestResult:
        self.dates = sorted({d for bars in self._bars.values() for d in bars})
        if not self.dates:
            raise ValueError("无行情数据，无法回测")

        self.strategy.on_init({sym: self._raw_data(sym) for sym in self._bars})

        for date in self.dates:
            self.current_date = date
            bars_today = {sym: bars.get(date) for sym, bars in self._bars.items() if date in bars}

            # 1. t 开盘撮合挂单（含 t-1 收盘产生的信号）
            self._match_pending(bars_today, date, price_key="open")

            # 2. 策略信号（t 收盘信息）→ 订单入队
            for bar in bars_today.values():
                self.strategy.on_bar(bar)

            # 2.5 收盘成交模式（策略声明 fill_style="close"）：
            # 信号与成交信息同步（同为 t 收盘），无未来函数——尾盘买入的真实近似
            if self.strategy.fill_style == "close":
                self._match_pending(bars_today, date, price_key="close")

            # 3. 收盘结算：T+1 解锁 + 净值记录
            self._settle(date, bars_today)

        return self._build_result()

    def _raw_data(self, symbol: str) -> pd.DataFrame:
        """策略预热用原始 DataFrame（trade_date 升序）。"""
        bars = self._bars[symbol]
        dates = sorted(bars)
        return pd.DataFrame([
            {"trade_date": d, "open": b.open, "high": b.high, "low": b.low,
             "close": b.close, "volume": b.volume, "amount": b.amount,
             "pct_change": b.pct_change}
            for d, b in [(d, bars[d]) for d in dates]
        ])

    def _match_pending(self, bars_today: dict, date: str, price_key: str = "open") -> None:
        """按参考价撮合挂单。

        price_key: open（次日开盘成交铁律）/ close（收盘成交模式）。
        open 档只处理普通单；close 档只处理尾盘单（fill_on_close=True），
        且当日未成交的尾盘单直接撤销（不隔夜）。
        """
        on_close = price_key == "close"
        for order in list(self.pending):
            if order.fill_on_close != on_close:
                continue  # 不同时点的单互不干涉
            bar = bars_today.get(order.symbol)
            if bar is None:
                # 停牌：尾盘单撤销；普通市价单当日撤销，限价单按生命周期
                if on_close or order.price_type == "market":
                    order.status = OrderStatus.CANCELLED
                    self.pending.remove(order)
                elif self._limit_order_expired(order, date):
                    order.status = OrderStatus.CANCELLED
                    self.pending.remove(order)
                continue

            reference = bar.open if not on_close else bar.close
            match = match_order(
                order, price=reference, cost_model=self.cost_model,
                limit_up=bar.limit_up, limit_down=bar.limit_down,
            )
            if match is None:
                # 未成交：尾盘单直接撤销；普通市价单当日撤销，限价单按生命周期
                if on_close or order.price_type == "market":
                    order.status = OrderStatus.CANCELLED
                    self.pending.remove(order)
                elif self._limit_order_expired(order, date):
                    order.status = OrderStatus.CANCELLED
                    self.pending.remove(order)
                continue

            fill_price, fee = match
            self._try_fill(order, fill_price, fee, date)
            self.pending.remove(order)  # 无论成交/拒单，本单处理完毕

    def _try_fill(self, order: OrderData, fill_price: float, fee: float, date: str) -> bool:
        """资金/持仓校验后成交。成功返回 True，订单从 pending 移除。"""
        if order.direction == Direction.BUY:
            if self._halted:
                order.status = OrderStatus.REJECTED
                order.reject_reason = "触发单日亏损限额，暂停开仓"
                return False
            volume = order.volume
            if volume == 0:  # 自动数量：按仓位分配模式计算买入预算
                budget = self._position_budget()
                volume = int(budget / fill_price) // self.lot_size * self.lot_size
                if volume <= 0:
                    order.status = OrderStatus.REJECTED
                    order.reject_reason = "资金不足"
                    return False
                order.volume = volume
            fee = self.cost_model.total_fee(fill_price, order.volume, Direction.BUY)
            if not self.portfolio.can_buy(fill_price, order.volume, fee):
                order.status = OrderStatus.REJECTED
                order.reject_reason = "资金不足"
                return False
            self.portfolio.buy_fill(order.symbol, fill_price, order.volume, fee)
        else:
            ok, reason = self.portfolio.can_sell(order.symbol, order.volume)
            if not ok:
                order.status = OrderStatus.REJECTED
                order.reject_reason = reason
                return False
            pnl = self.portfolio.sell_fill(order.symbol, fill_price, order.volume, fee)

        order.status = OrderStatus.FILLED
        order.filled_price = fill_price
        trade = TradeData(
            symbol=order.symbol, direction=order.direction, price=fill_price,
            volume=order.volume, trade_time=date, commission=fee,
            slippage=self.cost_model.slippage,
            pnl=pnl if order.direction == Direction.SELL else 0.0,
        )
        self.trades.append(trade)
        return True

    def _position_budget(self) -> float:
        """按仓位分配模式计算单笔买入预算（full/equal/fixed/kelly）。"""
        mode = self.config.allocation
        cash = self.portfolio.account.cash
        if mode == "full":
            return cash
        equity = self.portfolio.equity()
        if mode == "equal":
            return min(cash, equity / max(self.config.target_positions, 1))
        if mode == "fixed":
            return min(cash, equity * self.config.fixed_fraction)
        if mode == "kelly":
            w, b = self.config.kelly_win_rate, max(self.config.kelly_payoff, 1e-9)
            f = max(w - (1 - w) / b, 0.0)
            return min(cash, equity * f * 0.5)  # 半凯利，保守
        return cash

    def _limit_order_expired(self, order: OrderData, date: str) -> bool:
        """限价单挂单超过 max_limit_order_life 个交易日则撤销。"""
        if order.created_date and order.created_date in self.dates:
            life = self.dates.index(date) - self.dates.index(order.created_date)
            return life >= self.config.max_limit_order_life
        return False

    def _settle(self, date: str, bars_today: dict) -> None:
        self.portfolio.settle_t1()
        prices = {sym: bar.close for sym, bar in bars_today.items()}
        self.portfolio.update_last_close(prices)
        self._check_stops(bars_today)  # 止损触发 → 次日开盘强制卖出
        self._check_loss_limits(bars_today)
        equity = self.portfolio.equity()
        cash = self.portfolio.account.cash
        self.equity_curve.append({
            "date": date,
            "equity": equity,
            "cash": cash,
            "return_rate": equity / self.config.initial_capital - 1,
        })
        # 单日亏损限额：与昨日收盘权益比较，触发则次日暂停开仓
        if self.config.max_daily_loss_pct > 0 and len(self.equity_curve) >= 2:
            prev_equity = self.equity_curve[-2]["equity"]
            self._halted = prev_equity > 0 and (
                equity / prev_equity - 1 <= -self.config.max_daily_loss_pct
            )
        else:
            self._halted = False

    def _check_loss_limits(self, bars_today: dict) -> None:
        """单笔亏损限额：持仓亏损超过阈值 → 强制卖出（次日开盘成交）。"""
        if self.config.max_loss_per_trade_pct <= 0:
            return
        for symbol, pos in list(self.portfolio.positions.items()):
            if pos.volume <= 0 or pos.avg_cost <= 0:
                continue
            close = self.portfolio.last_close.get(symbol, 0.0)
            if close > 0 and close / pos.avg_cost - 1 <= -self.config.max_loss_per_trade_pct:
                self.sell(symbol, volume=None)

    def _check_stops(self, bars_today: dict) -> None:
        """收盘后评估各持仓的止损止盈，触发则提交卖出（t+1 开盘成交）。"""
        from app.engine.stops import evaluate_stop

        for symbol, stop in list(self._stops.items()):
            bar = bars_today.get(symbol)
            pos = self.portfolio.positions.get(symbol)
            if bar is None:
                continue  # 停牌：保留止损
            if pos is not None and pos.volume <= 0:
                self._stops.pop(symbol, None)  # 已清仓：移除止损
                continue
            # pos 为 None = 买单尚未成交（信号与成交隔一日）：保留止损等待
            if evaluate_stop(stop, bar.close):
                stop.triggered = True
                self.sell(symbol, volume=None)  # 全部可卖，次日成交
                self._stops.pop(symbol, None)

    def _build_result(self) -> BacktestResult:
        # 收盘后仍未成交的委托一律撤销
        for order in self.pending:
            order.status = OrderStatus.CANCELLED
        self.strategy.on_stop()
        metrics = compute_metrics(
            [e["date"] for e in self.equity_curve],
            [e["equity"] for e in self.equity_curve],
            self.config.initial_capital,
            self.trades,
            benchmark=self.benchmark,
        )
        return BacktestResult(
            dates=[e["date"] for e in self.equity_curve],
            equity=[e["equity"] for e in self.equity_curve],
            cash=[e["cash"] for e in self.equity_curve],
            return_rates=[e["return_rate"] for e in self.equity_curve],
            trades=self.trades,
            orders=self.order_history,
            metrics=metrics,
        )
