"""市场情绪数据：涨跌停/涨跌家数（新浪全市场快照本地统计）+ 两融余额（交易所官网）。

北向资金依赖东财接口（本机被封），暂缺，见 docs/data-notes.md。
广度数据每日收盘后同步入库（market_breadth 表）；两融实时拉取（官网接口，TTL 缓存）。
"""
import threading
import time
from datetime import datetime

import pandas as pd

from app.data import calendar, database
from app.data.models import MarketBreadth
from app.data.sync import get_source
from app.utils.logger import get_logger

logger = get_logger("data.sentiment")

LIMIT_THRESHOLD = 9.8  # 涨跌停近似阈值（%）

_margin_cache: tuple[float, dict | None] = (0.0, None)
_margin_lock = threading.Lock()


def compute_breadth(quotes: pd.DataFrame) -> dict:
    """从全市场快照统计市场广度。quotes 需含 pct_change 列。"""
    if quotes is None or quotes.empty:
        return {"limit_up": 0, "limit_down": 0, "advance": 0, "decline": 0, "total": 0}
    pct = pd.to_numeric(quotes["pct_change"], errors="coerce")
    return {
        "limit_up": int((pct >= LIMIT_THRESHOLD).sum()),
        "limit_down": int((pct <= -LIMIT_THRESHOLD).sum()),
        "advance": int((pct > 0).sum()),
        "decline": int((pct < 0).sum()),
        "total": int(len(quotes)),
    }


