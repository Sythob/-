"""多智能体分析测试（离线 Mock 全链路）。"""
import asyncio

from app.data.models import AiReport
from app.data.sync import _upsert_daily_bars
from app.llm.mock_provider import MockProvider
from app.llm.tasks.multiagent import analyze_multiagent, _build_report


def test_mock_multiagent_shapes():
    """Mock 按角色返回对应 JSON 形状。"""
    provider = MockProvider()

    async def run():
        analyst = await provider.chat_json(
            [{"role": "system", "content": "你是技术面分析师..."}], schema_hint="multiagent")
        manager = await provider.chat_json(
            [{"role": "system", "content": "你是研究经理..."}], schema_hint="multiagent")
        trader = await provider.chat_json(
            [{"role": "system", "content": "你是交易员..."}], schema_hint="multiagent")
        risk = await provider.chat_json(
            [{"role": "system", "content": "你是风控官..."}], schema_hint="multiagent")
        return analyst, manager, trader, risk

    analyst, manager, trader, risk = asyncio.run(run())
    assert "view" in analyst and "key_points" in analyst
    assert "summary" in manager
    assert "action" in trader and "position" in trader
    assert "approved" in risk and "risk_level" in risk


def test_multiagent_full_flow(tmp_db, monkeypatch):
    """全链路：4 分析师 → 经理 → 交易员 → 风控 → 报告入库（全程离线）。"""
    # 数据收集里的两融/板块是真实网络调用，测试必须打桩
    monkeypatch.setattr(
        "app.data.sentiment.get_sentiment_overview",
        lambda: {"breadth": [], "margin": {"margin_balance": 0.0, "source": "test"}},
    )
    monkeypatch.setattr(
        "app.data.sectors.get_sectors",
        lambda: [{"name": "测试板块", "pct_change": 1.0}],
    )
    from tests.test_phase3 import _seed_symbol

    _seed_symbol(tmp_db, "600519", seed=5, n=80)
    result = asyncio.run(analyze_multiagent("600519", provider=MockProvider()))
    assert result["model"] == "mock"
    assert result["decision"]["analysts"] and len(result["decision"]["analysts"]) == 4
    assert "action" in result["decision"]["trader"]
    assert "approved" in result["decision"]["risk"]
    assert "多智能体分析" in result["content_md"]
    with tmp_db() as session:
        row = session.query(AiReport).filter(AiReport.report_type == "multiagent").first()
        assert row is not None
        assert "600519" in row.title


def test_build_report_sections():
    decision = {
        "analysts": [{"name": "技术面分析师", "view": "bullish", "confidence": 0.8,
                      "key_points": ["趋势向上"], "risks": []}],
        "manager": {"summary": "综合看多", "agreements": ["共识1"], "disagreements": []},
        "trader": {"action": "buy", "position": 30, "entry": "1200-1250",
                   "exit": "1150", "reasoning": "理由"},
        "risk": {"approved": True, "risk_level": "medium", "warnings": ["注意波动"]},
    }
    md = _build_report("600519", "2026-10-03", decision)
    assert "分析师团队观点" in md
    assert "交易建议" in md
    assert "风控审查" in md
    assert "不构成投资建议" in md
