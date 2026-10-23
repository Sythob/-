"""A股多智能体分析（移植 TradingAgents 的多智能体分工设计）。

流程：4 位分析师并行（技术面/消息面/资金面/市场环境，全部基于本地数据）
→ 研究经理汇总（观点+分歧点）→ 交易员决策（多/空/观望+仓位建议）
→ 风控官审查（放行+风险等级+警告）→ 生成 Markdown 报告并入库（ai_report, type=multiagent）。

全部数据来自本地库与已缓存行情，无新增外部依赖；LLM 可插拔（DeepSeek/Claude/通义/Mock）。
"""
import asyncio
import json
from datetime import datetime

from app.data import database
from app.data.models import AiReport
from app.llm.base import get_provider
from app.llm.tasks.risk_check import build_stock_stats, recent_news_summary
from app.utils.logger import get_logger

logger = get_logger("llm.multiagent")

# ---------------------------------------------------------------- 分析师 Prompt

ANALYST_SYSTEMS = {
    "technical": (
        "你是技术面分析师。基于给定的行情统计（涨跌幅/波动/RSI 等）输出 JSON："
        '{"view": "bullish"或"bearish"或"neutral", "confidence": 0~1, '
        '"key_points": ["不超过40字的技术要点", 最多3条], "risks": ["技术风险", 最多2条]}'
    ),
    "news": (
        "你是消息面分析师。基于给定的近期新闻情绪归纳输出 JSON（格式同技术面）："
        "归纳利好/利空/中性分布，注意消息的时效与重要性。"
    ),
    "fund_flow": (
        "你是资金面分析师。基于给定的量价与两融数据输出 JSON（格式同技术面）："
        "关注量能变化、换手率、两融余额趋势对个股的含义。"
    ),
    "market_env": (
        "你是市场环境分析师。基于给定的市场广度（涨跌停/涨跌家数）与热门板块输出 JSON（格式同技术面）："
        "判断当前大盘环境对该股操作的影响（顺风/逆风）。"
    ),
}

ANALYST_NAMES = {
    "technical": "技术面分析师",
    "news": "消息面分析师",
    "fund_flow": "资金面分析师",
    "market_env": "市场环境分析师",
}

MANAGER_SYSTEM = (
    "你是研究经理。汇总四位分析师的结论，输出 JSON："
    '{"summary": "不超过80字的综合观点", "agreements": ["共识点", 最多2条], '
    '"disagreements": ["分歧点", 最多2条]}'
)

TRADER_SYSTEM = (
    "你是交易员。根据研究经理的汇总输出可执行决策 JSON："
    '{"action": "buy"或"hold"或"sell"或"avoid", "position": 0~100 建议仓位百分比, '
    '"entry": "参考买入区间", "exit": "参考卖出/止损位置", '
    '"reasoning": "不超过80字的决策理由"}'
)

RISK_SYSTEM = (
    "你是风控官。审查交易决策的合理性，输出 JSON："
    '{"approved": true或false, "risk_level": "low"或"medium"或"high", '
    '"warnings": ["风险提示", 最多3条]}'
)


# ---------------------------------------------------------------- 数据收集

