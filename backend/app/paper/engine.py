"""模拟盘引擎：轮询行情 → 撮合挂单 → 14:50 合成日线投喂策略 → 15:05 结算。

与回测共用 matching/costs/portfolio 组件，差异只在行情来源与时钟：
- 手动委托：即时撮合（按当前快照价 ± 滑点）
- 策略信号单（next_session=1）：次日才可撮合 —— 与回测「信号次日开盘成交」铁律一致
- T+1 / 整手 / 涨跌停规则与回测完全相同
- 现金/持仓/委托全落 SQLite，进程重启后恢复
"""
import json
import threading
from datetime import datetime, time as dtime

from app.config import get_settings
from app.data import database, loader
from app.data.models import PaperOrder, TradeRecord
from app.engine.costs import CostModel
from app.engine.matching import match_order
from app.engine.objects import Direction, OrderData, OrderStatus, PositionData
from app.engine.portfolio import Portfolio
from app.paper import account as account_service
from app.paper.quoter import fetch_quotes
from app.utils.logger import get_logger

logger = get_logger("paper.engine")

LIMIT_THRESHOLD = 9.8  # 涨跌停近似阈值（%）

_engine = None


def get_paper_engine() -> "PaperEngine":
    global _engine
    if _engine is None:
        _engine = PaperEngine()
    return _engine


class _PaperAdapter:
    """把 PaperEngine 暴露成 Strategy 期望的 engine 接口。"""

    def __init__(self, paper_engine: "PaperEngine"):
        self._pe = paper_engine
        from app.data import calendar

        self.dates = calendar.load_trade_dates() or []

    def buy(self, symbol, volume=None, price_type="market", price=0.0):
        return self._pe.place_order(symbol, Direction.BUY, volume or 0,
                                    price_type, price, next_session=True)

    def sell(self, symbol, volume=None, price_type="market", price=0.0):
        return self._pe.place_order(symbol, Direction.SELL, volume or 0,
                                    price_type, price, next_session=True)

    def position(self, symbol):
        return self._pe._portfolio.positions.get(symbol)

    @property
    def cash(self) -> float:
        return self._pe._portfolio.account.cash

    @property
    def equity(self) -> float:
        return self._pe._portfolio.equity()


