"""每周复盘周报：本周行情/操作/预测战绩聚合 → LLM 生成 → 下周预测入台账闭环。

铁律（与日报一致）：
1. 只在周五（本周最后交易日）收盘后生成，其他时间拒绝；
2. 幂等：同周周报已存在则跳过；
3. 上周周报的「下周预测」先自动核验，核验结果写入本期周报；
4. 本期「下周预测」提取入预测台账（horizon=weekly），下周自动核验。
"""
from datetime import datetime

from app.config import get_settings
from app.data import calendar, database, loader
from app.data.models import AiPrediction, AiReport, EquityCurve, PaperOrder
from app.data.sync import INDEX_HISTORY_WHITELIST
from app.llm.base import get_provider
from app.llm.prompts import WEEKLY_SYSTEM, weekly_user_prompt
from app.utils.logger import get_logger

logger = get_logger("llm.weekly_report")


def _week_dates(last_date: str) -> list[str]:
    """最近一个自然周（周一~last_date）的交易日列表。"""
    import datetime as dt

    last = datetime.strptime(last_date, "%Y-%m-%d").date()
    monday = last - dt.timedelta(days=last.weekday())
    trade_set = calendar.trade_date_set()
    dates = []
    d = monday
    while d <= last:
        s = d.strftime("%Y-%m-%d")
        if s in trade_set:
            dates.append(s)
        d += dt.timedelta(days=1)
    return dates


def _symbol_names(symbols: list[str]) -> dict[str, str]:
    from app.data.models import Instrument

    with database.get_session() as session:
        rows = session.query(Instrument).filter(Instrument.symbol.in_(symbols)).all()
        return {r.symbol: r.name for r in rows}


def _week_performance(symbols: list[str], names: dict[str, str], week: list[str]) -> list[str]:
    """各标的周表现：周涨跌幅 = (周末收盘-周初收盘)/周初收盘。"""
    lines: list[str] = []
    with database.get_session() as session:
        from app.data.models import DailyBar

        for sym in symbols:
            rows = (
                session.query(DailyBar)
                .filter(DailyBar.symbol == sym, DailyBar.trade_date.in_(week))
                .order_by(DailyBar.trade_date).all()
            )
            if len(rows) < 2:
                continue
            first, last = rows[0], rows[-1]
            week_pct = (last.close / first.close - 1) * 100 if first.close else 0.0
            lines.append(f"{sym}（{names.get(sym, '')}）: 周初 {first.close:.2f} → "
                         f"周末 {last.close:.2f}，周涨跌 {week_pct:+.2f}%"
                         f"（{first.trade_date}~{last.trade_date}）")
    return lines


