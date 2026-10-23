"""AI 小助手：对话式问答，自动注入系统实时数据快照。

每次对话前收集：市场状态（广度/涨跌停）、主要指数、模拟持仓与账户——
让助手能回答「今天市场怎么样」「我的持仓风险大吗」这类问题。
所有数据来自本地库与缓存，缺失时如实说明，严禁编造。
"""
from app.config import get_settings
from app.data import loader
from app.data.sync import INDEX_HISTORY_WHITELIST
from app.llm.base import get_provider
from app.utils.logger import get_logger

logger = get_logger("llm.assistant")

ASSISTANT_SYSTEM = """你是「唯我量化」量化交易系统的 AI 小助手，帮助用户使用系统并解答市场与量化问题。

以下是系统实时数据快照（随对话更新）：
{context}

回答规则：
1. 中文回答，简洁实用（一般不超过 300 字）
2. 涉及行情/持仓时只能引用快照中的数字，禁止编造；快照没有的数据如实说"系统暂无此数据"
3. 系统操作问题给出具体步骤（页面名称 + 按钮位置）
4. 可以解释技术指标、交易策略、量化概念、系统功能
5. 涉及投资建议时保持中立，提示仅供参考、不构成投资建议
"""


def assistant_context() -> str:
    """系统数据快照（本地库/缓存，快速离线）。"""
    lines = []
    try:
        from app.data.sentiment import market_regime

        state = market_regime()
        if state.get("regime") != "unknown":
            lines.append(
                f"市场状态: {state['regime']}（{state['date']}，上涨 {state['advance']} / "
                f"下跌 {state['decline']}，涨停 {state['limit_up']} / 跌停 {state['limit_down']}）"
            )
    except Exception:  # noqa: BLE001
        pass
    try:
        for sym in sorted(INDEX_HISTORY_WHITELIST):
            bar = loader.get_latest_bar(sym)
            if bar:
                lines.append(
                    f"指数 {sym}: 收盘 {bar['close']:.2f}（{bar['trade_date']}，"
                    f"当日 {bar['pct_change']:+.2f}%）"
                )
    except Exception:  # noqa: BLE001
        pass
    try:
        watchlist = get_settings().watchlist_symbols
        brief = []
        for sym in watchlist[:8]:
            bar = loader.get_latest_bar(sym)
            if bar:
                brief.append(f"{sym} {bar['close']:.2f}({bar['pct_change']:+.1f}%)")
        lines.append(f"自选池（{len(watchlist)} 只，前 8 只）: " + "；".join(brief))
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.paper import account as account_service

        summary = account_service.account_summary()
        if summary["positions"]:
            for p in summary["positions"][:5]:
                lines.append(
                    f"持仓 {p['symbol']}: {p['volume']} 股 成本 {p['avg_cost']:.2f} "
                    f"现价 {p['price']:.2f} 盈亏 {p['pnl']:+.0f} 元"
                )
        else:
            lines.append("模拟持仓: 无")
        lines.append(f"账户: 现金 {summary['cash']:,.0f} 元，总资产 {summary['total']:,.0f} 元")
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(lines) or "系统数据暂不可用（请先在系统页同步数据）"


async def chat_with_assistant(message: str, history: list[dict] | None = None,
                              provider=None) -> dict:
    """对话接口：注入数据快照 + 最近 10 条历史 → LLM 回复。"""
    provider = provider or get_provider()
    context = assistant_context()
    messages = [{"role": "system", "content": ASSISTANT_SYSTEM.format(context=context)}]
    for h in (history or [])[-10:]:
        role = h.get("role", "user")
        content = str(h.get("content", ""))
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})
    reply = await provider.chat(messages, temperature=0.5)
    logger.info("AI 助手对话: model=%s len=%d", provider.name, len(reply))
    return {"reply": reply, "model": provider.name}
