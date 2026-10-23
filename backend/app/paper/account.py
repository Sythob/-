"""模拟账户服务：账户/持仓/委托/净值的 DB 读写。

现金在 paper_account.cash，持仓在 paper_position（volume/available/avg_cost），
全部落 SQLite —— 进程重启后由引擎恢复。
"""
from datetime import datetime

from app.config import get_settings
from app.data import database, loader
from app.data.models import EquityCurve, PaperAccount, PaperPosition


def ensure_account() -> PaperAccount:
    """取第一个账户，无则创建（初始资金 100 万，风控模式取 .env 配置）。"""
    settings = get_settings()
    with database.get_session() as session:
        row = session.query(PaperAccount).order_by(PaperAccount.id).first()
        if row is None:
            row = PaperAccount(
                name="默认账户",
                initial_capital=1_000_000.0,
                cash=1_000_000.0,
                risk_mode=settings.ai_risk_mode,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
        return row


def get_account() -> PaperAccount | None:
    with database.get_session() as session:
        return session.query(PaperAccount).order_by(PaperAccount.id).first()


def get_account_by_id(account_id: int) -> PaperAccount | None:
    with database.get_session() as session:
        return session.get(PaperAccount, account_id)


def save_cash(account_id: int, cash: float) -> None:
    with database.get_session() as session:
        row = session.get(PaperAccount, account_id)
        if row:
            row.cash = round(cash, 2)
            session.commit()


def upsert_position(account_id: int, symbol: str, volume: int, available: int, avg_cost: float) -> None:
    with database.get_session() as session:
        row = (
            session.query(PaperPosition)
            .filter(PaperPosition.account_id == account_id, PaperPosition.symbol == symbol)
            .first()
        )
        if row is None:
            row = PaperPosition(account_id=account_id, symbol=symbol)
            session.add(row)
        row.volume = volume
        row.available_volume = available
        row.avg_cost = avg_cost
        row.updated_at = datetime.now()
        session.commit()


def load_portfolio_state() -> tuple[float, dict]:
    """返回 (现金, {symbol: (volume, available, avg_cost)})，供引擎恢复。"""
    account = ensure_account()
    with database.get_session() as session:
        rows = session.query(PaperPosition).filter(PaperPosition.account_id == account.id).all()
        positions = {
            r.symbol: (r.volume, r.available_volume, r.avg_cost)
            for r in rows if r.volume > 0
        }
        return account.cash, positions


def settle_t1(account_id: int) -> None:
    """T+1 结算：全部持仓解锁。"""
    with database.get_session() as session:
        rows = session.query(PaperPosition).filter(PaperPosition.account_id == account_id).all()
        for row in rows:
            row.available_volume = row.volume
            row.updated_at = datetime.now()
        session.commit()


def record_equity(account_id: int, date: str, equity: float, cash: float, return_rate: float) -> None:
    with database.get_session() as session:
        row = (
            session.query(EquityCurve)
            .filter(EquityCurve.scope_type == "paper", EquityCurve.scope_id == account_id,
                    EquityCurve.date == date)
            .first()
        )
        if row is None:
            row = EquityCurve(scope_type="paper", scope_id=account_id, date=date)
            session.add(row)
        row.equity = round(equity, 2)
        row.cash = round(cash, 2)
        row.return_rate = round(return_rate, 6)
        session.commit()


def account_summary() -> dict:
    """账户概览：现金/市值/总资产/今日盈亏/持仓明细。"""
    account = ensure_account()
    with database.get_session() as session:
        positions = (
            session.query(PaperPosition)
            .filter(PaperPosition.account_id == account.id, PaperPosition.volume > 0)
            .all()
        )
        equity_rows = (
            session.query(EquityCurve)
            .filter(EquityCurve.scope_type == "paper", EquityCurve.scope_id == account.id)
            .order_by(EquityCurve.date.desc())
            .limit(2).all()
        )

    # 持仓名称（证券主表一次查询）
    position_symbols = [p.symbol for p in positions]
    names: dict[str, str] = {}
    if position_symbols:
        from app.data.models import Instrument

        with database.get_session() as session:
            names = {
                r.symbol: r.name
                for r in session.query(Instrument).filter(Instrument.symbol.in_(position_symbols)).all()
            }

    position_list = []
    market_value = 0.0
    for p in positions:
        # 估值价优先用引擎缓存的最新行情（盘中实时），其次本地日线收盘
        price = _engine_latest_price(p.symbol) or 0.0
        if price <= 0:
            latest = loader.get_latest_bar(p.symbol)
            price = latest["close"] if latest else p.avg_cost
        value = p.volume * price
        market_value += value
        position_list.append({
            "symbol": p.symbol, "name": names.get(p.symbol, ""),
            "volume": p.volume, "available_volume": p.available_volume,
            "avg_cost": p.avg_cost, "price": price, "market_value": round(value, 2),
            "pnl": round((price - p.avg_cost) * p.volume, 2),
            "pnl_pct": round(price / p.avg_cost - 1, 4) if p.avg_cost else 0.0,
        })

    total = account.cash + market_value
    prev_equity = equity_rows[1].equity if len(equity_rows) >= 2 else account.initial_capital
    return {
        "id": account.id,
        "name": account.name,
        "initial_capital": account.initial_capital,
        "cash": round(account.cash, 2),
        "market_value": round(market_value, 2),
        "total": round(total, 2),
        "today_pnl": round(total - prev_equity, 2),
        "total_pnl": round(total - account.initial_capital, 2),
        "risk_mode": account.risk_mode,
        "positions": position_list,
    }


def _engine_latest_price(symbol: str) -> float:
    """引擎内存缓存的最新行情价（无则 0）。懒加载避免循环导入。"""
    try:
        from app.paper.engine import get_paper_engine

        return get_paper_engine()._portfolio.last_close.get(symbol, 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def positions_brief() -> str:
    """持仓简述（复盘日报输入用，含股票名称）。"""
    summary = account_summary()
    if not summary["positions"]:
        return ""
    from app.data import database as _db
    from app.data.models import Instrument as _Inst

    symbols = [p["symbol"] for p in summary["positions"]]
    with _db.get_session() as session:
        names = {r.symbol: r.name for r in session.query(_Inst).filter(_Inst.symbol.in_(symbols)).all()}
    lines = [
        f"- {p['symbol']}（{names.get(p['symbol'], '')}）: {p['volume']}股 成本{p['avg_cost']:.2f} "
        f"现价{p['price']:.2f} 盈亏{p['pnl']:+.0f}元"
        for p in summary["positions"]
    ]
    lines.append(f"现金 {summary['cash']:,.0f} 元，总资产 {summary['total']:,.0f} 元")
    return "\n".join(lines)


def reset_account() -> dict:
    """重置模拟账户：清空持仓/委托/净值，现金回到初始。"""
    account = ensure_account()
    with database.get_session() as session:
        session.query(PaperPosition).filter(PaperPosition.account_id == account.id).delete()
        session.query(EquityCurve).filter(
            EquityCurve.scope_type == "paper", EquityCurve.scope_id == account.id
        ).delete()
        from app.data.models import PaperOrder, TradeRecord

        session.query(PaperOrder).filter(PaperOrder.account_id == account.id).delete()
        session.query(TradeRecord).filter(TradeRecord.account_id == account.id).delete()
        row = session.get(PaperAccount, account.id)
        row.cash = row.initial_capital
        session.commit()
    return account_summary()
