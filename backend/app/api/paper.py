"""模拟交易 API：账户概览、下单（含 AI 风控，支持名称下单）、撤单、净值、重置。"""
import re

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.data import database
from app.data.models import EquityCurve, PaperOrder
from app.engine.objects import Direction
from app.paper import account as account_service
from app.paper.engine import get_paper_engine
from app.paper.risk import RiskBlocked, run_risk_check
from app.utils.logger import get_logger

router = APIRouter(tags=["paper"])
logger = get_logger("api.paper")


@router.get("/paper/account")
def account():
    return account_service.account_summary()


@router.get("/paper/orders")
def orders(status: str = "", limit: int = 50):
    from app.data.models import Instrument

    with database.get_session() as session:
        query = session.query(PaperOrder)
        if status:
            query = query.filter(PaperOrder.status == status)
        rows = query.order_by(PaperOrder.id.desc()).limit(limit).all()
        symbols = {r.symbol for r in rows}
        names = {
            r.symbol: r.name
            for r in session.query(Instrument).filter(Instrument.symbol.in_(symbols)).all()
        }
        return [
            {"id": r.id, "symbol": r.symbol, "name": names.get(r.symbol, ""),
             "direction": r.direction,
             "order_type": r.order_type, "price": r.price, "volume": r.volume,
             "filled_price": r.filled_price, "filled_volume": r.filled_volume,
             "status": r.status, "ai_check_json": r.ai_check_json,
             "reject_reason": r.reject_reason,
             "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S")}
            for r in rows
        ]


class OrderRequest(BaseModel):
    symbol: str
    direction: str  # buy / sell
    volume: int = 0  # 0 = 市价全仓买入
    order_type: str = "market"  # market / limit
    price: float = 0.0
    condition_type: str = ""  # ""/stop_loss/take_profit
    trigger_price: float = 0.0


def _resolve_symbol(symbol: str) -> str:
    """下单代码解析：6 位数字直接返回；否则按名称模糊匹配证券主表。"""
    if re.fullmatch(r"\d{6}", symbol.strip()):
        return symbol.strip()
    from app.data.models import Instrument

    with database.get_session() as session:
        rows = (
            session.query(Instrument)
            .filter(Instrument.name.like(f"%{symbol.strip()}%"))
            .limit(10).all()
        )
        if not rows:
            raise HTTPException(400, f"找不到股票「{symbol}」，请确认名称或改用 6 位代码")
        if len(rows) > 1:
            options = "、".join(f"{r.name}({r.symbol})" for r in rows)
            raise HTTPException(400, f"名称匹配到多只股票：{options}，请用代码下单或输入更完整名称")
        return rows[0].symbol


