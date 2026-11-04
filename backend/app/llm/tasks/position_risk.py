"""持仓风险分析：持仓 + 行情 + 新闻 + 市场环境 + 账户集中度 → LLM 风险评级。

结果落库 position_risk（每交易日每持仓一条），前端持仓表展示徽标、点击看详情。
"""
from datetime import datetime

from app.config import get_settings
from app.data import calendar, database, loader
from app.data.models import PositionRisk
from app.llm.base import get_provider
from app.llm.tasks.risk_check import build_stock_stats, recent_news_summary
from app.utils.logger import get_logger

logger = get_logger("llm.position_risk")

RISK_SYSTEM = """你是持仓风险评估专家。给定某只股票的全方位信息，评估当前持有该股的风险，输出 JSON：
{"risk_level": "低"或"中"或"高", "score": 0~100的整数（越高越危险）,
"factors": ["风险因素", 最多4条，每条不超过40字，说明具体数据依据],
"suggestion": "操作建议（不超过80字，含具体止损位或仓位参考，基于输入数据计算）"}
评估维度：仓位集中度（占账户总资产比例）、个股技术面（涨跌幅/RSI/波动）、消息面、大盘环境。
铁律：只能引用输入数据中的数字；数据不足时说明缺口；必须给出明确可执行的建议。只输出 JSON。"""


def _position_brief(symbol: str) -> dict:
    """持仓 + 行情 + 新闻 + 市场环境 + 集中度（全部本地数据）。"""
    from app.paper import account as account_service

    summary = account_service.account_summary()
    pos = next((p for p in summary["positions"] if p["symbol"] == symbol), None)
    if pos is None:
        raise ValueError(f"账户未持有 {symbol}")
    stats = build_stock_stats(symbol)
    news = recent_news_summary(symbol, limit=5)
    try:
        from app.data.sentiment import latest_breadth

        breadth = latest_breadth()
        env = (f"广度({breadth[0]['date']}): 涨停 {breadth[0]['limit_up']} 家、跌停 "
               f"{breadth[0]['limit_down']} 家、上涨 {breadth[0]['advance']} 家、下跌 "
               f"{breadth[0]['decline']} 家" if breadth else "暂无广度数据")
    except Exception:  # noqa: BLE001
        env = "暂无市场情绪数据"
    concentration = pos["market_value"] / summary["total"] * 100 if summary["total"] else 0.0
    return {
        "position": pos, "stats": stats, "news": news, "env": env,
        "concentration": round(concentration, 1),
        "account_total": summary["total"],
    }


def _prompt(symbol: str, brief: dict) -> str:
    p = brief["position"]
    return (
        f"股票代码：{symbol}\n"
        f"持仓：{p['volume']} 股，成本 {p['avg_cost']:.2f}，现价 {p['price']:.2f}，"
        f"浮动盈亏 {p['pnl']:+.0f} 元（{p['pnl_pct'] * 100:+.1f}%）\n"
        f"仓位集中度：占总资产 {brief['concentration']}%（总资产 {brief['account_total']:,.0f} 元）\n"
        f"行情统计：{brief['stats']}\n"
        f"近期新闻情绪：\n{brief['news'] or '无'}\n"
        f"市场环境：{brief['env']}\n"
        f"请评估持仓风险。"
    )


async def analyze_position_risk(symbol: str, provider=None) -> dict:
    """分析单只持仓风险并落库（同日重复分析覆盖）。"""
    provider = provider or get_provider()
    brief = _position_brief(symbol)
    today = calendar.latest_trade_date()
    result = await provider.chat_json(
        [{"role": "system", "content": RISK_SYSTEM},
         {"role": "user", "content": _prompt(symbol, brief)}],
        temperature=0.3, schema_hint="position_risk",
    )
    level = str(result.get("risk_level", "中"))
    if level not in ("低", "中", "高"):
        level = "中"
    try:
        score = float(result.get("score", 50))
        score = max(0.0, min(100.0, score))
    except (TypeError, ValueError):
        score = 50.0
    factors = "\n".join(str(f)[:60] for f in result.get("factors", [])[:4])
    suggestion = str(result.get("suggestion", ""))[:120]
    with database.get_session() as session:
        row = (
            session.query(PositionRisk)
            .filter(PositionRisk.symbol == symbol, PositionRisk.date == today)
            .first()
        )
        if row is None:
            row = PositionRisk(symbol=symbol, date=today)
            session.add(row)
        row.risk_level = level
        row.score = score
        row.factors = factors
        row.suggestion = suggestion
        row.model = provider.name
        session.commit()
        session.refresh(row)
        out = {"id": row.id, "symbol": symbol, "date": today, "risk_level": level,
               "score": score, "factors": factors.splitlines(), "suggestion": suggestion,
               "model": provider.name}
    logger.info("持仓风险分析完成: %s %s 分%s", symbol, score, level)
    return out


def load_position_risks(date: str = "") -> list[dict]:
    """当日（默认最新交易日）全部持仓风险记录。"""
    date = date or calendar.latest_trade_date()
    with database.get_session() as session:
        rows = session.query(PositionRisk).filter(PositionRisk.date == date).all()
        return [
            {"id": r.id, "symbol": r.symbol, "date": r.date, "risk_level": r.risk_level,
             "score": r.score, "factors": r.factors.splitlines() if r.factors else [],
             "suggestion": r.suggestion, "model": r.model,
             "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S")}
            for r in rows
        ]