def _collect_data(symbol: str) -> dict:
    """收集各分析师输入（全部本地/缓存数据）。"""
    stats = build_stock_stats(symbol)
    news = recent_news_summary(symbol, limit=8)
    try:
        from app.data.sentiment import get_sentiment_overview

        sentiment = get_sentiment_overview()
        breadth = sentiment["breadth"][0] if sentiment["breadth"] else {}
        margin = sentiment.get("margin", {})
        env = f"广度({breadth.get('date', '')}): 涨停 {breadth.get('limit_up', '?')} 家, " \
              f"跌停 {breadth.get('limit_down', '?')} 家, 上涨 {breadth.get('advance', '?')} 家, " \
              f"下跌 {breadth.get('decline', '?')} 家; " \
              f"两融余额 {margin.get('margin_balance', 0) / 1e8:.0f} 亿元" if margin.get("margin_balance") else "暂无情绪数据"
    except Exception:  # noqa: BLE001
        env = "暂无市场情绪数据"
    try:
        from app.data.sectors import get_sectors

        top_sectors = "、".join(f"{s['name']}({s['pct_change']:+.2f}%)" for s in get_sectors()[:3])
    except Exception:  # noqa: BLE001
        top_sectors = "暂无板块数据"
    try:
        from app.paper import account as account_service

        summary = account_service.account_summary()
        holding = next((p for p in summary["positions"] if p["symbol"] == symbol), None)
        portfolio = (
            f"当前持仓该股 {holding['volume']} 股（成本 {holding['avg_cost']:.2f}）" if holding
            else f"未持有该股；账户总资产 {summary['total']:,.0f} 元"
        )
    except Exception:  # noqa: BLE001
        portfolio = "无模拟持仓信息"
    return {"stats": stats, "news": news, "env": env, "sectors": top_sectors,
            "portfolio": portfolio}


# ---------------------------------------------------------------- 主流程

async def _call_analyst(provider, role: str, symbol: str, data: dict) -> dict:
    prompts = {
        "technical": f"股票 {symbol} 行情统计：{data['stats']}",
        "news": f"股票 {symbol} 近期新闻情绪：\n{data['news'] or '无'}",
        "fund_flow": f"股票 {symbol} 量价与两融：{data['stats']}；{data['env']}",
        "market_env": f"市场环境：{data['env']}；当前热门板块：{data['sectors']}",
    }
    try:
        result = await asyncio.wait_for(
            provider.chat_json(
                [{"role": "system", "content": ANALYST_SYSTEMS[role]},
                 {"role": "user", "content": prompts[role]}],
                temperature=0.3, schema_hint="multiagent",
            ),
            timeout=60,
        )
        return {"role": role, "name": ANALYST_NAMES[role], "view": result.get("view", "neutral"),
                "confidence": float(result.get("confidence", 0.5)),
                "key_points": list(result.get("key_points", []))[:3],
                "risks": list(result.get("risks", []))[:2]}
    except Exception as exc:  # noqa: BLE001 单个分析师失败不影响整体
        logger.warning("分析师 %s 失败: %s", role, exc)
        return {"role": role, "name": ANALYST_NAMES[role], "view": "neutral", "confidence": 0.0,
                "key_points": [f"分析失败：{type(exc).__name__}"], "risks": []}


