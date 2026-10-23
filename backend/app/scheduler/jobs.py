"""APScheduler 定时任务（与 FastAPI 同进程，单例）。

所有任务函数内部全量捕获异常（否则 APScheduler 会吞掉后续触发）；
行情类任务带交易日/交易时段守卫。
"""
import asyncio

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.data import calendar
from app.data.sync import sync_index_bars, sync_instruments, sync_news, sync_watchlist_bars
from app.llm.tasks.daily_report import generate_daily_report
from app.llm.tasks.news_sentiment import score_news
from app.paper.engine import get_paper_engine
from app.utils.logger import get_logger
from app.utils.timeutil import in_trading_hours

logger = get_logger("scheduler")

_scheduler: BackgroundScheduler | None = None


def _safe(name: str):
    """任务包装：异常只记日志，不中断调度。"""

    def decorator(fn):
        def wrapper():
            try:
                fn()
            except Exception:  # noqa: BLE001
                logger.exception("定时任务失败: %s", name)

        wrapper.__name__ = name
        return wrapper

    return decorator


# ---------------------------------------------------------------- 任务函数

@_safe("sync_instruments")
def job_sync_instruments():
    sync_instruments(full=True)  # 全市场名单（约 10 秒），支撑按名称搜索


@_safe("sync_watchlist_bars")
def job_sync_watchlist_bars():
    if not calendar.is_trade_day():
        return
    sync_watchlist_bars()


@_safe("sync_index_bars")
def job_sync_index_bars():
    if not calendar.is_trade_day():
        return
    sync_index_bars()


@_safe("sync_breadth")
def job_sync_breadth():
    """收盘后同步市场广度（涨跌停/涨跌家数）。"""
    if not calendar.is_trade_day():
        return
    from app.data.sentiment import sync_breadth

    sync_breadth()


@_safe("refresh_tscreen")
def job_refresh_tscreen():
    """收盘后刷新全市场做T候选池（快照粗筛 + 定向同步 + 精筛）。"""
    if not calendar.is_trade_day():
        return
    from app.data.tscreener import refresh_market_tscreen

    result = refresh_market_tscreen()
    logger.info("全市场做T候选刷新: 粗筛 %d 只，候选 %d 只",
                result["coarse"], len(result["candidates"]))


@_safe("refresh_board_screen")
def job_refresh_board_screen():
    """收盘后刷新打板候选池（涨停/连板标注）。"""
    if not calendar.is_trade_day():
        return
    from app.data.board_screener import refresh_board_screen

    result = refresh_board_screen()
    logger.info("打板候选刷新: %d 个候选", result["coarse"])


@_safe("sync_hour_bars")
def job_sync_hour_bars():
    """收盘后积累自选池 60 分钟线（新浪源只保留近端数据，需每日积累）。"""
    if not calendar.is_trade_day():
        return
    from app.data.sync import sync_hour_bars

    sync_hour_bars()


@_safe("fetch_news")
def job_fetch_news():
    if not calendar.is_trade_day():
        return
    sync_news()


@_safe("news_sentiment")
def job_news_sentiment():
    if not calendar.is_trade_day():
        return
    asyncio.run(score_news(30))


@_safe("daily_report")
def job_daily_report():
    if not calendar.is_trade_day():
        return
    asyncio.run(generate_daily_report())


@_safe("news_forecast")
def job_news_forecast():
    """收盘后生成新闻热点明日行情预测。"""
    if not calendar.is_trade_day():
        return
    from app.llm.tasks.news_forecast import generate_news_forecast

    asyncio.run(generate_news_forecast())


@_safe("paper_quote_poll")
def job_paper_poll():
    if not calendar.is_trade_day() or not in_trading_hours():
        return
    get_paper_engine().process_quotes()


@_safe("paper_settle")
def job_paper_settle():
    if not calendar.is_trade_day():
        return
    get_paper_engine().settle()


@_safe("weekly_report")
def job_weekly_report():
    """周五收盘后生成周报（本周复盘 + 下周预测，预测入台账下周自动核验）。"""
    from app.llm.tasks.weekly_report import generate_weekly_report

    result = asyncio.run(generate_weekly_report())
    if result.get("skipped"):
        logger.info("周报跳过: %s", result.get("reason"))
    else:
        logger.info("周报已生成: %s 预测 %d 条", result.get("title"), result.get("predictions", 0))


