"""每日复盘报告：收集本地数据 → LLM 生成 Markdown → 存 ai_report 表。"""
from datetime import datetime

from app.config import get_settings
from app.data import database, loader
from app.data.models import AiReport, News
from app.data.sync import INDEX_HISTORY_WHITELIST
from app.llm.base import get_provider
from app.llm.prompts import REPORT_SYSTEM, report_user_prompt
from app.utils.logger import get_logger

logger = get_logger("llm.daily_report")


def _symbol_names(symbols: list[str]) -> dict[str, str]:
    """代码 → 名称（证券主表，一次查询）。"""
    from app.data.models import Instrument

    with database.get_session() as session:
        rows = session.query(Instrument).filter(Instrument.symbol.in_(symbols)).all()
        return {r.symbol: r.name for r in rows}


def _local_market_lines() -> list[str]:
    """指数+自选最新表现（纯本地库，不碰网络）——日报与预测核验共用。"""
    from app.data import calendar as calendar_module

    latest_trade = calendar_module.latest_trade_date()
    index_names = _symbol_names(sorted(INDEX_HISTORY_WHITELIST))
    lines = [f"最新交易日（日历）：{latest_trade}"]
    stale: list[str] = []
    for sym in sorted(INDEX_HISTORY_WHITELIST):
        latest = loader.get_latest_bar(sym)
        name = index_names.get(sym, "")
        if not latest:
            stale.append(f"{sym}（{name}）无数据")
            continue
        lines.append(
            f"指数 {sym}（{name}）: 收盘 {latest['close']:.2f}（{latest['trade_date']}，"
            f"当日 {latest['pct_change']:+.2f}%）"
        )
        if latest["trade_date"] != latest_trade:
            stale.append(f"{sym}（{name}）截至 {latest['trade_date']}")
    watch = get_settings().watchlist_symbols
    watch_names = _symbol_names(watch)
    for sym in watch:
        latest = loader.get_latest_bar(sym)
        name = watch_names.get(sym, "")
        if not latest:
            stale.append(f"{sym}（{name}）无数据")
            continue
        lines.append(
            f"自选 {sym}（{name}）: 收盘 {latest['close']:.2f}（{latest['trade_date']}，"
            f"当日 {latest['pct_change']:+.2f}%）"
        )
        if latest["trade_date"] != latest_trade:
            stale.append(f"{sym}（{name}）截至 {latest['trade_date']}")
    if stale:
        lines.append(f"⚠️ 数据滞后警告（共 {len(stale)} 项未更新到 {latest_trade}，"
                     f"这些标的不是今日行情，报告分析时请忽略其涨跌幅）: " + "；".join(stale))
    return lines


def _build_market_brief() -> str:
    """指数与自选池最新表现 + 市场情绪（全部来自本地库，含数据日期、名称与当日涨跌幅）。

    铁律：行情必须是最新交易日的。任何一只标的的数据不是最新交易日，都会被
    逐只列出警告，绝不把旧数据冒充今日行情。
    """
    from app.data import calendar as calendar_module

    latest_trade = calendar_module.latest_trade_date()
    lines = _local_market_lines()
    try:
        from app.data.sentiment import get_sentiment_overview

        sentiment = get_sentiment_overview()
        if sentiment["breadth"]:
            b = sentiment["breadth"][0]
            if b["date"] == latest_trade:
                lines.append(
                    f"市场广度（{b['date']}）: 涨停 {b['limit_up']} 家，跌停 {b['limit_down']} 家，"
                    f"上涨 {b['advance']} 家，下跌 {b['decline']} 家"
                )
            else:
                lines.append(f"⚠️ 市场广度数据截至 {b['date']}，尚未更新到 {latest_trade}，已忽略")
        m = sentiment.get("margin", {})
        if m.get("margin_balance") and m.get("date", "") >= latest_trade:
            lines.append(f"两融余额: {m['margin_balance'] / 1e8:.1f} 亿元（{m['date']}）")
    except Exception:  # noqa: BLE001 情绪数据缺失不影响日报
        pass
    return "\n".join(lines) or "暂无本地行情数据（请先同步）"


def _build_positions_brief() -> str:
    from app.paper.account import positions_brief

    return positions_brief()


def _build_news_brief(limit: int = 20) -> str:
    """新闻摘要：只取最新交易日当天的新闻，绝不混入旧闻。"""
    from app.data import calendar as calendar_module

    latest_trade = calendar_module.latest_trade_date()
    with database.get_session() as session:
        rows = (
            session.query(News)
            .filter(News.sentiment_label.isnot(None), News.pub_time >= latest_trade)
            .order_by(News.pub_time.desc())
            .limit(limit).all()
        )
        symbols = {r.symbol for r in rows}
        names = _symbol_names(sorted(symbols))
        return "\n".join(
            f"- [{r.sentiment_label}] {r.symbol}（{names.get(r.symbol, '')}） {r.title}"
            for r in rows
        )


