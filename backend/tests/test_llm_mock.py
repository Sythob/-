"""LLM 层离线测试：mock provider、JSON 降级解析、风控兜底、codegen 校验。"""
import asyncio

import pytest

from app.data.models import AiReport, News
from app.llm.deepseek import DeepSeekProvider
from app.llm.mock_provider import MockProvider
from app.llm.tasks.codegen import _strip_markdown, _validate_code
from app.llm.tasks.daily_report import generate_daily_report
from app.llm.tasks.news_sentiment import score_news
from app.llm.tasks.risk_check import check_order_risk

GOOD_CODE = """
from app.strategies.base import Strategy
from app.ml.indicators import ma


class GeneratedMAStrategy(Strategy):
    name = 'generated_ma'
    params = {'n': 10}

    def on_init(self, data):
        self._ma = {s: dict(zip(df['trade_date'], ma(df['close'], self.params['n'])))
                    for s, df in data.items()}

    def on_bar(self, bar):
        value = self._ma[bar.symbol].get(bar.trade_date)
        if value is None:
            return
        pos = self.position(bar.symbol)
        if bar.close > value and (pos is None or pos.volume == 0):
            self.buy(bar.symbol)
        elif bar.close < value and pos is not None and pos.volume > 0:
            self.sell(bar.symbol)
"""


def test_mock_provider_shapes():
    provider = MockProvider()
    r1 = asyncio.run(provider.chat_json([], schema_hint="sentiment"))
    assert "results" in r1 and len(r1["results"]) >= 1
    r2 = asyncio.run(provider.chat_json([], schema_hint="risk"))
    assert r2["approved"] is True and "risk_level" in r2
    r3 = asyncio.run(provider.chat_json([], schema_hint="codegen"))
    assert "code" in r3 and "class" in r3["code"]
    md = asyncio.run(provider.chat([]))
    assert "复盘" in md


def test_extract_json_fallback():
    assert DeepSeekProvider._extract_json('前缀 {"a": 1} 后缀') == {"a": 1}
    assert DeepSeekProvider._extract_json('{"a": 1}') == {"a": 1}
    with pytest.raises((ValueError, TypeError)):
        DeepSeekProvider._extract_json("没有JSON")


def test_risk_check_fallback_on_failure():
    """provider 抛异常 → 兜底放行（AI 故障不阻塞）。"""

    class BrokenProvider:
        name = "broken"

        async def chat(self, messages, temperature=0.3):
            raise TimeoutError("模拟超时")

        async def chat_json(self, messages, temperature=0.3, schema_hint=""):
            raise TimeoutError("模拟超时")

    result = asyncio.run(check_order_risk(
        {"symbol": "600519", "direction": "buy", "volume": 100, "price": 0, "order_type": "market"},
        {"x": 1}, "", {"cash": 100000, "total": 100000},
        provider=BrokenProvider(),
    ))
    assert result["approved"] is True
    assert result["checked"] is False


def test_news_sentiment_writes_db(tmp_db):
    with tmp_db() as session:
        session.add(News(symbol="600519", title="测试新闻", content="内容",
                         url="http://x/1", pub_time="2024-01-01 10:00:00"))
        session.commit()
    result = asyncio.run(score_news(limit=10, provider=MockProvider()))
    assert result["scored"] == 1
    with tmp_db() as session:
        row = session.query(News).first()
        assert row.sentiment_score is not None
        assert row.sentiment_label == "中性"


def test_daily_report_mock(tmp_db):
    result = asyncio.run(generate_daily_report(provider=MockProvider(), date="2024-01-05"))
    assert "复盘" in result["content_md"]
    with tmp_db() as session:
        row = session.query(AiReport).first()
        assert row.report_type == "daily"
        assert row.model == "mock"


def test_report_self_review_and_lessons(tmp_db):
    """自我总结闭环：第二期报告引用第一期 + 教训沉淀入库。"""
    from app.data.models import AiLesson

    # 第一期报告（含"明日关注"预测）
    with tmp_db() as session:
        session.add(AiReport(
            report_type="daily", title="每日复盘 2024-01-04",
            content_md="# 今日复盘（2024-01-04）\n## 五、明日关注\n- 预测：明日指数有望反弹\n",
            model="mock",
        ))
        session.commit()
    # 第二期报告：应引用上期并提炼教训
    result = asyncio.run(generate_daily_report(provider=MockProvider(), date="2024-01-05"))
    assert "复盘" in result["content_md"]
    assert "lessons" in result
    with tmp_db() as session:
        lessons = session.query(AiLesson).all()
        assert len(lessons) >= 1  # 教训已入库
        reports = session.query(AiReport).filter(AiReport.report_type == "daily").all()
        assert len(reports) == 2


def test_lessons_loaded_and_prior_found(tmp_db):
    """教训库读取与上期报告定位。"""
    from datetime import datetime, timedelta

    from app.data.models import AiLesson
    from app.llm.tasks.daily_report import _load_lessons, _load_prior_report

    with tmp_db() as session:
        session.add(AiReport(
            report_type="daily", title="每日复盘 2024-01-04",
            content_md="上一期内容", model="mock",
            created_at=datetime.now() - timedelta(days=1),
        ))
        session.add(AiLesson(date="2024-01-04", lesson="教训一"))
        session.commit()
    assert _load_prior_report() == "上一期内容"
    assert _load_lessons() == ["教训一"]


def test_codegen_validate_good():
    assert _validate_code(GOOD_CODE) == "generated_ma"


def test_codegen_reject_bad():
    with pytest.raises(ValueError):
        _validate_code("import os\n\nclass X(Strategy):\n    pass")
    with pytest.raises(ValueError):
        _validate_code(
            "import pandas as pd\n\nclass X(Strategy):\n"
            "    def on_bar(self, bar):\n        eval('1')"
        )
    with pytest.raises(ValueError):
        _validate_code("import pandas as pd\n\nclass X:\n    pass")  # 未继承 Strategy
    with pytest.raises(ValueError):
        _validate_code("import pandas as pd\n\nclass X(Strategy):\n    name = 'Bad Name'\n")
    with pytest.raises(SyntaxError):
        _validate_code("def broken(:")


def test_codegen_strip_markdown():
    assert _strip_markdown("```python\nimport pandas as pd\n```") == "import pandas as pd"
    assert _strip_markdown("  import pandas as pd  ") == "import pandas as pd"