async def analyze_multiagent(symbol: str, provider=None) -> dict:
    """多智能体分析主流程。返回报告记录 dict。"""
    provider = provider or get_provider()

    # 数据保障：本地历史不足时先补同步该股日线（约 10~30 秒），避免"无足够行情数据"
    from app.llm.tasks.risk_check import build_stock_stats

    if "无足够" in str(build_stock_stats(symbol).get("数据", "")):
        from app.data.sync import sync_watchlist_bars

        logger.info("多智能体分析：%s 本地行情不足，先补同步日线", symbol)
        sync_watchlist_bars([symbol])

    data = _collect_data(symbol)
    today = datetime.now().strftime("%Y-%m-%d")

    # 1. 四位分析师并行；网络抖动失败的分析师自动补重试两轮（每轮间隔 15 秒）
    analysts = await asyncio.gather(
        *(_call_analyst(provider, role, symbol, data) for role in ANALYST_SYSTEMS)
    )
    for _round in range(2):
        failed_roles = [a["role"] for a in analysts if a["confidence"] <= 0.0]
        if not failed_roles:
            break
        logger.info("分析师失败 %s，15 秒后补重试（第 %d 轮）", failed_roles, _round + 1)
        await asyncio.sleep(15)
        retried = await asyncio.gather(
            *(_call_analyst(provider, role, symbol, data) for role in failed_roles)
        )
        for a in retried:
            for i, old in enumerate(analysts):
                if old["role"] == a["role"]:
                    analysts[i] = a

    # 2. 研究经理汇总
    analyst_brief = "\n".join(
        f"- {a['name']}: {a['view']}（信心 {a['confidence']:.2f}）要点：{'；'.join(a['key_points'])}"
        for a in analysts
    )
    manager = await provider.chat_json(
        [{"role": "system", "content": MANAGER_SYSTEM},
         {"role": "user", "content": f"股票 {symbol} 分析师结论：\n{analyst_brief}"}],
        temperature=0.3, schema_hint="multiagent",
    )

    # 3. 交易员决策
    trader = await provider.chat_json(
        [{"role": "system", "content": TRADER_SYSTEM},
         {"role": "user", "content": f"研究经理汇总：{manager.get('summary', '')}\n"
                                     f"共识：{manager.get('agreements', [])}\n"
                                     f"分歧：{manager.get('disagreements', [])}\n"
                                     f"账户情况：{data['portfolio']}"}],
        temperature=0.2, schema_hint="multiagent",
    )

    # 4. 风控官审查
    risk = await provider.chat_json(
        [{"role": "system", "content": RISK_SYSTEM},
         {"role": "user", "content": f"交易决策：{json.dumps(trader, ensure_ascii=False)}\n"
                                     f"标的近况：{data['stats']}"}],
        temperature=0.1, schema_hint="multiagent",
    )

    decision = {
        "symbol": symbol, "date": today, "analysts": analysts,
        "manager": manager, "trader": trader, "risk": risk,
    }
    report_md = _build_report(symbol, today, decision)

    with database.get_session() as session:
        row = AiReport(
            report_type="multiagent",
            title=f"{symbol} 多智能体分析 {today}",
            content_md=report_md,
            meta_json=json.dumps(decision, ensure_ascii=False),
            model=provider.name,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        logger.info("多智能体分析已生成: %s id=%s model=%s", symbol, row.id, provider.name)
        return {"id": row.id, "title": row.title, "content_md": report_md,
                "decision": decision, "model": provider.name}


def _build_report(symbol: str, today: str, decision: dict) -> str:
    view_cn = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}
    action_cn = {"buy": "买入", "hold": "持有/观望", "sell": "卖出", "avoid": "回避"}
    trader = decision["trader"]
    risk = decision["risk"]
    lines = [
        f"# {symbol} 多智能体分析（{today}）",
        "",
        "## 一、分析师团队观点",
        "| 角色 | 观点 | 信心 | 关键要点 |",
        "|---|---|---|---|",
    ]
    for a in decision["analysts"]:
        lines.append(
            f"| {a['name']} | {view_cn.get(a['view'], a['view'])} | {a['confidence']:.0%} | "
            f"{'；'.join(a['key_points'])[:100]} |"
        )
    lines += [
        "",
        f"**研究经理汇总**：{decision['manager'].get('summary', '')}",
    ]
    if decision["manager"].get("agreements"):
        lines.append(f"- 共识：{'；'.join(decision['manager']['agreements'])}")
    if decision["manager"].get("disagreements"):
        lines.append(f"- 分歧：{'；'.join(decision['manager']['disagreements'])}")
    lines += [
        "",
        "## 二、交易建议",
        f"- **动作**：{action_cn.get(trader.get('action'), trader.get('action'))}",
        f"- **建议仓位**：{trader.get('position', 0)}%",
        f"- **参考买入区间**：{trader.get('entry', '—')}",
        f"- **参考卖出/止损**：{trader.get('exit', '—')}",
        f"- **理由**：{trader.get('reasoning', '')}",
        "",
        "## 三、风控审查",
        f"- **结论**：{'通过' if risk.get('approved', True) else '否决'}（风险等级 {risk.get('risk_level', 'unknown')}）",
    ]
    for w in risk.get("warnings", []):
        lines.append(f"- ⚠️ {w}")
    lines += [
        "",
        "> 本报告由 AI 多智能体生成，仅供学习研究，不构成投资建议。",
    ]
    return "\n".join(lines)