def report_exists_for_date(report_date: str) -> bool:
    """当日报告是否已生成（幂等守卫）。"""
    with database.get_session() as session:
        row = (
            session.query(AiReport)
            .filter(AiReport.report_type == "daily", AiReport.title.like(f"%{report_date}%"))
            .first()
        )
        return row is not None


def _load_prior_report() -> str | None:
    """上一期复盘报告（今日之前最近一期）。"""
    from datetime import datetime

    today = datetime.now().strftime("%Y-%m-%d")
    with database.get_session() as session:
        row = (
            session.query(AiReport)
            .filter(AiReport.report_type == "daily", AiReport.created_at < today)
            .order_by(AiReport.id.desc())
            .first()
        )
        return row.content_md if row else None


def _load_lessons(limit: int = 8) -> list[str]:
    from app.data.models import AiLesson

    with database.get_session() as session:
        rows = session.query(AiLesson).order_by(AiLesson.id.desc()).limit(limit).all()
        return [r.lesson for r in reversed(rows)]


def _save_lessons(lessons: list[str], date: str) -> int:
    from app.data.models import AiLesson

    with database.get_session() as session:
        for lesson in lessons[:3]:
            session.add(AiLesson(date=date, lesson=lesson[:200]))
        session.commit()
    return min(len(lessons), 3)


# ---------------------------------------------------------------- 预测台账

