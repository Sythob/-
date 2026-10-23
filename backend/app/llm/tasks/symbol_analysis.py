"""个股综合解读：行情统计 + 新闻情绪 + 公告 → LLM 生成解读报告（存 ai_report，type=stock）。"""
from app.data import database
from app.data.models import AiReport
from app.llm.base import get_provider
from app.llm.tasks.risk_check import build_stock_stats, recent_news_summary
from app.utils.logger import get_logger

logger = get_logger("llm.symbol_analysis")

SYSTEM_PROMPT = """你是 A股个股分析师。基于给定的行情统计与近期新闻，输出中文 Markdown 解读报告：
# {symbol} 个股解读（YYYY-MM-DD）
## 一、近期走势
（涨跌、波动、RSI 位置，2-3 句）
## 二、消息面
（新闻情绪归纳，利好/利空/中性各列出要点）
## 三、技术面提示
（支撑/压力参考、风险点，2-3 条）
## 四、结论
（一句话倾向：偏多/偏空/观望 + 理由）
要求：客观中立、300 字左右、直接输出 Markdown。
"""


async def analyze_symbol(symbol: str, provider=None) -> dict:
    provider = provider or get_provider()
    stats = build_stock_stats(symbol)
    news = recent_news_summary(symbol, limit=10)
    from datetime import datetime

    today = datetime.now().strftime("%Y-%m-%d")
    user_prompt = (
        f"股票代码：{symbol}\n行情统计：{stats}\n近期新闻情绪：\n{news or '无'}\n"
        f"请输出解读报告。"
    )
    content = await provider.chat([
        {"role": "system", "content": SYSTEM_PROMPT.replace("{symbol}", symbol)
         .replace("{today}", today)},
        {"role": "user", "content": user_prompt},
    ], temperature=0.4)

    with database.get_session() as session:
        row = AiReport(
            report_type="stock", title=f"{symbol} 个股解读 {today}",
            content_md=content, meta_json=f'{{"symbol": "{symbol}"}}',
            model=provider.name,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        logger.info("个股解读已生成: %s id=%s model=%s", symbol, row.id, provider.name)
        return {"id": row.id, "title": row.title, "content_md": content, "model": provider.name}
