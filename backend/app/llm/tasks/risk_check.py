"""下单前 AI 风险检查：10 秒超时 + 失败兜底（默认放行 + 警告），AI 故障不阻塞模拟盘。"""
import asyncio

from app.data import loader
from app.data.models import News
from app.data import database
from app.llm.base import get_provider
from app.llm.prompts import RISK_SYSTEM, risk_user_prompt
from app.ml.indicators import rsi
from app.utils.logger import get_logger

logger = get_logger("llm.risk_check")

TIMEOUT_SECONDS = 10.0


def build_stock_stats(symbol: str) -> dict:
    """标的近 20 日统计（本地库计算，离线可用）。"""
    df = loader.load_daily(symbol, adjust="qfq")
    if df.empty or len(df) < 5:
        return {"数据": "本地无足够行情"}
    tail = df.tail(21)
    stats = {
        "数据日期": str(tail["trade_date"].iloc[-1]),
        "最新收盘": round(float(tail["close"].iloc[-1]), 2),
        "近20日涨跌幅": round(float(tail["close"].iloc[-1] / tail["close"].iloc[0] - 1), 4),
        "近20日波动率": round(float(tail["pct_change"].tail(20).std()), 2),
        "RSI(14)": round(float(rsi(df["close"], 14).iloc[-1]), 1),
    }
    if len(tail) >= 2:
        stats["当日涨跌幅"] = round(float(tail["pct_change"].iloc[-1]), 2)
    return stats


def recent_news_summary(symbol: str, limit: int = 5) -> str:
    with database.get_session() as session:
        rows = (
            session.query(News)
            .filter(News.symbol == symbol, News.sentiment_label.isnot(None))
            .order_by(News.pub_time.desc())
            .limit(limit).all()
        )
        return "\n".join(f"- [{r.sentiment_label}] {r.title}" for r in rows)


async def check_order_risk(order: dict, stats: dict, news_summary: str, account: dict,
                           provider=None) -> dict:
    """调用 LLM 判断风险；超时/异常时兜底放行。order 含 symbol/direction/volume/price/order_type。"""
    provider = provider or get_provider()
    messages = [
        {"role": "system", "content": RISK_SYSTEM},
        {"role": "user", "content": risk_user_prompt(order, stats, news_summary, account)},
    ]
    try:
        result = await asyncio.wait_for(
            provider.chat_json(messages, temperature=0.1, schema_hint="risk"),
            timeout=TIMEOUT_SECONDS,
        )
        return {
            "approved": bool(result.get("approved", True)),
            "risk_level": str(result.get("risk_level", "medium")),
            "reasons": list(result.get("reasons", []))[:5],
            "checked": True,
        }
    except Exception as exc:  # noqa: BLE001 兜底：AI 故障不得阻塞模拟盘
        logger.warning("AI 风控检查失败，按配置兜底放行: %s", exc)
        return {
            "approved": True,
            "risk_level": "unknown",
            "reasons": [f"AI 检查不可用，已放行（{type(exc).__name__}）"],
            "checked": False,
        }
