"""交易笔记测试：CRUD、每日主笔记 upsert、复盘引用（离线）。"""
from fastapi.testclient import TestClient

from app.main import app


def test_notes_crud_and_daily_upsert(tmp_db):
    with TestClient(app) as client:
        # 初始为空（返回生效日期 + 空列表）
        resp = client.get("/api/paper/notes")
        assert resp.status_code == 200
        body = resp.json()
        assert body["rows"] == []
        assert body["date"]  # 最新交易日

        date = body["date"]
        # 建每日笔记
        r1 = client.post("/api/paper/notes",
                         json={"date": date, "kind": "daily", "content": "今天看好白酒"})
        assert r1.status_code == 200 and r1.json()["updated"] is False
        # 同日再建 = 更新（每天一篇）
        r2 = client.post("/api/paper/notes",
                         json={"date": date, "kind": "daily", "content": "改为看空白酒"})
        assert r2.status_code == 200 and r2.json()["updated"] is True
        # 盘中随手记两条
        client.post("/api/paper/notes", json={"date": date, "kind": "quick", "content": "早盘冲高"})
        client.post("/api/paper/notes", json={"date": date, "kind": "quick", "content": "尾盘跳水"})
        # 列表：1 主笔记 + 2 随手记
        rows = client.get("/api/paper/notes").json()["rows"]
        assert len(rows) == 3
        daily = [r for r in rows if r["kind"] == "daily"][0]
        assert daily["content"] == "改为看空白酒"
        # 编辑与删除
        qid = [r for r in rows if r["kind"] == "quick"][0]["id"]
        assert client.put(f"/api/paper/notes/{qid}", json={"content": "早盘冲高回落"}).status_code == 200
        assert client.delete(f"/api/paper/notes/{qid}").status_code == 200
        assert len(client.get("/api/paper/notes").json()["rows"]) == 2
        # 校验：空内容 / 非法 kind
        assert client.post("/api/paper/notes", json={"kind": "daily", "content": "  "}).status_code == 400
        assert client.post("/api/paper/notes", json={"kind": "bad", "content": "x"}).status_code == 400


def test_notes_brief_includes_daily_and_quick(tmp_db):
    """复盘笔记摘要：包含主笔记与随手记，无笔记返回空串。"""
    from app.data import calendar as calendar_module
    from app.data import database
    from app.data.models import TradingNote
    from app.llm.tasks.daily_report import _build_notes_brief

    assert _build_notes_brief() == ""
    lt = calendar_module.latest_trade_date()
    with database.get_session() as session:
        session.add(TradingNote(date=lt, kind="daily", content="主笔记"))
        session.add(TradingNote(date=lt, kind="quick", content="随手记"))
        session.commit()
    brief = _build_notes_brief()
    assert "[每日笔记] 主笔记" in brief
    assert "[盘中记录] 随手记" in brief
