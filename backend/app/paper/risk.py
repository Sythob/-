"""模拟盘 AI 风控钩子：下单前检查，模式 block/warn/off（账户级优先，回退 .env 配置）。

AI 失败时兜底放行（10s 超时），保证模拟盘不被 AI 故障卡死。
"""
import asyncio

from app.llm.tasks.risk_check import build_stock_stats, check_order_risk, recent_news_summary
from app.paper import account as account_service
from app.utils.logger import get_logger

logger = get_logger("paper.risk")


class RiskBlocked(Exception):
    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("；".join(reasons))


def run_risk_check(symbol: str, direction: str, price: float, volume: int,
                   order_type: str) -> dict:
    """下单前 AI 风险检查。block 模式且被拒时抛 RiskBlocked。"""
    account = account_service.ensure_account()
    mode = account.risk_mode
    if mode == "off":
        return {"checked": False, "approved": True, "risk_level": "skip",
                "reasons": ["AI 风控已关闭"]}

    stats = build_stock_stats(symbol)
    news = recent_news_summary(symbol)
    summary = account_service.account_summary()
    account_info = {"cash": summary["cash"], "total": summary["total"]}

    result = asyncio.run(check_order_risk(
        {"symbol": symbol, "direction": direction, "volume": volume,
         "price": price, "order_type": order_type},
        stats, news, account_info,
    ))

    if mode == "block" and not result["approved"]:
        raise RiskBlocked(result["reasons"])
    if mode == "warn" and result["risk_level"] in ("medium", "high"):
        logger.warning("AI 风控警告 %s %s: %s", symbol, direction, result["reasons"])
    return result
