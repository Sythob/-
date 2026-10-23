"""做T候选池筛选器测试（离线）。"""
import numpy as np
import pandas as pd

from app.data.models import Instrument
from app.data.sync import _upsert_daily_bars
from app.data.tscreener import (
    _evaluate,
    _save_watch_zone,
    coarse_screen_quotes,
    load_watch_zone,
    screen_tsymbols,
)


def _seed(tmp_db, symbol, name="测试股", n=30, price=50.0, amplitude=0.05,
          amount=1e9, turnover=6.0, drift=0.002, is_st=0):
    """合成日线：可指定振幅/成交额/换手/趋势。"""
    rng = np.random.default_rng(hash(symbol) % 9999)
    dates = pd.date_range("2024-01-02", periods=n, freq="B").strftime("%Y-%m-%d")
    rows = []
    for i, d in enumerate(dates):
        base = price * (1 + drift * i)
        amp = amplitude * base
        rows.append({
            "symbol": symbol, "trade_date": d,
            "open": base - amp / 4, "high": base + amp / 2, "low": base - amp / 2,
            "close": base + amp / 4, "volume": amount / base,
            "amount": amount, "pct_change": 0.0, "turnover_rate": turnover,
            "adj_factor": 1.0,
        })
    _upsert_daily_bars(rows)
    with tmp_db() as session:
        session.add(Instrument(symbol=symbol, name=name, type="stock", is_st=is_st))
        session.commit()


def test_evaluate_passes_good_candidate():
    close = np.linspace(50, 55, 25)  # 温和上升趋势
    df = pd.DataFrame({
        "open": close * 0.99, "high": close * 1.03, "low": close * 0.98, "close": close,
        "volume": [2e7] * 25, "amount": [1e9] * 25, "turnover_rate": [6.0] * 25,
    })
    result = _evaluate(df, "600001", "好票", 5e8, 0.03, 3.0, 10.0, 20)
    assert result is not None
    assert result["symbol"] == "600001"
    assert result["score"] > 0


def test_evaluate_rejects_low_amount():
    df = pd.DataFrame({
        "open": [50.0] * 25, "high": [52.6] * 25, "low": [49.9] * 25, "close": [51.0] * 25,
        "volume": [1e5] * 25, "amount": [1e7] * 25, "turnover_rate": [6.0] * 25,
    })
    assert _evaluate(df, "x", "小票", 5e8, 0.03, 0.03, 0.10, 20) is None  # 成交额不足


def test_evaluate_rejects_low_amplitude():
    df = pd.DataFrame({
        "open": [50.0] * 25, "high": [50.2] * 25, "low": [49.8] * 25, "close": [50.0] * 25,
        "volume": [2e7] * 25, "amount": [1e9] * 25, "turnover_rate": [6.0] * 25,
    })
    assert _evaluate(df, "x", "横盘票", 5e8, 0.03, 0.03, 0.10, 20) is None  # 振幅不足


def test_evaluate_rejects_downtrend():
    close = np.linspace(60, 40, 25)  # 持续下跌
    df = pd.DataFrame({
        "open": close * 0.99, "high": close * 1.03, "low": close * 0.97, "close": close,
        "volume": [2e7] * 25, "amount": [1e9] * 25, "turnover_rate": [6.0] * 25,
    })
    assert _evaluate(df, "x", "下跌票", 5e8, 0.03, 0.03, 0.10, 20) is None  # 趋势向下


def _spot_quotes():
    """合成全市场快照（新浪回退格式：含 pre_close/high/low/amount/name）。"""
    return pd.DataFrame([
        {"symbol": "600001", "name": "活跃股", "last_price": 10.5, "pct_change": 5.0,
         "open": 10.0, "high": 10.8, "low": 9.9, "pre_close": 10.0,
         "volume": 5e7, "amount": 5e8},
        {"symbol": "600002", "name": "*ST烂票", "last_price": 3.0, "pct_change": 4.0,
         "open": 2.9, "high": 3.1, "low": 2.8, "pre_close": 2.9,
         "volume": 5e7, "amount": 5e8},
        {"symbol": "600003", "name": "涨停票", "last_price": 11.0, "pct_change": 10.0,
         "open": 10.0, "high": 11.0, "low": 10.0, "pre_close": 10.0,
         "volume": 5e7, "amount": 5e8},
        {"symbol": "600004", "name": "低量票", "last_price": 10.5, "pct_change": 3.0,
         "open": 10.0, "high": 10.8, "low": 9.9, "pre_close": 10.0,
         "volume": 1e6, "amount": 1e7},
        {"symbol": "600005", "name": "横盘票", "last_price": 10.1, "pct_change": 1.0,
         "open": 10.0, "high": 10.2, "low": 9.9, "pre_close": 10.0,
         "volume": 5e7, "amount": 5e8},
    ])