def _build_weekly_brief(last_date: str) -> str:
    """本周数据聚合：行情、模拟账户操作、预测战绩。"""
    week = _week_dates(last_date)
    if len(week) < 2:
        return f"本周交易日不足（{week}），无法生成周报"
    lines = [f"本周交易日：{'、'.join(week)}"]
    symbols = sorted(INDEX_HISTORY_WHITELIST)
    names = _symbol_names(symbols)
    lines.append("—— 指数周表现 ——")
    lines += _week_performance(symbols, names, week)
    watch = get_settings().watchlist_symbols
    watch_names = _symbol_names(watch)
    lines.append("—— 自选周表现 ——")
    lines += _week_performance(watch, watch_names, week)

    # 模拟账户本周操作
    with database.get_session() as session:
        orders = (
            session.query(PaperOrder)
            .filter(PaperOrder.status == "filled",
                    PaperOrder.created_at >= f"{week[0]} 00:00:00",
                    PaperOrder.created_at <= f"{week[-1]} 23:59:59")
            .order_by(PaperOrder.created_at).all()
        )
        if orders:
            order_names = _symbol_names([o.symbol for o in orders])
            lines.append("—— 本周成交 ——")
            for o in orders:
                direction = "买入" if o.direction == "buy" else "卖出"
                lines.append(
                    f"{o.created_at.strftime('%m-%d %H:%M')} {direction} {o.symbol}"
                    f"（{order_names.get(o.symbol, '')}）{o.filled_volume} 股 @{o.filled_price:.2f}"
                )
        else:
            lines.append("—— 本周成交 ——\n本周无成交")

        # 本周权益变化（周初 vs 周末）
        account_rows = (
            session.query(EquityCurve)
            .filter(EquityCurve.scope_type == "paper", EquityCurve.date.in_(week))
            .order_by(EquityCurve.date).all()
        )
        if len(account_rows) >= 2:
            first, last = account_rows[0], account_rows[-1]
            week_ret = (last.equity / first.equity - 1) * 100 if first.equity else 0.0
            lines.append(f"—— 账户权益 ——\n周初 {first.equity:,.0f} → 周末 {last.equity:,.0f}，"
                         f"周收益 {week_ret:+.2f}%（{first.date}~{last.date}）")

        # 本周预测战绩
        pred_rows = (
            session.query(AiPrediction)
            .filter(AiPrediction.report_date.in_(week))
            .all()
        )
        if pred_rows:
            stats: dict[str, int] = {}
            for p in pred_rows:
                stats[p.verdict] = stats.get(p.verdict, 0) + 1
            lines.append("—— 本周预测战绩 ——\n" +
                         f"共 {len(pred_rows)} 条：对 {stats.get('correct', 0)}、"
                         f"错 {stats.get('wrong', 0)}、部分对 {stats.get('partial', 0)}、"
                         f"待核验 {stats.get('pending', 0)}")
            for p in pred_rows:
                if p.verdict in ("correct", "wrong", "partial"):
                    mark = {"correct": "✅", "wrong": "❌", "partial": "⚠️"}[p.verdict]
                    lines.append(f"{mark} [{p.category}] {p.content} —— {p.actual}")
    return "\n".join(lines)


def weekly_exists(last_date: str) -> bool:
    with database.get_session() as session:
        row = (
            session.query(AiReport)
            .filter(AiReport.report_type == "weekly", AiReport.title.like(f"%{last_date}%"))
            .first()
        )
        return row is not None


async def generate_weekly_report(provider=None, date: str | None = None) -> dict:
    """生成周报；非周五收盘后拒绝生成（显式 date 参数可强制）。"""
    provider = provider or get_provider()
    last_date = date or calendar.latest_trade_date()
    if date is None:
        last = datetime.strptime(last_date, "%Y-%m-%d")
        if last.weekday() != 4:  # 周五
            return {"skipped": True, "reason": f"周报只在周五收盘后生成（今天是周{last.weekday() + 1}）"}
        if weekly_exists(last_date):
            logger.info("本周周报已存在，跳过")
            return {"skipped": True, "reason": "本周周报已存在"}
    brief = _build_weekly_brief(last_date)

    # 核验上周 pending 预测（含周报预测）
    from app.llm.tasks.daily_report import verify_pending_predictions

    verify_text, _ = await verify_pending_predictions(provider, last_date)

    # 上一期周报（对照用）
    with database.get_session() as session:
        prior = (
            session.query(AiReport)
            .filter(AiReport.report_type == "weekly")
            .order_by(AiReport.created_at.desc()).first()
        )

    content = await provider.chat([
        {"role": "system", "content": WEEKLY_SYSTEM},
        {"role": "user", "content": weekly_user_prompt(
            brief, last_date,
            prior_weekly=prior.content_md if prior else "",
            verify_text=verify_text,
        )},
    ], temperature=0.5)

    with database.get_session() as session:
        row = AiReport(
            report_type="weekly",
            title=f"每周复盘 {last_date}",
            content_md=content,
            meta_json=f'{{"date": "{last_date}"}}',
            model=provider.name,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
    # 下周预测入台账（horizon=weekly，下周自动核验）
    from app.llm.tasks.daily_report import extract_predictions

    n_pred = await extract_predictions(provider, content, last_date, horizon="weekly")
    logger.info("周报已生成: id=%s date=%s 预测=%d 条", row.id, last_date, n_pred)
    return {"id": row.id, "title": row.title, "content_md": row.content_md,
            "model": provider.name, "predictions": n_pred}
