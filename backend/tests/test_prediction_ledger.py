"""预测台账测试：提取入账、次日自动核验、错误沉淀教训、周报生成与周五门槛（全 Mock 离线）。"""
import asyncio

from fastapi.testclient import TestClient

from app.data import database
from app.data.models import AiLesson, AiPrediction, AiReport
from app.llm.mock_provider import MockProvider
from app.llm.tasks.daily_report import extract_predictions, verify_pending_predictions
from app.llm.tasks.weekly_report import generate_weekly_report
from app.main import app


def test_extract_predictions_writes_ledger(tmp_db):
    n = asyncio.run(extract_predictions(
        MockProvider(), "# 报告\n## 五、明日关注\n判断：沪深300 明日收涨", "2026-10-21"))
    assert n == 1
    with database.get_session() as session:
        row = session.query(AiPrediction).one()
        assert row.report_date == "2026-10-21"
        assert row.horizon == "daily"
        assert row.verdict == "pending"


def test_verify_pending_updates_verdicts(tmp_db):
    with database.get_session() as session:
        session.add(AiPrediction(report_date="2026-10-20", horizon="daily",
                                 category="大盘", content="判断：沪深300 明日收涨",
                                 verdict="pending"))
        session.commit()
    text, n = asyncio.run(verify_pending_predictions(MockProvider(), "2026-10-21"))
    assert n == 1
    assert "✅" in text
    with database.get_session() as session:
        row = session.query(AiPrediction).one()
        assert row.verdict == "correct"
        assert row.actual  # 核验写入了实际情况


def test_verify_skips_same_day_pending(tmp_db):
    """当天刚入库的预测不核验（report_date == latest_trade 只核验更早的）。"""
    with database.get_session() as session:
        session.add(AiPrediction(report_date="2026-10-21", horizon="daily",
                                 category="个股", symbol="600519",
                                 content="判断：茅台明日上涨", verdict="pending"))
        session.commit()
    text, n = asyncio.run(verify_pending_predictions(MockProvider(), "2026-10-21"))
    assert n == 0 and text == ""
    with database.get_session() as session:
        assert session.query(AiPrediction).one().verdict == "pending"


def test_wrong_verdict_saves_lesson(tmp_db, monkeypatch):
    """核验为错/部分对的预测自动沉淀教训库。"""
    with database.get_session() as session:
        session.add(AiPrediction(report_date="2026-10-20", horizon="daily",
                                 category="大盘", content="判断：上证 明日大涨",
                                 verdict="pending"))
        session.commit()

    class WrongMock(MockProvider):
        async def chat_json(self, messages, temperature=0.3, schema_hint=""):
            if schema_hint == "verify":
                return {"results": [{"index": 1, "verdict": "wrong",
                                     "actual": "上证收跌 1.2%", "lesson": "弱势市勿追高"}]}
            return await super().chat_json(messages, temperature, schema_hint)

    text, n = asyncio.run(verify_pending_predictions(WrongMock(), "2026-10-21"))
    assert n == 1 and "❌" in text
    with database.get_session() as session:
        row = session.query(AiPrediction).one()
        assert row.verdict == "wrong"
        assert row.lesson == "弱势市勿追高"
        lesson = session.query(AiLesson).one()
        assert lesson.lesson == "弱势市勿追高"


def test_weekly_report_generates_and_extracts(tmp_db):
    result = asyncio.run(generate_weekly_report(provider=MockProvider(), date="2026-10-16"))
    assert "每周复盘" in result["title"]
    assert result["predictions"] == 1
    with database.get_session() as session:
        row = session.query(AiReport).filter(AiReport.report_type == "weekly").one()
        assert row.title == "每周复盘 2026-10-16"
        pred = session.query(AiPrediction).one()
        assert pred.horizon == "weekly"


def test_weekly_skips_non_friday(tmp_db, monkeypatch):
    """非周五收盘后拒绝生成（今天 2026-10-21 是周二）。"""
    result = asyncio.run(generate_weekly_report(provider=MockProvider()))
    assert result.get("skipped") is True
    assert "周五" in result.get("reason", "")


def test_news_sentiment_api(tmp_db):
    """新闻情绪列表：只返回已打分且日期范围内的新闻，附分布统计。"""
    from app.data import calendar as calendar_module
    from app.data.models import News

    latest = calendar_module.latest_trade_date()
    with database.get_session() as session:
        session.add(News(symbol="600519", title="茅台提价", url="http://x/n1",
                         pub_time=f"{latest} 10:00:00", sentiment_label="利好",
                         sentiment_score=0.8, sentiment_reason="提价增厚利润"))
        session.add(News(symbol="600519", title="旧闻", url="http://x/n2",
                         pub_time="2026-10-10 10:00:00", sentiment_label="利好",
                         sentiment_score=0.5, sentiment_reason="旧"))
        session.add(News(symbol="600519", title="未打分", url="http://x/n3",
                         pub_time=f"{latest} 11:00:00"))
        session.commit()
    with TestClient(app) as client:
        resp = client.get("/api/ai/news-sentiment")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["stats"]["利好"] == 1  # 只统计范围内的已打分新闻
        assert len(body["rows"]) == 1
        assert body["rows"][0]["title"] == "茅台提价"
        assert body["rows"][0]["reason"] == "提价增厚利润"


def test_predictions_api(tmp_db):
    with database.get_session() as session:
        session.add(AiPrediction(report_date="2026-10-20", horizon="daily",
                                 category="大盘", content="判断：指数上涨",
                                 verdict="correct", actual="指数涨 0.5%"))
        session.add(AiPrediction(report_date="2026-10-20", horizon="daily",
                                 category="个股", symbol="600519",
                                 content="判断：茅台下跌", verdict="wrong",
                                 actual="茅台涨 2%", lesson="勿逆势看空龙头"))
        session.commit()
    with TestClient(app) as client:
        resp = client.get("/api/ai/predictions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["stats"]["correct"] == 1 and body["stats"]["wrong"] == 1
        assert body["stats"]["accuracy"] == 50.0
        assert len(body["rows"]) == 2
        # 周报列表
        resp2 = client.get("/api/ai/weekly")
        assert resp2.status_code == 200 and resp2.json() == []
