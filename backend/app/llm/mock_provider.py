"""离线 Mock Provider：LLM_MOCK=1 或未配置 Key 时启用，全流程离线可跑。

按 schema_hint 返回各场景的正确 JSON 形状；chat 返回固定 Markdown。
"""
from datetime import datetime

from app.llm.base import LLMProvider


class MockProvider(LLMProvider):
    name = "mock"

    async def chat(self, messages: list[dict], temperature: float = 0.3) -> str:
        today = datetime.now().strftime("%Y-%m-%d")
        return (
            f"# 今日复盘（{today}）\n\n"
            "> 本报告由【模拟模型】生成。在 backend/.env 配置 DEEPSEEK_API_KEY 并设置 "
            "LLM_MOCK=0 后，将输出真实 AI 复盘。\n\n"
            "## 一、行情回顾\n市场整体平稳，指数窄幅震荡，成交量维持近期均值。\n\n"
            "## 二、热点与情绪\n无显著主线热点，市场情绪中性。\n\n"
            "## 三、持仓点评\n当前暂无模拟持仓，无点评内容。\n\n"
            "## 四、风险提示\n模拟环境，仅供学习研究，不构成投资建议。\n\n"
            "## 五、明日关注\n关注宏观消息面与自选池个股量能变化。\n"
        )

    async def chat_json(self, messages: list[dict], temperature: float = 0.3,
                        schema_hint: str = "") -> dict:
        if schema_hint == "sentiment":
            n = self._count_items(messages)
            return {
                "results": [
                    {"index": i, "score": 0.3, "label": "中性", "reason": "模拟打分（未配置真实模型）"}
                    for i in range(n)
                ]
            }
        if schema_hint == "risk":
            return {
                "approved": True,
                "risk_level": "low",
                "reasons": ["模拟检查通过（未配置真实模型）"],
            }
        if schema_hint == "multiagent":
            return self._multiagent_shape(messages)

        if schema_hint == "lessons":
            return {"lessons": ["模拟教训：预测回顾需要在真实数据下验证（未配置真实模型）"]}

        if schema_hint == "predictions":
            return {"predictions": [
                {"category": "大盘", "symbol": "", "content": "模拟预测：指数明日维持震荡（未配置真实模型）"},
            ]}

        if schema_hint == "verify":
            n = 0
            for m in messages:
                content = m.get("content", "")
                if isinstance(content, str) and "待核验预测" in content:
                    for line in content.splitlines():
                        if line.strip() and line.strip()[0].isdigit():
                            n += 1
            return {"results": [
                {"index": i + 1, "verdict": "correct", "actual": "模拟核验（未配置真实模型）", "lesson": ""}
                for i in range(n)
            ]}

        if schema_hint == "codegen":
            return {
                "code": (
                    "from app.strategies.base import Strategy\n"
                    "from app.ml.indicators import ma\n\n\n"
                    "class GeneratedMAStrategy(Strategy):\n"
                    "    name = 'generated_ma'\n"
                    "    display_name = '生成的均线策略'\n"
                    "    params = {'n': 10}\n"
                    "    params_schema = [{'name': 'n', 'type': 'int', 'default': 10, "
                    "'min': 2, 'max': 60, 'label': '均线周期'}]\n\n"
                    "    def on_init(self, data):\n"
                    "        self._ma = {s: dict(zip(df['trade_date'], ma(df['close'], "
                    "self.params['n']))) for s, df in data.items()}\n\n"
                    "    def on_bar(self, bar):\n"
                    "        value = self._ma[bar.symbol].get(bar.trade_date)\n"
                    "        if value is None:\n"
                    "            return\n"
                    "        pos = self.position(bar.symbol)\n"
                    "        if bar.close > value and (pos is None or pos.volume == 0):\n"
                    "            self.buy(bar.symbol)\n"
                    "        elif bar.close < value and pos is not None and pos.volume > 0:\n"
                    "            self.sell(bar.symbol)\n"
                )
            }
        raise ValueError(f"未知 schema_hint: {schema_hint}")

    @staticmethod
    def _multiagent_shape(messages: list[dict]) -> dict:
        """多智能体各角色返回对应 JSON 形状（按 system prompt 关键词区分）。"""
        text = "".join(str(m.get("content", "")) for m in messages)
        if "技术面分析师" in text or "消息面分析师" in text or "资金面分析师" in text or "市场环境分析师" in text:
            return {"view": "neutral", "confidence": 0.5,
                    "key_points": ["模拟分析（未配置真实模型）"], "risks": []}
        # 注意顺序：交易员的 prompt 会引用"研究经理"，必须先匹配交易员
        if "交易员" in text:
            return {"action": "hold", "position": 0, "entry": "—", "exit": "—",
                    "reasoning": "模拟决策（未配置真实模型）"}
        if "研究经理" in text:
            return {"summary": "模拟汇总：四分析师观点中性，建议观望（未配置真实模型）",
                    "agreements": ["模拟共识"], "disagreements": []}
        if "风控官" in text:
            return {"approved": True, "risk_level": "low", "warnings": ["模拟审查（未配置真实模型）"]}
        return {"view": "neutral", "confidence": 0.5, "key_points": [], "risks": []}

    @staticmethod
    def _count_items(messages: list[dict]) -> int:
        """从用户消息中估算新闻条数（每条一行）。"""
        count = 0
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, str) and m.get("role") == "user":
                count = max(count, sum(1 for line in content.splitlines() if line.strip()))
        return max(count, 1)