@router.post("/paper/orders")
def place_order(req: OrderRequest):
    if req.direction not in ("buy", "sell"):
        raise HTTPException(400, "direction 仅支持 buy/sell")
    if req.order_type not in ("market", "limit"):
        raise HTTPException(400, "order_type 仅支持 market/limit")
    symbol = _resolve_symbol(req.symbol)  # 名称 → 代码
    if req.order_type == "limit" and req.price <= 0:
        raise HTTPException(400, "限价单必须提供价格")
    if req.direction == "sell" and req.volume <= 0:
        raise HTTPException(400, "卖出数量必须大于 0（买入 0 = 市价全仓）")
    if req.condition_type and req.condition_type not in ("stop_loss", "take_profit"):
        raise HTTPException(400, "condition_type 仅支持 stop_loss/take_profit")

    # 1. AI 风控（block 模式被拒 → 400）
    try:
        ai_check = run_risk_check(symbol, req.direction, req.price, req.volume, req.order_type)
    except RiskBlocked as exc:
        raise HTTPException(400, f"AI 风控拦截：{exc}") from exc

    # 2. 下单入库（引擎轮询撮合）
    try:
        row = get_paper_engine().place_order(
            symbol, Direction(req.direction), req.volume,
            price_type=req.order_type, price=req.price, ai_check=ai_check,
            condition_type=req.condition_type, trigger_price=req.trigger_price,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    # 3. 后台立即撮合一轮（行情源慢时不影响接口响应；交易时段 30s 轮询也会撮合）
    from app.utils import tasks

    tasks.submit(get_paper_engine().process_quotes)

    return {"order_id": row.id, "status": row.status, "ai_check": ai_check}


@router.delete("/paper/orders/{order_id}")
def cancel_order(order_id: int):
    with database.get_session() as session:
        row = session.get(PaperOrder, order_id)
        if row is None:
            raise HTTPException(404, "委托不存在")
        if row.status != "submitted":
            raise HTTPException(400, f"委托状态为 {row.status}，无法撤销")
        row.status = "cancelled"
        session.commit()
        return {"order_id": order_id, "status": "cancelled"}


@router.get("/paper/equity")
def equity():
    account = account_service.ensure_account()
    with database.get_session() as session:
        rows = (
            session.query(EquityCurve)
            .filter(EquityCurve.scope_type == "paper", EquityCurve.scope_id == account.id)
            .order_by(EquityCurve.date).all()
        )
        return [
            {"date": r.date, "equity": r.equity, "cash": r.cash, "return_rate": r.return_rate}
            for r in rows
        ]


@router.post("/paper/reset")
def reset():
    return account_service.reset_account()


@router.post("/paper/import-ths")
def import_ths(positions_file: UploadFile | None = File(None),
               trades_file: UploadFile | None = File(None)):
    """同花顺远航版导出文件导入：持仓镜像覆盖 + 交割单追加委托记录。"""
    from app.data.ths_import import apply_positions, apply_trades, parse_positions, parse_trades

    if positions_file is None and trades_file is None:
        raise HTTPException(400, "请至少上传持仓或交割单文件之一")
    warnings: list[str] = []
    n_pos = n_trade = 0
    if positions_file is not None:
        try:
            rows = parse_positions(positions_file.file.read(),
                                   positions_file.filename or "positions.xls")
        except ValueError as exc:
            raise HTTPException(400, f"持仓文件解析失败：{exc}") from exc
        n_pos, w = apply_positions(rows)
        warnings += w
    if trades_file is not None:
        try:
            rows = parse_trades(trades_file.file.read(),
                                trades_file.filename or "trades.xls")
        except ValueError as exc:
            raise HTTPException(400, f"交割单文件解析失败：{exc}") from exc
        n_trade, w = apply_trades(rows)
        warnings += w
    get_paper_engine().reload()  # 引擎内存状态立即重载
    return {"positions_imported": n_pos, "trades_imported": n_trade, "warnings": warnings}


@router.get("/paper/export-ths")
def export_ths():
    """反向导出：模拟账户持仓/委托 → Excel（供与同花顺对照）。"""
    import io

    import pandas as pd
    from fastapi.responses import StreamingResponse

    summary = account_service.account_summary()
    pos_df = pd.DataFrame(summary["positions"], columns=[
        "symbol", "name", "volume", "available_volume", "avg_cost",
        "price", "market_value", "pnl",
    ])
    pos_df.columns = ["证券代码", "证券名称", "股票余额", "可用余额",
                      "成本价", "现价", "市值", "浮动盈亏"]
    with database.get_session() as session:
        rows = session.query(PaperOrder).order_by(PaperOrder.id.desc()).limit(200).all()
    order_df = pd.DataFrame([{
        "成交时间": r.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "证券代码": r.symbol,
        "操作": "买入" if r.direction == "buy" else "卖出",
        "成交价": r.filled_price, "成交数量": r.filled_volume,
        "状态": r.status, "手续费": 0.0,
    } for r in rows])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pos_df.to_excel(writer, sheet_name="持仓", index=False)
        order_df.to_excel(writer, sheet_name="委托", index=False)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=weiwolianghua_paper.xlsx"},
    )