async def extract_predictions(provider, content: str, report_date: str,
                              horizon: str = "daily") -> int:
    """从报告正文提取预测条目入台账（LLM 结构化提取，失败不影响报告）。"""
    from app.data.models import AiPrediction
    from app.llm.prompts import PREDICT_EXTRACT_SYSTEM

    try:
        result = await provider.chat_json(
            [
                {"role": "system", "content": PREDICT_EXTRACT_SYSTEM},
                {"role": "user", "content": content},
            ],
            temperature=0.2, schema_hint="predictions",
        )
        items = result.get("predictions", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("预测条目提取失败: %s", exc)
        return 0
    count = 0
    with database.get_session() as session:
        # 去重：同报告日期+周期重新提取时，先清掉旧条目（报告重新生成不产生重复台账）
        session.query(AiPrediction).filter(
            AiPrediction.report_date == report_date, AiPrediction.horizon == horizon,
            AiPrediction.verdict == "pending",
        ).delete()
        for item in items:
            content_text = str(item.get("content", "")).strip()
            if not content_text:
                continue
            category = str(item.get("category", "大盘"))
            if category not in ("大盘", "板块", "个股"):
                category = "大盘"
            session.add(AiPrediction(
                report_date=report_date, horizon=horizon, category=category,
                symbol=str(item.get("symbol", "") or "")[:10],
                content=content_text[:200], verdict="pending",
            ))
            count += 1
        session.commit()
    logger.info("预测台账入库 %d 条（%s %s）", count, horizon, report_date)
    return count


def _next_trade_date(report_date: str) -> str:
    """报告日期的下一个交易日（= 预测目标日）。"""
    from datetime import timedelta

    from app.data import calendar as calendar_module

    dates = sorted(calendar_module.load_trade_dates())
    for d in dates:
        if d > report_date:
            return d
    # 日历为空（测试/初始化阶段）→ 退化为次日
    try:
        return (datetime.strptime(report_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _market_lines_as_of(target: str) -> list[str]:
    """核验基准日（预测目标日）当天的收盘行情（纯本地库）。"""
    from app.data.models import DailyBar

    symbols = sorted(INDEX_HISTORY_WHITELIST) + get_settings().watchlist_symbols
    names = _symbol_names(symbols)
    lines = [f"核验基准日：{target}（必须只使用该日的收盘数据）"]
    with database.get_session() as session:
        for sym in symbols:
            bar = (
                session.query(DailyBar)
                .filter(DailyBar.symbol == sym, DailyBar.trade_date == target)
                .first()
            )
            if bar:
                lines.append(f"{sym}（{names.get(sym, '')}）: 收盘 {bar.close:.2f}，当日 {bar.pct_change:+.2f}%")
            else:
                lines.append(f"{sym}（{names.get(sym, '')}）: {target} 无数据（该标的当日无法核验）")
    return lines


async def verify_pending_predictions(provider, latest_trade: str) -> tuple[str, int]:
    """核验所有待判定的预测，返回（核验结果文本, 核验条数）。

    铁律：核验必须使用「预测目标日」（报告日的下一交易日）的收盘数据，
    不用核验当天数据——防止数据滞后导致误判。
    判定为 wrong/partial 的自动沉淀教训库；结果回写台账。
    """
    from app.data.models import AiPrediction
    from app.llm.prompts import VERIFY_SYSTEM

    with database.get_session() as session:
        rows = (
            session.query(AiPrediction)
            .filter(AiPrediction.verdict == "pending", AiPrediction.report_date < latest_trade)
            .order_by(AiPrediction.id).all()
        )
        pending = [r for r in rows]
    if not pending:
        return "", 0
    # 按预测目标日分组核验
    groups: dict[str, list] = {}
    for r in pending:
        target = _next_trade_date(r.report_date)
        if target and target <= latest_trade:
            groups.setdefault(target, []).append(r)
    text_lines: list[str] = []
    wrong_lessons: list[str] = []
    n = 0
    for target, group in groups.items():
        market = "\n".join(_market_lines_as_of(target))
        lines = [f"核验基准日：{target}"]
        for i, r in enumerate(group, 1):
            lines.append(f"{i}. [{r.horizon}/{r.category}] {r.symbol} {r.content}")
        try:
            result = await provider.chat_json(
                [
                    {"role": "system", "content": VERIFY_SYSTEM},
                    {"role": "user", "content": "待核验预测：\n" + "\n".join(lines) + "\n\n实际行情：\n" + market},
                ],
                temperature=0.2, schema_hint="verify",
            )
            items = result.get("results", [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("预测核验失败（基准日 %s）: %s", target, exc)
            continue
        verdict_map = {int(i.get("index", 0)): i for i in items if "index" in i}
        with database.get_session() as session:
            for i, r in enumerate(group, 1):
                v = verdict_map.get(i, {})
                verdict = str(v.get("verdict", ""))
                if verdict not in ("correct", "wrong", "partial"):
                    continue
                row = session.get(AiPrediction, r.id)
                row.verdict = verdict
                row.actual = str(v.get("actual", ""))[:200]
                row.lesson = str(v.get("lesson", ""))[:200]
                n += 1
                mark = {"correct": "✅ 对", "wrong": "❌ 错", "partial": "⚠️ 部分对"}[verdict]
                text_lines.append(f"{mark} [{r.horizon}/{r.category}] {r.content} —— 实际：{row.actual}")
                if verdict in ("wrong", "partial") and row.lesson:
                    wrong_lessons.append(row.lesson)
            session.commit()
    if wrong_lessons:
        _save_lessons(wrong_lessons, latest_trade)
    logger.info("预测核验完成 %d 条（错 %d 条沉淀教训）", n, len(wrong_lessons))
    return "\n".join(text_lines), n


CLOSE_GATE = (15, 15)  # 当日报告必须等 15:15 收盘后生成（盘中数据不完整）


def _after_close(report_date: str) -> bool:
    """报告日期为今天时必须已过 15:15；历史日期（周末/假期补生成）不限制。"""
    today = datetime.now().strftime("%Y-%m-%d")
    if report_date < today:
        return True
    now = datetime.now()
    return (now.hour, now.minute) >= CLOSE_GATE


def _delete_stale_intraday(report_date: str) -> bool:
    """删除当日盘中（15:15 前）误生成的旧报告，返回是否删除过。"""
    deleted = False
    with database.get_session() as session:
        rows = (
            session.query(AiReport)
            .filter(AiReport.report_type == "daily", AiReport.title.like(f"%{report_date}%"))
            .all()
        )
        for r in rows:
            if (r.created_at.hour, r.created_at.minute) < CLOSE_GATE:
                session.delete(r)
                deleted = True
        session.commit()
    return deleted


def _ensure_today_data() -> list[str]:
    """确保指数+自选日线更新到最新交易日：缺则触发同步重试，返回仍未更新的标的名。"""
    from app.data import calendar as calendar_module
    from app.data.sync import ensure_fresh_data

    latest_trade = calendar_module.latest_trade_date()
    symbols = sorted(INDEX_HISTORY_WHITELIST) + get_settings().watchlist_symbols

    def stale_symbols() -> list[str]:
        out = []
        for sym in symbols:
            latest = loader.get_latest_bar(sym)
            if not latest or latest["trade_date"] != latest_trade:
                out.append(sym)
        return out

    missing = stale_symbols()
    if missing:
        logger.info("复盘数据校验：%d 只标的未更新到 %s，触发补同步", len(missing), latest_trade)
        ensure_fresh_data()
        missing = stale_symbols()
    return missing


async def generate_daily_report(provider=None, date: str | None = None) -> dict:
    """生成并入库复盘日报。

    铁律（防止复盘出现非今日数据）：
    1. 报告日期 = 最新交易日（非自然日）；
    2. 当天 15:15 收盘前不生成（盘中数据不完整）；
    3. 盘中误生成的旧报告自动删除重建；
    4. 生成前校验指数+自选日线已更新到最新交易日，缺则补同步重试。
    """
    from app.data import calendar as calendar_module

    provider = provider or get_provider()
    report_date = date or calendar_module.latest_trade_date()
    if not _after_close(report_date):
        logger.info("尚未收盘（%s），跳过复盘生成，等收盘后数据", report_date)
        return {"skipped": True, "reason": "盘中不生成复盘，收盘后自动生成"}
    if report_exists_for_date(report_date) and date is None:
        if not _delete_stale_intraday(report_date):
            logger.info("今日复盘已存在（收盘后生成），跳过")
            with database.get_session() as session:
                row = (
                    session.query(AiReport)
                    .filter(AiReport.report_type == "daily", AiReport.title.like(f"%{report_date}%"))
                    .first()
                )
                return {"id": row.id, "title": row.title, "content_md": row.content_md,
                        "model": row.model, "skipped": True}
        logger.info("检测到盘中旧报告，删除后重新生成")
    missing: list[str] = []
    if date is None:
        # 自动生成今日报告：数据必须是最新交易日的，缺则补同步
        missing = _ensure_today_data()
        # 只有「指数大面积缺失」才拒绝生成（市场行情都不齐，报告无意义）；
        # 个别自选股缺失 → 照常生成，报告里逐只列出警告，绝不因几只股票拖住整份报告
        index_missing = [s for s in missing if s in INDEX_HISTORY_WHITELIST]
        if len(index_missing) > max(2, len(INDEX_HISTORY_WHITELIST) // 2):
            raise RuntimeError(f"复盘数据校验失败：{len(index_missing)} 个指数未更新到 {report_date}，"
                               "数据源不可用，拒绝生成含旧数据的报告（稍后自动重试）")
    market = _build_market_brief()
    positions = _build_positions_brief()
    news = _build_news_brief()
    prior_report = _load_prior_report()
    lessons = _load_lessons()

    # 预测台账核验：上期每条预测逐条判定对错（错/部分对的沉淀教训）
    verify_text = ""
    if date is None:
        verify_text, _ = await verify_pending_predictions(provider, report_date)

    from app.llm.prompts import LESSONS_SYSTEM

    content = await provider.chat([
        {"role": "system", "content": REPORT_SYSTEM},
        {"role": "user", "content": report_user_prompt(
            market, positions, news, report_date,
            prior_report=prior_report or "",
            lessons="\n".join(f"- {l}" for l in lessons),
            verify_text=verify_text,
        )},
    ], temperature=0.5)

    # 提炼本期经验教训（从「昨日预测回顾」中沉淀，供后续报告引用改进）
    new_lessons: list[str] = []
    try:
        lessons_result = await provider.chat_json(
            [
                {"role": "system", "content": LESSONS_SYSTEM},
                {"role": "user", "content": f"今日复盘报告：\n{content}"},
            ],
            temperature=0.3, schema_hint="lessons",
        )
        new_lessons = [str(l)[:200] for l in lessons_result.get("lessons", []) if str(l).strip()]
    except Exception as exc:  # noqa: BLE001 教训提炼失败不影响报告
        logger.warning("经验教训提炼失败: %s", exc)

    with database.get_session() as session:
        row = AiReport(
            report_type="daily",
            title=f"每日复盘 {report_date}",
            content_md=content,
            meta_json=f'{{"date": "{report_date}", "news_count": {len(news.splitlines()) if news else 0}}}',
            model=provider.name,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        n_lessons = _save_lessons(new_lessons, report_date) if new_lessons else 0
        logger.info("复盘日报已生成: id=%s date=%s model=%s 教训=%d 条",
                    row.id, report_date, provider.name, n_lessons)

        from app.utils.notify import send_notification

        # 预测台账：本期「明日关注」条目入库，供次日自动核验
        n_pred = await extract_predictions(provider, content, report_date, horizon="daily")
        send_notification("AI 复盘日报已生成", f"{row.title}（{provider.name}）\n内容见 Web 面板 AI 报告页。")
        return {"id": row.id, "title": row.title, "content_md": row.content_md,
                "model": provider.name, "lessons": new_lessons, "predictions": n_pred}
