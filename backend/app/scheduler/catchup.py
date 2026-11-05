"""进入系统补跑：用户打开系统时（收盘后）立即拉数据+补生成当日日报等。

与启动补跑共用同一套逻辑：数据新鲜度 → 日报/周报/候选池/解读/风险/热点预测逐项自愈。
收盘前（15:15 前）直接跳过——盘中数据不完整，不浪费网络请求。
"""
import threading
from datetime import datetime

from app.utils.logger import get_logger

logger = get_logger("scheduler.catchup")

_running = threading.Event()  # 防重复排队（启动补跑与进入补跑并发时只跑一份）


def _after_close() -> bool:
    now = datetime.now()
    return (now.hour, now.minute) >= (15, 15)


def run_catch_up(force: bool = False) -> dict:
    """收盘后补跑全部缺失数据与报告。已在跑则跳过（返回 skipped）。"""
    if _running.is_set():
        return {"skipped": True}
    if not force and not _after_close():
        return {"skipped": True, "reason": "收盘前不补跑（盘中数据不完整）"}
    _running.set()
    try:
        return _catch_up_body()
    finally:
        _running.clear()


def _catch_up_body() -> dict:
    from app.data import calendar, database
    from app.data.models import AiReport, BoardWatch, MinuteBar, PositionRisk, TscreenWatch
    from app.data.sync import ensure_fresh_data

    lt = calendar.latest_trade_date()
    result: dict = {"latest_trade": lt, "repaired": []}

    # 1) 行情/广度/新闻 + 复盘
    if calendar.is_trade_day():
        sync = ensure_fresh_data()
        result["sync"] = sync["status"]
        import asyncio

        from app.llm.tasks.daily_report import generate_daily_report
        from app.llm.tasks.news_sentiment import score_news

        try:
            asyncio.run(score_news(30))
        except Exception:  # noqa: BLE001
            logger.exception("新闻情绪打分失败（不影响使用）")
        try:
            rep = asyncio.run(generate_daily_report())  # 幂等：已有则跳过
            if not rep.get("skipped"):
                result["repaired"].append("复盘日报")
        except Exception:  # noqa: BLE001
            logger.exception("补跑日报失败（稍后重试）")
    else:
        result["sync"] = "nontrade"

    # 2) 漏跑任务自愈（候选池/60分钟线/个股解读/风险/热点预测/周五周报）
    with database.get_session() as session:
        board_latest = session.query(BoardWatch.date).order_by(BoardWatch.date.desc()).first()
        ts_latest = session.query(TscreenWatch.date).order_by(TscreenWatch.date.desc()).first()
        has_hour_bars = session.query(MinuteBar).first() is not None
    from app.utils import tasks

    if board_latest is None or board_latest[0] < lt:
        tasks.submit(_safe_refresh_board)
        result["repaired"].append("打板候选池")
    if ts_latest is None or ts_latest[0] < lt:
        tasks.submit(_safe_refresh_tscreen)
        result["repaired"].append("做T观察区")
    if not has_hour_bars:
        tasks.submit(_safe_sync_hour_bars)
        result["repaired"].append("60分钟线")

    # 本地 finbert 情绪打分（ML 因子数据源；模型未就绪/无未打分新闻时静默跳过）
    with database.get_session() as session:
        from app.data.models import News

        unscored = session.query(News.id).filter(News.finbert_score.is_(None)).first()
    if unscored is not None:
        tasks.submit(_safe_news_finbert)
        result["repaired"].append("新闻情绪因子打分")

    with database.get_session() as session:
        stock_done = session.query(AiReport).filter(
            AiReport.report_type == "stock", AiReport.title.like(f"%{lt}%")).count()
    if stock_done == 0:
        tasks.submit(_safe_symbol_analysis)
        result["repaired"].append("个股解读")

    with database.get_session() as session:
        risk_done = session.query(PositionRisk).filter(PositionRisk.date == lt).count()
    if risk_done == 0:
        tasks.submit(_safe_position_risk)
        result["repaired"].append("持仓风险分析")

    from app.llm.tasks.daily_report import _next_trade_date

    target = _next_trade_date(lt)
    with database.get_session() as session:
        forecast_done = session.query(AiReport).filter(
            AiReport.report_type == "news_forecast", AiReport.title.like(f"%{target}%")).count()
    if forecast_done == 0:
        tasks.submit(_safe_news_forecast)
        result["repaired"].append("热点预测")

    if datetime.strptime(lt, "%Y-%m-%d").weekday() == 4:
        with database.get_session() as session:
            weekly = session.query(AiReport).filter(
                AiReport.report_type == "weekly", AiReport.title.like(f"%{lt}%")).first()
        if weekly is None:
            tasks.submit(_safe_weekly_report)
            result["repaired"].append("周报")

    if result["repaired"]:
        logger.info("进入系统补跑：%s", "、".join(result["repaired"]))
    return result


# ---------------------------------------------------------------- 各补跑子任务（单只失败不影响其余）

def _safe_refresh_board():
    try:
        from app.data.board_screener import refresh_board_screen

        refresh_board_screen()
    except Exception:  # noqa: BLE001
        logger.exception("补跑打板候选失败")


def _safe_refresh_tscreen():
    try:
        from app.data.tscreener import refresh_market_tscreen

        refresh_market_tscreen()
    except Exception:  # noqa: BLE001
        logger.exception("补跑做T候选失败")


def _safe_sync_hour_bars():
    try:
        from app.data.sync import sync_hour_bars

        sync_hour_bars()
    except Exception:  # noqa: BLE001
        logger.exception("补跑60分钟线失败")


def _safe_news_finbert():
    try:
        from app.scheduler.jobs import job_news_finbert

        job_news_finbert()
    except Exception:  # noqa: BLE001
        logger.exception("补跑新闻情绪因子打分失败")


def _safe_symbol_analysis():
    import asyncio

    from app.llm.tasks.symbol_analysis import analyze_symbol
    from app.paper import account as account_service

    for sym in [p["symbol"] for p in account_service.account_summary()["positions"]]:
        try:
            asyncio.run(analyze_symbol(sym))
        except Exception:  # noqa: BLE001
            logger.exception("补跑个股解读失败: %s", sym)


def _safe_position_risk():
    import asyncio

    from app.llm.tasks.position_risk import analyze_position_risk
    from app.paper import account as account_service

    for sym in [p["symbol"] for p in account_service.account_summary()["positions"]]:
        try:
            asyncio.run(analyze_position_risk(sym))
        except Exception:  # noqa: BLE001
            logger.exception("补跑持仓风险分析失败: %s", sym)


def _safe_news_forecast():
    try:
        import asyncio

        from app.llm.tasks.news_forecast import generate_news_forecast

        asyncio.run(generate_news_forecast())
    except Exception:  # noqa: BLE001
        logger.exception("补跑热点预测失败")


def _safe_weekly_report():
    try:
        import asyncio

        from app.llm.tasks.weekly_report import generate_weekly_report

        asyncio.run(generate_weekly_report())
    except Exception:  # noqa: BLE001
        logger.exception("补跑周报失败")
