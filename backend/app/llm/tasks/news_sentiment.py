"""新闻情绪批量打分：只处理未打分的新闻，批量合并调用省 token，结果回写 news 表。"""
import asyncio

from app.data import database
from app.data.models import News
from app.llm.base import get_provider
from app.llm.prompts import SENTIMENT_SYSTEM, sentiment_user_prompt
from app.utils.logger import get_logger

logger = get_logger("llm.news_sentiment")

BATCH_SIZE = 10  # 每批最多 10 条新闻


async def score_news(limit: int = 30, provider=None) -> dict:
    """对未打分新闻批量打情绪分。返回统计。"""
    provider = provider or get_provider()
    with database.get_session() as session:
        rows = (
            session.query(News)
            .filter(News.sentiment_score.is_(None))
            .order_by(News.pub_time.desc())
            .limit(limit).all()
        )
        items = [{"id": r.id, "title": r.title, "content": r.content} for r in rows]
    if not items:
        return {"scored": 0, "batches": 0}

    scored = 0
    for batch_start in range(0, len(items), BATCH_SIZE):
        batch = items[batch_start: batch_start + BATCH_SIZE]
        result = await provider.chat_json(
            [
                {"role": "system", "content": SENTIMENT_SYSTEM},
                {"role": "user", "content": sentiment_user_prompt(batch)},
            ],
            temperature=0.1,
            schema_hint="sentiment",
        )
        updates = {}
        for entry in result.get("results", []):
            try:
                idx = int(entry["index"])
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= idx < len(batch):
                score = float(entry.get("score", 0))
                label = str(entry.get("label", "中性"))[:10]
                updates[batch[idx]["id"]] = (max(-1.0, min(1.0, score)), label,
                                            str(entry.get("reason", ""))[:200])
        if updates:
            with database.get_session() as session:
                for news_id, (score, label, reason) in updates.items():
                    row = session.get(News, news_id)
                    if row is not None:
                        row.sentiment_score = score
                        row.sentiment_label = label
                        row.sentiment_reason = reason
                session.commit()
            scored += len(updates)
        await asyncio.sleep(0)  # 批次间让出事件循环

    logger.info("新闻情绪打分完成: %d/%d 条", scored, len(items))
    return {"scored": scored, "batches": (len(items) + BATCH_SIZE - 1) // BATCH_SIZE,
            "total": len(items)}
