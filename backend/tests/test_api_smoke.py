"""API 冒烟：TestClient + 临时 DB，覆盖主要端点（全程离线）。"""
import time

from fastapi.testclient import TestClient

from app.data.sync import _upsert_daily_bars
from app.main import app
from tests.helpers import make_daily


def _seed_bars(tmp_db):
    rows = []
    for _, r in make_daily().iterrows():
        rows.append({**r.to_dict(), "symbol": "600519", "adj_factor": 1.0})
    _upsert_daily_bars(rows)


def test_health_and_strategies(tmp_db):
    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        metas = client.get("/api/strategies").json()
        names = {m["name"] for m in metas}
        assert {"dual_ma", "rsi_reversal", "ml_signal"} <= names
        # 参数 schema 供 UI 表单
        dual_ma = next(m for m in metas if m["name"] == "dual_ma")
        assert dual_ma["params"] == {"fast": 5, "slow": 20}
        assert len(dual_ma["params_schema"]) == 2


def test_bars_and_backtest_flow(tmp_db):
    _seed_bars(tmp_db)
    with TestClient(app) as client:
        # 行情接口
        bars = client.get("/api/market/bars/600519", params={"adjust": "raw"}).json()
        assert len(bars) == 4
        assert bars[0]["close"] == 10.2

        # 回测流程：提交 → 轮询 → 结果
        run = client.post("/api/backtest/run", json={
            "strategy": "dual_ma", "symbols": ["600519"],
            "start": "2020-01-01", "capital": 100000,
        }).json()
        run_id = run["run_id"]
        status = "pending"
        for _ in range(100):  # 最长 ~10s
            time.sleep(0.1)
            detail = client.get(f"/api/backtest/runs/{run_id}").json()
            status = detail["status"]
            if status in ("done", "failed"):
                break
        assert status == "done", f"回测失败: {detail.get('error')}"
        assert detail["metrics"]["initial_capital"] == 100000
        assert len(detail["equity"]) == 4
        assert detail["metrics"]["total_return"] >= -1.0

        # 回测列表
        runs = client.get("/api/backtest/runs").json()
        assert runs[0]["id"] == run_id


def test_market_and_system_endpoints(tmp_db):
    with TestClient(app) as client:
        assert client.get("/api/market/instruments").json() == []
        assert client.get("/api/market/news").json() == []
        assert client.get("/api/ml/models").json() == []
        cfg = client.get("/api/system/config").json()
        assert cfg["watchlist"] and "deepseek_api_key" in cfg
        scheduler = client.get("/api/system/scheduler").json()
        assert "running" in scheduler and isinstance(scheduler["jobs"], list)
        assert isinstance(client.get("/api/system/tasks").json(), list)  # 其他测试可能留有任务


def test_unknown_strategy_rejected(tmp_db):
    with TestClient(app) as client:
        resp = client.post("/api/backtest/run", json={
            "strategy": "no_such_strategy", "symbols": ["600519"],
        })
        assert resp.status_code == 400


def test_watchlist_update_persists(tmp_db, tmp_path, monkeypatch):
    """自选池更新：写入 .env 并热更新缓存，无需重启（.env 隔离到临时目录，不污染真实配置）。"""
    import app.config as config_module

    from app.config import get_settings

    monkeypatch.setattr(config_module, "BASE_DIR", tmp_path)  # .env 写入临时目录
    original_watchlist = get_settings().watchlist
    try:
        with TestClient(app) as client:
            resp = client.put("/api/system/watchlist", json={"symbols": ["600519", "000001"]})
            assert resp.status_code == 200
            assert resp.json()["watchlist"] == ["600519", "000001"]
            # 热更新生效
            assert get_settings().watchlist_symbols == ["600519", "000001"]
            # 配置接口同步返回
            cfg = client.get("/api/system/config").json()
            assert cfg["watchlist"] == ["600519", "000001"]
            # .env 文件确实写入了临时目录
            env_file = tmp_path / ".env"
            assert env_file.exists()
            assert "WATCHLIST=600519,000001" in env_file.read_text(encoding="utf-8")
    finally:
        get_settings().watchlist = original_watchlist  # 恢复缓存值