def test_coarse_screen_filters():
    result = coarse_screen_quotes(_spot_quotes(), top_n=10)
    symbols = {r["symbol"] for r in result}
    assert "600001" in symbols     # 活跃股：振幅 9%、量足 → 入选
    assert "600002" not in symbols  # ST 排除
    assert "600003" not in symbols  # 涨停排除
    assert "600004" not in symbols  # 成交额不足排除
    assert "600005" not in symbols  # 振幅不足排除


def test_coarse_screen_empty():
    assert coarse_screen_quotes(pd.DataFrame()) == []


def test_watch_zone_persist_and_load(tmp_db):
    """观察区落库（upsert）与读取。"""
    rows = [
        {"symbol": "600001", "name": "活跃股", "amplitude_today": 8.5,
         "amount_today": 5e8, "pct_change": 4.2, "score": 1.5},
        {"symbol": "600002", "name": "次活跃", "amplitude_today": 5.1,
         "amount_today": 3e8, "pct_change": 2.0, "score": 1.1},
    ]
    assert _save_watch_zone(rows) == 2
    # 覆盖更新（同 symbol）
    _save_watch_zone([{**rows[0], "amplitude_today": 9.9}])
    zone = load_watch_zone()
    assert len(zone) == 2
    first = zone[0]
    assert first["symbol"] == "600001"
    assert first["amplitude_today"] == 9.9
    assert first["date"]  # 记录日期非空


def test_market_regime_gating(tmp_db):
    """市场状态判定：强/弱/震荡 + 弱势收紧趋势条件。"""
    from app.data.models import MarketBreadth
    from app.data.sentiment import market_regime

    # 无广度数据 → unknown
    assert market_regime()["regime"] == "unknown"

    dates = iter(["2024-01-05", "2024-01-08", "2024-01-09"])

    def seed_breadth(advance, decline, up, down):
        with tmp_db() as session:
            row = MarketBreadth(date=next(dates), limit_up=up, limit_down=down,
                                advance=advance, decline=decline, total=advance + decline)
            session.add(row)
            session.commit()

    seed_breadth(4000, 1000, 120, 10)   # 强势
    assert market_regime()["regime"] == "strong"
    assert market_regime()["trend_strict"] is False

    seed_breadth(1000, 4000, 10, 120)   # 弱势
    assert market_regime()["regime"] == "weak"
    assert market_regime()["trend_strict"] is True

    seed_breadth(2600, 2400, 50, 40)    # 震荡
    assert market_regime()["regime"] == "neutral"


def test_evaluate_trend_strict():
    """弱势门控：MA20 向上但 MA60 向下 → 严格模式下排除。"""
    import numpy as np

    n = 70
    # 前 45 日下跌、后 25 日反弹：MA20 向上但 MA60 仍向下
    close = np.concatenate([np.linspace(60, 40, 45), np.linspace(40, 55, 25)])
    df = pd.DataFrame({
        "open": close * 0.99, "high": close * 1.03, "low": close * 0.98, "close": close,
        "volume": [2e7] * n, "amount": [1e9] * n, "turnover_rate": [6.0] * n,
    })
    assert _evaluate(df, "x", "反弹股", 5e8, 0.03, 3.0, 10.0, 20, trend_strict=False) is not None
    assert _evaluate(df, "x", "反弹股", 5e8, 0.03, 3.0, 10.0, 20, trend_strict=True) is None


def test_screen_excludes_st_and_index(tmp_db):
    _seed(tmp_db, "600001", "好票", amount=1e9, amplitude=0.05, drift=0.003)
    _seed(tmp_db, "600002", "ST票", amount=1e9, amplitude=0.05, drift=0.003, is_st=1)
    _seed(tmp_db, "600003", "烂票", amount=1e7, amplitude=0.05, drift=0.003)
    result = screen_tsymbols()
    symbols = {r["symbol"] for r in result}
    assert "600001" in symbols
    assert "600002" not in symbols  # ST 排除
    assert "600003" not in symbols  # 流动性不足排除
