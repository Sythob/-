"""新闻热点驱动的明日行情预测：市场级新闻 → LLM 分析 → 可验证的明日预测报告。

数据：新浪全球财经快讯（市场级新闻，本机可用）+ 本地市场数据（指数/广度/自选情绪）。
输出格式与复盘「明日关注」一致：判断（方向+点位）+ 依据，可被次日行情验证。
"""
from datetime import datetime

from app.data import database
from app.data.models import AiReport
from app.llm.base import get_provider
from app.llm.tasks.assistant import assistant_context
from app.utils.logger import get_logger

logger = get_logger("llm.news_forecast")

FORECAST_SYSTEM = """你是 A股短线策略分析师。根据今日热点新闻与市场数据，预测明日行情。
输出 Markdown：
# 新闻热点明日行情预测（YYYY-MM-DD）
## 一、今日热点新闻摘要
（3-5 条对市场影响最大的新闻，每条一句话）
## 二、明日大盘预测
（明确方向+点位，如"判断：上证指数 明日收涨，收盘价高于 X 点"；覆盖 1-2 个主要指数）
## 三、热点板块预测
（1-2 个受益板块，说明逻辑）
## 四、龙头候选
（1-2 只龙头，格式：代码（名称）+ 推荐逻辑 + 参考买入区间）
## 五、风险提示
（1-2 条）

准确性铁律：
1. 只能引用输入新闻与数据中的信息，严禁编造新闻或数字
2. 所有预测必须方向明确（涨/跌+点位），禁止"关注""或将""可能"等模糊表述
3. 只推荐输入数据中存在的股票
"""


def fetch_market_news(limit: int = 30) -> list[str]:
    """市场级热点新闻（新浪财经快讯，返回文本列表）。"""
    import akshare as ak

    df = ak.stock_info_global_sina()
    if df is None or df.empty:
        return []
    news = []
    for _, r in df.head(limit).iterrows():
        content = str(r.get("内容", "")).strip()
        if content:
            news.append(content[:150])
    return news


def forecast_exists_for_date(date: str) -> bool:
    with database.get_session() as session:
        row = (
            session.query(AiReport)
            .filter(AiReport.report_type == "news_forecast", AiReport.title.like(f"%{date}%"))
            .first()
        )
        return row is not None


async def generate_news_forecast(provider=None, news: list[str] | None = None) -> dict:
    """生成热点新闻明日预测报告（幂等：同日已生成则跳过）。"""
    provider = provider or get_provider()
    today = datetime.now().strftime("%Y-%m-%d")
    if forecast_exists_for_date(today):
        with database.get_session() as session:
            row = (
                session.query(AiReport)
                .filter(AiReport.report_type == "news_forecast", AiReport.title.like(f"%{today}%"))
                .first()
            )
            return {"id": row.id, "title": row.title, "content_md": row.content_md,
                    "model": row.model, "skipped": True}

    news_list = news if news is not None else fetch_market_news()
    market = assistant_context()
    news_text = "\n".join(f"- {n}" for n in news_list) if news_list else "今日无市场新闻（数据源暂不可用）"
    content = await provider.chat([
        {"role": "system", "content": FORECAST_SYSTEM},
        {"role": "user", "content": (
            f"今日热点新闻：\n{news_text}\n\n市场数据快照：\n{market}\n\n请输出明日行情预测。"
        )},
    ], temperature=0.4)

    with database.get_session() as session:
        row = AiReport(
            report_type="news_forecast",
            title=f"新闻热点明日行情预测 {today}",
            content_md=content,
            meta_json=f'{{"date": "{today}", "news_count": {len(news_list)}}}',
            model=provider.name,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        logger.info("热点预测已生成: id=%s news=%d 条 model=%s", row.id, len(news_list), provider.name)
        return {"id": row.id, "title": row.title, "content_md": content, "model": provider.name}