def sync_breadth() -> dict:
    """拉全市场快照 → 统计广度 → 入库（按日期 upsert）。"""
    try:
        quotes = get_source().get_realtime_quotes(None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("广度同步失败（快照不可用）: %s", exc)
        return {"ok": False, "error": str(exc)[:80]}
    breadth = compute_breadth(quotes)
    # 数据日期 = 最新交易日（周末/节假日运行也不会写入错误日期）
    today = calendar.latest_trade_date()
    with database.get_session() as session:
        row = session.get(MarketBreadth, today)
        if row is None:
            row = MarketBreadth(date=today)
            session.add(row)
        row.limit_up = breadth["limit_up"]
        row.limit_down = breadth["limit_down"]
        row.advance = breadth["advance"]
        row.decline = breadth["decline"]
        row.total = breadth["total"]
        session.commit()
    logger.info("市场广度已入库: %s", breadth)
    return {"ok": True, **breadth}


def sentiment_cycle_brief() -> str:
    """短线情绪周期指标：涨停晋级率 / 炸板率 / 连板梯队 / 跌停趋势（纯本地库计算）。

    这些是 A股短线预测最有效的领先指标，注入预测提示词提升准确率。
    """
    import threading
    import time as _time

    global _cycle_cache
    with _cycle_lock:
        if _cycle_cache[0] and _time.monotonic() - _cycle_cache[0] < 600:
            return _cycle_cache[1]

    from app.data import calendar
    from app.data.models import BoardWatch, DailyBar, MarketBreadth
    from app.data.board_screener import limit_threshold

    lt = calendar.latest_trade_date()
    lines = []
    try:
        with database.get_session() as session:
            rows = (
                session.query(DailyBar.symbol, DailyBar.close, DailyBar.high,
                              DailyBar.pct_change)
                .filter(DailyBar.trade_date == lt).all()
            )
            breadth = (
                session.query(MarketBreadth)
                .order_by(MarketBreadth.date.desc()).limit(2).all()
            )
            board = session.query(BoardWatch).filter(BoardWatch.date == lt).all()
        touched = capped = 0
        for symbol, close, high, pct in rows:
            prev_close = close / (1 + pct / 100) if pct and pct > -99 else close
            th = limit_threshold(symbol) / 100
            limit_price = prev_close * (1 + th)
            if high >= limit_price * 0.998:
                touched += 1
                if close >= limit_price * 0.998:
                    capped += 1
        blast_rate = (touched - capped) / touched * 100 if touched else 0.0
        lines.append(f"今日触板 {touched} 只、封板 {capped} 只、炸板率 {blast_rate:.0f}%")
        # 涨停晋级率：昨日涨停 → 今日连板
        prev_limit = next((b.limit_up for b in breadth if b.date < lt), 0)
        streak2 = sum(1 for b in board if b.streak >= 2)
        promote = streak2 / prev_limit * 100 if prev_limit else 0.0
        lines.append(f"涨停晋级率：昨日涨停 {prev_limit} 家 → 今日连板 {streak2} 家（{promote:.0f}%）")
        # 连板梯队
        t2 = sum(1 for b in board if b.streak == 2)
        t3 = sum(1 for b in board if b.streak == 3)
        t4 = sum(1 for b in board if b.streak >= 4)
        lines.append(f"连板梯队：2板 {t2} / 3板 {t3} / 4板+ {t4}")
        # 跌停趋势
        cur_down = next((b.limit_down for b in breadth if b.date == lt), 0)
        prev_down = next((b.limit_down for b in breadth if b.date < lt), 0)
        lines.append(f"今日跌停 {cur_down} 家（昨日 {prev_down} 家）")
    except Exception as exc:  # noqa: BLE001
        logger.warning("情绪周期指标计算失败: %s", exc)
        return ""
    brief = "；".join(lines)
    with _cycle_lock:
        _cycle_cache = (_time.monotonic(), brief)
    return brief


_cycle_cache: tuple[float, str] = (0.0, "")
_cycle_lock = threading.Lock()


def latest_breadth() -> dict:
    with database.get_session() as session:
        rows = (
            session.query(MarketBreadth)
            .order_by(MarketBreadth.date.desc()).limit(5).all()
        )
        return [
            {"date": r.date, "limit_up": r.limit_up, "limit_down": r.limit_down,
             "advance": r.advance, "decline": r.decline, "total": r.total}
            for r in rows
        ]


def get_margin_summary() -> dict:
    """上交所两融余额（官网接口，TTL 30 分钟缓存）。"""
    global _margin_cache
    with _margin_lock:
        if _margin_cache[1] is not None and time.monotonic() - _margin_cache[0] < 1800:
            return _margin_cache[1]
    try:
        import akshare as ak

        start = (datetime.now() - pd.Timedelta(30, unit="D")).strftime("%Y%m%d")
        end = datetime.now().strftime("%Y%m%d")
        df = ak.stock_margin_sse(start_date=start, end_date=end)
        if df is None or df.empty:
            raise ValueError("两融数据为空")
        df = df.sort_values("信用交易日期") if "信用交易日期" in df.columns else df
        last = df.iloc[-1]
        summary = {
            "date": str(last.get("信用交易日期", "")),
            "margin_balance": float(last.get("融资余额", 0) or 0),      # 融资余额（元）
            "short_balance": float(last.get("融券余量", 0) or 0),       # 融券余量
            "source": "上交所官网",
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("两融数据获取失败: %s", exc)
        summary = {"date": "", "margin_balance": 0.0, "short_balance": 0.0,
                   "source": "不可用", "error": str(exc)[:80]}
    with _margin_lock:
        _margin_cache = (time.monotonic(), summary)
    return summary


def market_regime() -> dict:
    """市场状态判定（做T候选池门控用）。

    基于最新广度：涨跌家数比 + 涨跌停家数对比。
    - strong（强势）：上涨占比 ≥60% 且涨停 > 跌停
    - weak（弱势）：上涨占比 ≤35% 或跌停 > 涨停
    - neutral（震荡）：其余
    弱势时建议收紧做T趋势条件（MA20 与 MA60 双向上）。
    """
    breadth = latest_breadth()
    if not breadth:
        return {"regime": "unknown", "advice": "暂无广度数据（收盘后自动同步，或系统页点同步数据）",
                "trend_strict": False}
    b = breadth[0]
    total = b["advance"] + b["decline"]
    advance_ratio = b["advance"] / total if total > 0 else 0.5
    if advance_ratio >= 0.6 and b["limit_up"] > b["limit_down"]:
        regime = "strong"
        advice = "市场情绪偏强，做T顺风；注意避免追高连续大涨的标的"
    elif advance_ratio <= 0.35 or b["limit_down"] > b["limit_up"]:
        regime = "weak"
        advice = "弱势环境做T胜率下降，建议半仓或观望；趋势条件已自动收紧（需 MA20 与 MA60 双向上）"
    else:
        regime = "neutral"
        advice = "震荡市是经典做T环境，优选振幅大且趋势平稳的标的"
    return {
        "regime": regime,
        "date": b["date"],
        "advance": b["advance"], "decline": b["decline"],
        "limit_up": b["limit_up"], "limit_down": b["limit_down"],
        "advance_ratio": round(advance_ratio, 3),
        "advice": advice,
        "trend_strict": regime == "weak",
    }


def get_sentiment_overview() -> dict:
    """情绪总览：广度（历史 5 日）+ 两融 + 数据源说明。"""
    return {
        "breadth": latest_breadth(),
        "margin": get_margin_summary(),
        "note": "北向资金暂缺（东财接口在本机网络不可用）",
    }
