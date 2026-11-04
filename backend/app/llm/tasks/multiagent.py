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
        "铁律：key_points 禁止为空，至少 1 条；必须给出明确方向（bullish/bearish/neutral 三选一）。"
    ),
    "news": (
        "你是消息面分析师。基于给定的近期新闻情绪归纳输出 JSON（格式同技术面）："
        "归纳利好/利空/中性分布，注意消息的时效与重要性。"
        "铁律：key_points 禁止为空——即使新闻很少或无新闻，也要基于可得信息给出至少 1 条判断"
        "（如'近期无重大消息，情绪面中性，需关注后续公告'），并明确说明数据缺口。"
    ),
    "fund_flow": (
        "你是资金面分析师。基于给定的量价与两融数据输出 JSON（格式同技术面）："
        "关注量能变化、换手率、两融余额趋势对个股的含义。"
        "铁律：key_points 禁止为空——数据不足时用可得数据推断（如两融整体趋势对个股资金环境的影响），"
        "至少 1 条并明确说明数据缺口。"
    ),
    "market_env": (
        "你是市场环境分析师。基于给定的市场广度（涨跌停/涨跌家数）与热门板块输出 JSON（格式同技术面）："
        "判断当前大盘环境对该股操作的影响（顺风/逆风）。"
        "铁律：key_points 禁止为空，至少 1 条——广度与板块数据足够给出该股所处环境的判断。"
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
    # 已打分新闻为空时回退到原始新闻标题（保证消息面分析师有输入）
    if not news:
        from app.data.models import News

        with database.get_session() as session:
            rows = (
                session.query(News)
                .filter(News.symbol == symbol)
                .order_by(News.pub_time.desc()).limit(8).all()
            )
        news = "\n".join(f"- [未打分] {r.title}" for r in rows) or "近期无该股新闻"
    try:
        import concurrent.futures

        from app.data.sentiment import get_sentiment_overview

        # 两融接口访问上交所官网可能很慢：线程执行 + 15 秒超时。
        # 注意不能用 with（__exit__ 会等待挂起线程），shutdown(wait=False) 丢弃慢线程。
        pool = concurrent.futures.ThreadPoolExecutor(1)
        try:
            sentiment = pool.submit(get_sentiment_overview).result(timeout=15)
        finally:
            pool.shutdown(wait=False)
        breadth = sentiment["breadth"][0] if sentiment["breadth"] else {}
        margin = sentiment.get("margin", {})
        env = f"广度({breadth.get('date', '')}): 涨停 {breadth.get('limit_up', '?')} 家, " \
              f"跌停 {breadth.get('limit_down', '?')} 家, 上涨 {breadth.get('advance', '?')} 家, " \
              f"下跌 {breadth.get('decline', '?')} 家; " \
              f"两融余额 {margin.get('margin_balance', 0) / 1e8:.0f} 亿元" if margin.get("margin_balance") else "暂无情绪数据"
    except Exception:  # noqa: BLE001 情绪数据缺失/超时不影响分析
        env = "暂无市场情绪数据"
    # 该股近 5 日量能（资金面分析师输入）
    try:
        from app.data.models import DailyBar

        with database.get_session() as session:
            bars = (
                session.query(DailyBar)
                .filter(DailyBar.symbol == symbol)
                .order_by(DailyBar.trade_date.desc()).limit(5).all()
            )
        volume = "、".join(
            f"{b.trade_date[5:]}(量{b.volume / 1e6:.1f}万手 额{b.amount / 1e8:.1f}亿 换手{b.turnover_rate:.1f}%)"
            for b in reversed(bars)
        ) or "无量能数据"
    except Exception:  # noqa: BLE001
        volume = "无量能数据"
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
            "portfolio": portfolio, "volume": volume}


def _extract_key_points(result: dict) -> list[str]:
    """兼容模型多种返回结构提取要点（标准 key_points / data_summary / signal 兜底）。"""
    points = result.get("key_points") or []
    if isinstance(points, str):
        points = [points]
    if not points:
        ds = result.get("data_summary", {})
        if isinstance(ds, dict):
            points = [str(v) for v in ds.values() if str(v).strip()]
        elif isinstance(ds, str) and ds.strip():
            points = [ds]
    if not points and result.get("signal"):
        points = [f"信号：{result['signal']}"]
    return [str(p)[:60] for p in points if str(p).strip()][:3]


def _extract_view(result: dict) -> str:
    """兼容标准 view 字段与 signal 字段推导观点。"""
    view = str(result.get("view", "")).lower()
    if view in ("bullish", "bearish", "neutral"):
        return view
    signal = str(result.get("signal", ""))
    if "看多" in signal or "买入" in signal or "bull" in signal.lower():
        return "bullish"
    if "看空" in signal or "卖出" in signal or "bear" in signal.lower():
        return "bearish"
    return "neutral"


def _data_snippet(role: str, data: dict) -> str:
    """分析师兜底要点：如实列出其收到的数据摘要（模型两次返回空时使用，不编造）。"""
    if role == "news":
        return f"近期新闻：{str(data.get('news', ''))[:60] or '无'}"
    if role == "fund_flow":
        return f"近5日量能：{str(data.get('volume', ''))[:60] or '无'}"
    if role == "market_env":
        return f"市场环境：{str(data.get('env', ''))[:60] or '无'}"
    return f"行情统计：{str(data.get('stats', ''))[:60]}"


# ---------------------------------------------------------------- 主流程

async def _call_analyst(provider, role: str, symbol: str, data: dict) -> dict:
    prompts = {
        "technical": f"股票 {symbol} 行情统计：{data['stats']}",
        "news": f"股票 {symbol} 近期新闻情绪：\n{data['news'] or '无'}",
        "fund_flow": (
            f"请基于以下数据回答问题并输出 JSON：\n"
            f"1) 股票 {symbol} 近5日量能与换手变化如何解读？\n"
            f"2) 当前市场资金环境（广度/两融）对该股是顺风还是逆风？\n"
            f"3) 综合以上，你的判断是 bullish/bearish/neutral？\n\n"
            f"数据：近5日量能：{data['volume']}\n市场资金环境：{data['env']}"
        ),
        "market_env": f"市场环境：{data['env']}；当前热门板块：{data['sectors']}",
    }
    try:
        messages = [{"role": "system", "content": ANALYST_SYSTEMS[role]},
                    {"role": "user", "content": prompts[role]}]
        result = await asyncio.wait_for(
            provider.chat_json(messages, temperature=0.3, schema_hint="multiagent"),
            timeout=60,
        )
        key_points = _extract_key_points(result)
        # 空要点补救：重试一次并强调必须输出要点（防止"只有一个分析师在分析"）
        if not key_points:
            logger.info("分析师 %s 要点为空，强化指令重试", role)
            messages.append({"role": "user",
                             "content": "你上轮 key_points 为空，这是不合格的。请立即基于上述数据"
                                        "重新输出 JSON，key_points 至少 1 条。"})
            result = await asyncio.wait_for(
                provider.chat_json(messages, temperature=0.3, schema_hint="multiagent"),
                timeout=60,
            )
            key_points = _extract_key_points(result)
        # 两次仍空：确定性兜底（列出该分析师收到的真实数据要点，不编造）
        if not key_points:
            key_points = [f"{ANALYST_NAMES[role]}数据不足：{_data_snippet(role, data)}"]
        return {"role": role, "name": ANALYST_NAMES[role], "view": _extract_view(result),
                "confidence": float(result.get("confidence", 0.5)),
                "key_points": key_points,
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
