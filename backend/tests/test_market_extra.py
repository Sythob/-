"""市场数据扩展测试：板块成分股/龙虎榜/资金流向/北向资金（akshare 全 mock，离线）。"""
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.data import market_extra, sectors
from app.main import app


@pytest.fixture(autouse=True)
def _clear_caches(monkeypatch):
    """每个测试清空 TTL 缓存，避免用例间串数据。"""
    market_extra._cache.clear()
    sectors._cache = (0.0, [])
    yield


def _fake_sector_spot(**kwargs):
    return pd.DataFrame([
        {"label": "new_blhy", "板块": "玻璃行业", "公司家数": 19, "平均价格": 15.15,
         "涨跌额": 0.06, "涨跌幅": 0.45, "总成交量": 4e8, "总成交额": 8.2e9,
         "股票代码": "sh600293", "个股-涨跌幅": 9.95, "个股-当前价": 4.75,
         "个股-涨跌额": 0.43, "股票名称": "三峡新材"},
        {"label": "new_cbzz", "板块": "船舶制造", "公司家数": 8, "平均价格": 14.01,
         "涨跌额": -0.16, "涨跌幅": -1.12, "总成交量": 2e8, "总成交额": 3.4e9,
         "股票代码": "sz300123", "个股-涨跌幅": 0.26, "个股-当前价": 3.78,
         "个股-涨跌额": 0.01, "股票名称": "ST亚光"},
    ])


def _fake_sector_detail():
    return pd.DataFrame([
        {"代码": "600293", "名称": "三峡新材", "最新价": 4.75, "涨跌幅": 9.95,
         "涨跌额": 0.43, "成交量": 1e8, "成交额": 4.7e8},
        {"代码": "601636", "名称": "旗滨集团", "最新价": 5.10, "涨跌幅": -1.2,
         "涨跌额": -0.06, "成交量": 2e7, "成交额": 1.0e8},
    ])


def test_sector_detail_endpoint(tmp_db, monkeypatch):
    monkeypatch.setattr("akshare.stock_sector_spot", _fake_sector_spot)
    monkeypatch.setattr("akshare.stock_sector_detail", lambda sector: _fake_sector_detail())
    with TestClient(app) as client:
        resp = client.get("/api/market/sectors/玻璃行业/stocks")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "玻璃行业"
        assert body["stocks"][0]["code"] == "600293"  # 按涨跌幅降序
        assert body["stocks"][1]["code"] == "601636"
        # 未知板块 → 400
        resp2 = client.get("/api/market/sectors/不存在的板块/stocks")
        assert resp2.status_code == 400


def test_lhb_endpoint(tmp_db, monkeypatch):
    monkeypatch.setattr("akshare.stock_lhb_detail_daily_sina", lambda date: pd.DataFrame([
        {"序号": 1, "股票代码": "000056", "股票名称": "*ST皇庭", "收盘价": 2.09,
         "对应值": -10.34, "成交量": 6612.81, "成交额": 13900.59, "指标": "跌幅偏离值达7%的证券"},
    ]))
    with TestClient(app) as client:
        resp = client.get("/api/market/lhb")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["rows"][0]["code"] == "000056"
        assert body["rows"][0]["reason"] == "跌幅偏离值达7%的证券"
        assert body["date"]  # 最近交易日


def test_fund_flow_endpoint(tmp_db, monkeypatch):
    df = pd.DataFrame([
        {"序号": 1, "行业": "汽车零部件", "行业指数": 1, "行业-涨跌幅": 2.5,
         "流入资金": 5e9, "流出资金": 3e9, "净额": 2e9, "公司家数": 100,
         "领涨股": "A股", "领涨股-涨跌幅": 10.0, "当前价": 1},
        {"序号": 2, "行业": "银行", "行业指数": 2, "行业-涨跌幅": -1.0,
         "流入资金": 1e9, "流出资金": 2e9, "净额": -1e9, "公司家数": 42,
         "领涨股": "B股", "领涨股-涨跌幅": 0.5, "当前价": 2},
    ])
    monkeypatch.setattr("akshare.stock_fund_flow_industry", lambda symbol: df)
    monkeypatch.setattr("akshare.stock_fund_flow_concept", lambda symbol: df.rename(columns={"行业": "概念"}))
    with TestClient(app) as client:
        resp = client.get("/api/market/fund-flow")
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert rows[0]["name"] == "汽车零部件"  # 净额降序
        assert rows[1]["net"] == -1e9
        assert client.get("/api/market/fund-flow", params={"kind": "concept"}).status_code == 200
        assert client.get("/api/market/fund-flow", params={"kind": "bad"}).status_code == 400


def test_northbound_endpoint_filters_north_only(tmp_db, monkeypatch):
    monkeypatch.setattr("akshare.stock_hsgt_fund_flow_summary_em", lambda: pd.DataFrame([
        {"交易日": "2026-10-21", "类型": "沪港通", "板块": "沪股通", "资金方向": "北向",
         "交易状态": 3, "成交净买额": 12.5, "资金净流入": 0, "当日资金余额": 0,
         "上涨数": 590, "持平数": 74, "下跌数": 978, "相关指数": "上证指数", "指数涨跌幅": -0.6},
        {"交易日": "2026-10-21", "类型": "沪港通", "板块": "港股通(沪)", "资金方向": "南向",
         "交易状态": 3, "成交净买额": -22.9, "资金净流入": 420, "当日资金余额": 0,
         "上涨数": 208, "持平数": 29, "下跌数": 430, "相关指数": "恒生指数", "指数涨跌幅": -0.9},
    ]))
    monkeypatch.setattr("akshare.stock_hsgt_hist_em", lambda: pd.DataFrame([
        {"日期": "2026-10-20", "当日成交净买额": 5.5e8, "买入成交额": 1e9,
         "卖出成交额": 4.5e8, "历史累计净买额": 1.8e12},
        {"日期": "2026-10-21", "当日成交净买额": -2.0e8, "买入成交额": 8e8,
         "卖出成交额": 1e9, "历史累计净买额": 1.8e12},
    ]))
    with TestClient(app) as client:
        resp = client.get("/api/market/northbound")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["summary"]) == 1  # 南向被过滤
        assert body["summary"][0]["board"] == "沪股通"
        assert len(body["hist"]) == 2
        assert body["hist"][0]["net"] == 5.5e8
