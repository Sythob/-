"""AI API：报告列表/详情、手动生成日报、批量新闻情绪打分。"""
import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.data import database
from app.data.models import AiReport
from app.llm.tasks.daily_report import generate_daily_report
from app.llm.tasks.news_sentiment import score_news

router = APIRouter(tags=["ai"])


@router.get("/ai/reports")
def reports(type: str = "", limit: int = 50):
    with database.get_session() as session:
        query = session.query(AiReport)
        if type:
            query = query.filter(AiReport.report_type == type)
        rows = query.order_by(AiReport.id.desc()).limit(limit).all()
        return [
            {"id": r.id, "report_type": r.report_type, "title": r.title,
             "model": r.model, "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S")}
            for r in rows
        ]


@router.get("/ai/reports/{report_id}")
def report_detail(report_id: int):
    with database.get_session() as session:
        row = session.get(AiReport, report_id)
        if row is None:
            raise HTTPException(404, "报告不存在")
        return {
            "id": row.id, "report_type": row.report_type, "title": row.title,
            "content_md": row.content_md, "meta_json": row.meta_json,
            "model": row.model, "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        }


@router.post("/ai/reports/generate")
def generate_report():
    try:
        return asyncio.run(generate_daily_report())
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"日报生成失败: {exc}") from exc


@router.get("/ai/predictions")
def predictions(horizon: str = "", verdict: str = "", limit: int = 100):
    """预测台账：全部预测条目 + 准确率统计（AI 报告页「预测战绩」）。"""
    from app.data.models import AiPrediction

    with database.get_session() as session:
        query = session.query(AiPrediction)
        if horizon:
            query = query.filter(AiPrediction.horizon == horizon)
        if verdict:
            query = query.filter(AiPrediction.verdict == verdict)
        rows = query.order_by(AiPrediction.id.desc()).limit(min(limit, 500)).all()
        stats: dict[str, int] = {}
        total = 0
        for r in rows:
            stats[r.verdict] = stats.get(r.verdict, 0) + 1
            total += 1
        judged = stats.get("correct", 0) + stats.get("wrong", 0) + stats.get("partial", 0)
        return {
            "stats": {"total": total, "correct": stats.get("correct", 0),
                      "wrong": stats.get("wrong", 0), "partial": stats.get("partial", 0),
                      "pending": stats.get("pending", 0),
                      "accuracy": round(stats.get("correct", 0) / judged * 100, 1) if judged else None},
            "rows": [
                {"id": r.id, "report_date": r.report_date, "horizon": r.horizon,
                 "category": r.category, "symbol": r.symbol, "content": r.content,
                 "verdict": r.verdict, "actual": r.actual, "lesson": r.lesson,
                 "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S")}
                for r in rows
            ],
        }


@router.post("/ai/weekly/generate")
def generate_weekly():
    from app.llm.tasks.weekly_report import generate_weekly_report

    try:
        return asyncio.run(generate_weekly_report())
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"周报生成失败: {exc}") from exc


@router.get("/ai/weekly")
def weekly_list(limit: int = 20):
    """周报列表（最新在前）。"""
    with database.get_session() as session:
        rows = (
            session.query(AiReport)
            .filter(AiReport.report_type == "weekly")
            .order_by(AiReport.created_at.desc())
            .limit(min(limit, 50)).all()
        )
        return [
            {"id": r.id, "title": r.title, "content_md": r.content_md,
             "model": r.model, "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S")}
            for r in rows
        ]


@router.post("/ai/sentiment")
def sentiment(limit: int = 30):
    try:
        return asyncio.run(score_news(limit))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"情绪打分失败: {exc}") from exc


@router.get("/ai/news-sentiment")
def news_sentiment_list(limit: int = 100, date: str = ""):
    """新闻情绪列表：已打分新闻（默认最新交易日至今）+ 情绪分布统计。"""
    from app.data import calendar as calendar_module
    from app.data.models import Instrument, News

    with database.get_session() as session:
        if not date:
            date = calendar_module.latest_trade_date()
            # 最新交易日当天尚无已打分新闻（盘中/尚未抓取）→ 回退到最近有新闻的日期
            has = (
                session.query(News.id)
                .filter(News.sentiment_label.isnot(None), News.pub_time >= date)
                .first()
            )
            if has is None:
                latest = (
                    session.query(News.pub_time)
                    .filter(News.sentiment_label.isnot(None))
                    .order_by(News.pub_time.desc()).first()
                )
                if latest:
                    date = latest[0][:10]
        query = session.query(News).filter(
            News.sentiment_label.isnot(None), News.pub_time >= date)
        all_rows = query.all()
        rows = query.order_by(News.pub_time.desc()).limit(min(limit, 300)).all()
        symbols = {r.symbol for r in rows if r.symbol}
        names = {
            r.symbol: r.name
            for r in session.query(Instrument).filter(Instrument.symbol.in_(symbols)).all()
        }
        stats = {"利好": 0, "中性": 0, "利空": 0}
        for r in all_rows:
            if r.sentiment_label in stats:
                stats[r.sentiment_label] += 1
        return {
            "date": date,
            "stats": stats,
            "rows": [
                {"id": r.id, "symbol": r.symbol, "name": names.get(r.symbol, ""),
                 "title": r.title, "pub_time": r.pub_time,
                 "label": r.sentiment_label, "score": r.sentiment_score,
                 "reason": r.sentiment_reason, "source": r.source}
                for r in rows
            ],
        }


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []


@router.post("/ai/chat")
def chat(req: ChatRequest):
    """AI 小助手对话（自动注入系统实时数据快照）。"""
    from app.llm.tasks.assistant import chat_with_assistant

    if not req.message.strip():
        raise HTTPException(400, "消息不能为空")
    try:
        return asyncio.run(chat_with_assistant(req.message, req.history))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"AI 助手调用失败: {exc}") from exc


class AnalyzeRequest(BaseModel):
    symbol: str


@router.post("/ai/analyze")
def analyze(req: AnalyzeRequest):
    """个股综合解读（行情统计 + 新闻情绪 → LLM 报告）。"""
    from app.llm.tasks.symbol_analysis import analyze_symbol

    try:
        return asyncio.run(analyze_symbol(req.symbol))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"个股解读失败: {exc}") from exc


@router.post("/ai/news_forecast")
def news_forecast():
    """新闻热点明日行情预测（市场级新闻 + 本地数据 → LLM 预测，后台运行约 1 分钟）。"""
    from app.llm.tasks.news_forecast import generate_news_forecast
    from app.utils import tasks

    task_id = tasks.submit(lambda: asyncio.run(generate_news_forecast()))
    return {"task_id": task_id}


@router.post("/ai/multiagent")
def multiagent(req: AnalyzeRequest):
    """A股多智能体分析（4 分析师并行 → 经理汇总 → 交易员 → 风控官）。

    支持股票名称或 6 位代码（名称模糊匹配证券主表）。约 7 次 LLM 调用（1~2 分钟），
    后台运行，轮询 /api/system/tasks/{task_id} 取结果。
    """
    from app.api.paper import _resolve_symbol
    from app.llm.tasks.multiagent import analyze_multiagent
    from app.utils import tasks

    symbol = _resolve_symbol(req.symbol)  # 名称 → 代码
    task_id = tasks.submit(lambda: asyncio.run(analyze_multiagent(symbol)))
    return {"task_id": task_id}