class PaperEngine:
    def __init__(self):
        settings = get_settings()
        self._cost_model = CostModel(
            commission_rate=settings.commission_rate,
            commission_min=settings.commission_min,
            stamp_tax_rate=settings.stamp_tax_rate,
            transfer_fee_rate=settings.transfer_fee_rate,
            slippage=settings.slippage,
        )
        self._session_date = ""
        self._today_bars: dict[str, dict] = {}
        self._strategies_fired = False
        self._settled = False
        self._halted = False  # 触发单日亏损限额后暂停开仓（新交易日重置）
        # 轮询/下单 API/定时任务可能并发调用引擎，状态必须串行化
        self._lock = threading.Lock()
        self._restore()
        self._load_strategy()

    # ------------------------------------------------------------ 状态恢复

    def _restore(self) -> None:
        cash, positions = account_service.load_portfolio_state()
        self._account_id = account_service.ensure_account().id
        self._portfolio = Portfolio(cash)
        for sym, (volume, available, avg_cost) in positions.items():
            self._portfolio.positions[sym] = PositionData(
                symbol=sym, volume=volume, available_volume=available, avg_cost=avg_cost,
            )
            self._portfolio._lots[sym] = [[volume, avg_cost]] if volume else []
            latest = loader.get_latest_bar(sym)
            if latest:
                self._portfolio.last_close[sym] = latest["close"]

    def reload(self) -> None:
        """从 DB 重载账户与持仓（外部导入/重置后调用，立即生效）。"""
        with self._lock:
            self._restore()

    def _load_strategy(self) -> None:
        """从配置加载模拟盘自动策略（可为空 = 仅手动交易）。"""
        name = get_settings().paper_strategy
        self._strategy = None
        self._strategy_name = ""
        if not name:
            return
        from app.strategies import StrategyRegistry

        strategy_cls = StrategyRegistry.get(name)
        self._strategy = strategy_cls(_PaperAdapter(self), {})
        data = {}
        for sym in get_settings().watchlist_symbols:
            df = loader.load_daily(sym, adjust="qfq")
            if not df.empty:
                data[sym] = df
        self._strategy.on_init(data)
        self._strategy_name = name
        logger.info("模拟盘策略已加载: %s", name)

    # ------------------------------------------------------------ 委托

    def place_order(self, symbol: str, direction: Direction, volume: int,
                    price_type: str = "market", price: float = 0.0,
                    next_session: bool = False, ai_check: dict | None = None,
                    condition_type: str = "", trigger_price: float = 0.0) -> PaperOrder:
        """下单入库（状态 submitted），由轮询撮合。返回委托记录。

        condition_type 支持 ""(普通单) / "stop_loss" / "take_profit"：
        条件单入库后处于等待状态，行情穿越触发价后按市价激活撮合。
        """
        if direction == Direction.SELL and volume % self._portfolio.lot_size != 0:
            raise ValueError("卖出数量必须是整手（100 股）")
        if direction == Direction.BUY and volume and volume % self._portfolio.lot_size != 0:
            raise ValueError("买入数量必须是整手（100 股）")
        if condition_type and condition_type not in ("stop_loss", "take_profit"):
            raise ValueError("condition_type 仅支持 stop_loss/take_profit")
        if condition_type and trigger_price <= 0:
            raise ValueError("条件单必须提供触发价")
        with database.get_session() as session:
            row = PaperOrder(
                account_id=self._account_id, symbol=symbol,
                direction=direction.value, order_type=price_type,
                price=price, volume=volume, next_session=1 if next_session else 0,
                condition_type=condition_type, trigger_price=trigger_price,
                ai_check_json=json.dumps(ai_check, ensure_ascii=False) if ai_check else "",
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    # ------------------------------------------------------------ 轮询主循环

    def process_quotes(self) -> dict:
        """每 30s 调度调用（仅交易时段）。并发调用时内部串行化。"""
        with self._lock:
            today = datetime.now().strftime("%Y-%m-%d")
            if today != self._session_date:
                self._session_date = today
                self._today_bars = {}
                self._strategies_fired = False
                self._settled = False
                self._halted = False  # 新交易日解除暂停

            symbols = set(get_settings().watchlist_symbols)
            symbols.update(self._portfolio.positions.keys())
            quotes = fetch_quotes(sorted(symbols))
            if not quotes:
                return {"quotes": 0, "message": "行情获取失败，跳过本轮"}

            self._accumulate(quotes)
            filled = self._match_orders(quotes)

            now = datetime.now().time()
            if now >= dtime(14, 50) and not self._strategies_fired:
                self._fire_strategies()
            if now >= dtime(15, 5) and not self._settled:
                self._settle_locked()
            return {"quotes": len(quotes), "filled": filled}

    def _accumulate(self, quotes: dict[str, dict]) -> None:
        """当日 OHLC 合成（供 14:50 日线策略与结算估值）。"""
        for sym, q in quotes.items():
            if q["last_price"] <= 0:
                continue
            self._portfolio.last_close[sym] = q["last_price"]  # 估值用最新实时价
            bar = self._today_bars.get(sym)
            if bar is None:
                bar = {"open": q["open"] or q["last_price"], "high": q["last_price"],
                       "low": q["last_price"], "close": q["last_price"],
                       "volume": q["volume"], "amount": q["amount"], "pct_change": q["pct_change"]}
                self._today_bars[sym] = bar
            else:
                bar["high"] = max(bar["high"], q["last_price"])
                bar["low"] = min(bar["low"], q["last_price"])
                bar["close"] = q["last_price"]
                bar["volume"] = q["volume"]
                bar["amount"] = q["amount"]
                bar["pct_change"] = q["pct_change"]

    def _match_orders(self, quotes: dict[str, dict]) -> int:
        """撮合全部 submitted 委托。策略单当日不撮合（防未来函数）。"""
        with database.get_session() as session:
            rows = (
                session.query(PaperOrder)
                .filter(PaperOrder.account_id == self._account_id,
                        PaperOrder.status == "submitted")
                .all()
            )
            pending = [
                PaperOrderRow(r) for r in rows
            ]
        filled = 0
        for order in pending:
            quote = quotes.get(order.symbol)
            if not quote or quote["last_price"] <= 0:
                continue
            if order.next_session and order.created_date == self._session_date:
                continue  # 当日产生的策略信号单，次日才可撮合
            # 条件单：行情未穿越触发价则继续等待
            if order.condition_type and not self._condition_triggered(order, quote):
                continue
            limit_up = quote["pct_change"] >= LIMIT_THRESHOLD
            limit_down = quote["pct_change"] <= -LIMIT_THRESHOLD
            result = match_order(order.to_engine_order(), price=quote["last_price"],
                                 cost_model=self._cost_model,
                                 limit_up=limit_up, limit_down=limit_down)
            if result is None:
                continue  # 限价未穿越/涨跌停：保留待下一轮
            fill_price, fee = result
            if self._try_fill(order, fill_price, fee, quote):
                filled += 1
        return filled

    @staticmethod
    def _condition_triggered(order: "PaperOrderRow", quote: dict) -> bool:
        """条件单触发判断：止损单价格 ≤ 触发价；止盈单价格 ≥ 触发价。"""
        price = quote["last_price"]
        if order.condition_type == "stop_loss":
            return price <= order.trigger_price
        if order.condition_type == "take_profit":
            return price >= order.trigger_price
        return False

    def _try_fill(self, order: "PaperOrderRow", fill_price: float, fee: float,
                  quote: dict) -> bool:
        """资金/持仓校验后成交并落库。"""
        if order.direction == Direction.BUY:
            if self._halted:
                self._reject_order(order.row_id, "触发单日亏损限额，暂停开仓")
                return False
            volume = order.volume
            if volume == 0:  # 市价全仓
                volume = self._portfolio.max_affordable_volume(
                    fill_price,
                    lambda v: self._cost_model.total_fee(fill_price, v, Direction.BUY),
                )
                if volume <= 0:
                    self._reject_order(order.row_id, "资金不足")
                    return False
            fee = self._cost_model.total_fee(fill_price, volume, Direction.BUY)
            if not self._portfolio.can_buy(fill_price, volume, fee):
                self._reject_order(order.row_id, "资金不足")
                return False
            self._portfolio.buy_fill(order.symbol, fill_price, volume, fee)
        else:
            ok, reason = self._portfolio.can_sell(order.symbol, order.volume)
            if not ok:
                self._reject_order(order.row_id, reason)
                return False
            self._portfolio.sell_fill(order.symbol, fill_price, order.volume, fee)
            volume = order.volume

        self._persist_fill(order, fill_price, volume, fee)
        return True

    def _persist_fill(self, order: "PaperOrderRow", fill_price: float, volume: int,
                      fee: float) -> None:
        account_service.save_cash(self._account_id, self._portfolio.account.cash)
        pos = self._portfolio.positions[order.symbol]
        account_service.upsert_position(self._account_id, order.symbol,
                                        pos.volume, pos.available_volume, pos.avg_cost)
        with database.get_session() as session:
            row = session.get(PaperOrder, order.row_id)
            if row:
                row.status = "filled"
                row.filled_price = fill_price
                row.filled_volume = volume
            session.add(TradeRecord(
                account_id=self._account_id, symbol=order.symbol,
                direction=order.direction.value, price=fill_price, volume=volume,
                trade_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                commission=fee, slippage=self._cost_model.slippage,
            ))
            session.commit()
        # 成交通知（配置了推送通道才发送，异步不阻塞）
        from app.utils.notify import send_notification

        direction_cn = "买入" if order.direction == Direction.BUY else "卖出"
        send_notification(
            "模拟盘成交",
            f"{order.symbol} {direction_cn} {volume} 股 @ {fill_price:.2f}，费用 {fee:.2f} 元",
        )

    def _reject_order(self, order_id: int, reason: str) -> None:
        with database.get_session() as session:
            row = session.get(PaperOrder, order_id)
            if row:
                row.status = "rejected"
                row.reject_reason = reason[:200]
                session.commit()

    # ------------------------------------------------------------ 日线策略与结算

    def _fire_strategies(self) -> None:
        self._strategies_fired = True
        if self._strategy is None:
            return
        from app.engine.objects import BarData

        for sym in get_settings().watchlist_symbols:
            bar = self._today_bars.get(sym)
            if bar is None:
                continue
            bar_data = BarData(
                symbol=sym, trade_date=self._session_date,
                open=bar["open"], high=bar["high"], low=bar["low"], close=bar["close"],
                volume=bar["volume"], amount=bar["amount"], pct_change=bar["pct_change"],
                limit_up=bar["pct_change"] >= LIMIT_THRESHOLD,
                limit_down=bar["pct_change"] <= -LIMIT_THRESHOLD,
            )
            try:
                self._strategy.on_bar(bar_data)
            except Exception:  # noqa: BLE001 单个标的策略异常不影响其他
                logger.exception("模拟盘策略异常 %s", sym)
        logger.info("模拟盘策略信号已触发: %s", self._strategy_name)

    def settle(self) -> None:
        """T+1 解锁 + 净值快照 + 未成交市价单撤销（加锁，幂等）。"""
        with self._lock:
            self._settle_locked()

    def _settle_locked(self) -> None:
        if self._settled:
            return
        self._settled = True
        self._portfolio.settle_t1()
        account_service.settle_t1(self._account_id)
        self._check_loss_limits()

    def _check_loss_limits(self) -> None:
        """亏损限额：单笔亏损超限强制卖出（下一轮行情成交）；单日亏损超限次日停开仓。"""
        settings = get_settings()
        if settings.paper_max_loss_per_trade > 0:
            for symbol, pos in list(self._portfolio.positions.items()):
                close = self._portfolio.last_close.get(symbol, 0.0)
                if pos.volume > 0 and pos.avg_cost > 0 and close > 0:
                    if close / pos.avg_cost - 1 <= -settings.paper_max_loss_per_trade:
                        logger.warning("触发单笔亏损限额，强制卖出 %s", symbol)
                        self.place_order(symbol, Direction.SELL, pos.available_volume)
        if settings.paper_max_daily_loss > 0:
            with database.get_session() as session:
                from app.data.models import EquityCurve

                rows = (
                    session.query(EquityCurve)
                    .filter(EquityCurve.scope_type == "paper",
                            EquityCurve.scope_id == self._account_id)
                    .order_by(EquityCurve.date.desc()).limit(2).all()
                )
            if len(rows) >= 2 and rows[1].equity > 0:
                today = self._portfolio.equity()
                if today / rows[1].equity - 1 <= -settings.paper_max_daily_loss:
                    self._halted = True
                    logger.warning("触发单日亏损限额，次日暂停开仓")

        for sym, pos in self._portfolio.positions.items():
            if sym in self._today_bars:
                self._portfolio.last_close[sym] = self._today_bars[sym]["close"]
            account_service.upsert_position(self._account_id, sym,
                                            pos.volume, pos.available_volume, pos.avg_cost)

        equity = self._portfolio.equity()
        initial = account_service.ensure_account().initial_capital
        account_service.record_equity(self._account_id, self._session_date,
                                      equity, self._portfolio.account.cash,
                                      equity / initial - 1)

        # 手动市价单当日未成交即撤销（不隔夜）；策略信号单/限价单/条件单保留
        with database.get_session() as session:
            rows = (
                session.query(PaperOrder)
                .filter(PaperOrder.account_id == self._account_id,
                        PaperOrder.status == "submitted",
                        PaperOrder.order_type == "market",
                        PaperOrder.next_session == 0,
                        PaperOrder.condition_type == "")
                .all()
            )
            for row in rows:
                row.status = "cancelled"
            session.commit()
        logger.info("模拟盘结算完成: 资产 %.2f", equity)


class PaperOrderRow:
    """paper_order 行的轻量包装（转引擎 OrderData 用）。"""

    def __init__(self, row: PaperOrder):
        self.row_id = row.id
        self.symbol = row.symbol
        self.direction = Direction(row.direction)
        self.price_type = row.order_type
        self.price = row.price
        self.volume = row.volume
        self.next_session = bool(row.next_session)
        self.condition_type = row.condition_type or ""
        self.trigger_price = row.trigger_price or 0.0
        self.created_date = row.created_at.strftime("%Y-%m-%d")

    def to_engine_order(self) -> OrderData:
        return OrderData(
            symbol=self.symbol, direction=self.direction, price_type=self.price_type,
            price=self.price, volume=self.volume,
        )