@_safe("freshness_watchdog")
def job_freshness_watchdog():
    """数据新鲜度自愈看护：每 30 分钟检查一次，滞后则自动补齐（失败自动重试）。

    解决"老是没有当日数据"：任何同步失败都会在 30 分钟内自动重试自愈。
    """
    if not calendar.is_trade_day():
        return
    from app.data.sync import data_is_fresh, ensure_fresh_data

    if data_is_fresh():
        return
    result = ensure_fresh_data()
    logger.info("新鲜度看护补齐: %s（失败: 自选 %s / 指数 %s）",
                result["status"], result["bars"]["failed"], result["index_bars"]["failed"])


@_safe("ml_auto_retrain")
def job_ml_auto_retrain():
    """每周自动重训 ML 模型，OOS AUC 明显退化时推送告警（需 ML_AUTO_RETRAIN=1）。"""
    from app.config import get_settings

    if not get_settings().ml_auto_retrain:
        return
    from datetime import datetime

    from app.ml.persistence import list_models
    from app.ml.train import train_pipeline

    models = list_models()
    prev_auc = None
    if models:
        prev_auc = models[0].get("metrics", {}).get("oos_auc")
    report = train_pipeline(
        get_settings().watchlist_symbols, name=f"auto_{datetime.now():%Y%m%d}"
    )
    from app.utils.notify import send_notification

    if prev_auc is not None and prev_auc - report["oos_auc"] > 0.05:
        send_notification(
            "ML 模型性能退化告警",
            f"OOS AUC 从 {prev_auc:.3f} 降至 {report['oos_auc']:.3f}（模型 {report['name']}）",
        )


# ---------------------------------------------------------------- 调度器

def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    _scheduler.add_job(job_sync_instruments, CronTrigger(day_of_week="mon", hour=6, minute=10),
                       id="sync_instruments")
    _scheduler.add_job(job_sync_watchlist_bars, CronTrigger(day_of_week="mon-fri", hour=8, minute=30),
                       id="sync_watchlist_morning")
    _scheduler.add_job(job_sync_watchlist_bars, CronTrigger(day_of_week="mon-fri", hour=16, minute=5),
                       id="sync_watchlist_close")
    _scheduler.add_job(job_sync_index_bars, CronTrigger(day_of_week="mon-fri", hour=16, minute=10),
                       id="sync_index_bars")
    _scheduler.add_job(job_sync_breadth, CronTrigger(day_of_week="mon-fri", hour=16, minute=5),
                       id="sync_breadth")
    _scheduler.add_job(job_sync_hour_bars, CronTrigger(day_of_week="mon-fri", hour=16, minute=30),
                       id="sync_hour_bars")
    _scheduler.add_job(job_refresh_tscreen, CronTrigger(day_of_week="mon-fri", hour=16, minute=20),
                       id="refresh_tscreen")
    _scheduler.add_job(job_refresh_board_screen, CronTrigger(day_of_week="mon-fri", hour=16, minute=25),
                       id="refresh_board_screen")
    _scheduler.add_job(job_fetch_news, CronTrigger(day_of_week="mon-fri", hour=17, minute=0),
                       id="fetch_news")
    _scheduler.add_job(job_news_sentiment, CronTrigger(day_of_week="mon-fri", hour=17, minute=30),
                       id="news_sentiment")
    _scheduler.add_job(job_daily_report, CronTrigger(day_of_week="mon-fri", hour=18, minute=0),
                       id="daily_report")
    _scheduler.add_job(job_weekly_report, CronTrigger(day_of_week="fri", hour=18, minute=30),
                       id="weekly_report")
    _scheduler.add_job(job_news_forecast, CronTrigger(day_of_week="mon-fri", hour=17, minute=45),
                       id="news_forecast")
    _scheduler.add_job(job_paper_poll, IntervalTrigger(seconds=30), id="paper_quote_poll")
    _scheduler.add_job(job_paper_settle, CronTrigger(day_of_week="mon-fri", hour=15, minute=5),
                       id="paper_settle")
    _scheduler.add_job(job_ml_auto_retrain, CronTrigger(day_of_week="sun", hour=2, minute=17),
                       id="ml_auto_retrain")
    _scheduler.add_job(job_freshness_watchdog, IntervalTrigger(minutes=30),
                       id="freshness_watchdog")
    _scheduler.start()
    logger.info("调度器已启动：%d 个任务", len(_scheduler.get_jobs()))


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def scheduler_status() -> dict:
    if _scheduler is None:
        return {"running": False, "jobs": []}
    jobs = []
    for job in _scheduler.get_jobs():
        trigger = str(job.trigger)
        jobs.append({
            "id": job.id,
            "next_run": job.next_run_time.strftime("%Y-%m-%d %H:%M:%S") if job.next_run_time else "",
            "trigger": trigger,
        })
    return {"running": _scheduler.running, "jobs": jobs}
