"""同花顺导入测试：解析容错（xlsx/HTML 伪装 xls/列名变体/代码清洗）+ 落库镜像语义。"""
import io
from datetime import datetime

import pandas as pd

from app.data.ths_import import (
    apply_positions, apply_trades, parse_positions, parse_trades,
)
from app.paper import account as account_service


def _xlsx(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, header=False)
    return buf.getvalue()


def test_parse_positions_xlsx(tmp_db):
    df = pd.DataFrame([
        ["证券代码", "证券名称", "股票余额", "可用余额", "成本价", "现价"],
        ["600519", "贵州茅台", "400", "400", "1,308.39", "1324.97"],
        ["002639", "雪人股份", "1,500", "1500", "14.52", "14.51"],
        ["合计", "", "1900", "", "", ""],  # 汇总行应跳过
    ])
    rows = parse_positions(_xlsx(df), "positions.xlsx")
    assert rows == [
        {"symbol": "600519", "name": "贵州茅台", "volume": 400, "available": 400, "cost": 1308.39},
        {"symbol": "002639", "name": "雪人股份", "volume": 1500, "available": 1500, "cost": 14.52},
    ]


def test_parse_positions_html_disguised_as_xls(tmp_db):
    """同花顺老版本常见：HTML 表格伪装成 .xls。"""
    html = (
        "<table><tr><td>证券代码</td><td>股票名称</td><td>持仓数量</td><td>成本价</td></tr>"
        "<tr><td>600519</td><td>贵州茅台</td><td>400</td><td>1308.39</td></tr></table>"
    ).encode("utf-8")
    rows = parse_positions(html, "positions.xls")
    assert len(rows) == 1
    assert rows[0]["symbol"] == "600519" and rows[0]["volume"] == 400


def test_parse_trades_direction_variants(tmp_db):
    df = pd.DataFrame([
        ["成交日期", "证券代码", "证券名称", "操作", "成交均价", "成交数量"],
        ["2024-01-05 09:31:00", "600519", "贵州茅台", "证券买入", "1300.00", "100"],
        ["2024/01/06", "600519", "贵州茅台", "证券卖出", "1310.50", "100"],
        ["2024-01-07", "600519", "贵州茅台", "申购配号", "1.00", "100"],  # 非买卖跳过
    ])
    rows = parse_trades(_xlsx(df), "trades.xlsx")
    assert len(rows) == 2
    assert rows[0]["direction"] == "buy" and rows[0]["trade_time"] == datetime(2024, 1, 5, 9, 31)
    assert rows[1]["direction"] == "sell"


def test_apply_positions_mirrors_account(tmp_db):
    """持仓导入 = 镜像覆盖：旧持仓清空，新持仓按文件落库，可用=持有（按已结算处理）。"""
    from app.data import database
    from app.data.models import PaperPosition

    account_service.upsert_position(1, "000001", 500, 500, 10.0)  # 旧持仓
    rows = [{"symbol": "600519", "name": "贵州茅台", "volume": 400, "available": 400, "cost": 1308.39}]
    n, warnings = apply_positions(rows)
    assert n == 1 and warnings == []
    with database.get_session() as session:
        positions = session.query(PaperPosition).all()
        assert [(p.symbol, p.volume, p.available_volume, p.avg_cost) for p in positions] == \
            [("600519", 400, 400, 1308.39)]


def test_apply_trades_appends_filled_orders(tmp_db):
    from app.data import database
    from app.data.models import PaperOrder

    rows = [{
        "symbol": "600519", "name": "贵州茅台", "direction": "buy",
        "price": 1300.0, "volume": 100, "fee": 5.0,
        "trade_time": datetime(2024, 1, 5, 9, 31),
    }]
    n, warnings = apply_trades(rows)
    assert n == 1 and warnings == []
    with database.get_session() as session:
        order = session.query(PaperOrder).one()
        assert order.status == "filled"
        assert order.direction == "buy" and order.filled_price == 1300.0


def test_import_endpoint(tmp_db):
    """端到端：上传持仓文件 → 接口返回计数 → 账户汇总反映新持仓。"""
    from fastapi.testclient import TestClient

    from app.main import app

    df = pd.DataFrame([
        ["证券代码", "证券名称", "股票余额", "可用余额", "成本价"],
        ["600519", "贵州茅台", "400", "400", "1308.39"],
    ])
    content = _xlsx(df)
    with TestClient(app) as client:
        resp = client.post(
            "/api/paper/import-ths",
            files={"positions_file": ("positions.xlsx", content,
                                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["positions_imported"] == 1 and body["trades_imported"] == 0
        summary = client.get("/api/paper/account").json()
        assert summary["positions"][0]["symbol"] == "600519"
        assert summary["positions"][0]["volume"] == 400


def test_import_endpoint_rejects_no_files(tmp_db):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        resp = client.post("/api/paper/import-ths")
        assert resp.status_code == 400
        assert "至少上传" in resp.json()["detail"]
