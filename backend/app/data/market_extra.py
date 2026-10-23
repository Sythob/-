"""市场数据扩展：板块行情 / 龙虎榜 / 资金流向 / 北向资金。

数据源（东财被部分 WAF 封禁，以实测可用为准）：
- 板块行情 + 成分股：新浪（stock_sector_spot / stock_sector_detail）
- 龙虎榜：新浪（stock_lhb_detail_daily_sina）
- 资金流向（行业/概念）：东财即时（stock_fund_flow_industry / stock_fund_flow_concept）
- 北向资金：东财（stock_hsgt_fund_flow_summary_em / stock_hsgt_hist_em）
  注：2024-08 起盘中实时数据停发，仅收盘后有日度数据。

全部接口按需拉取 + 内存 TTL 缓存，失败抛 RuntimeError（中文原因），由 API 层转 400。
"""
import math
import time

from app.data import calendar
from app.utils.logger import get_logger

logger = get_logger("data.market_extra")

_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, ttl: float, fetcher):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    data = fetcher()
    _cache[key] = (time.monotonic(), data)
    return data


def _ak():
    import akshare as ak

    return ak


def _f(value, default: float = 0.0) -> float:
    """数值清洗：非数值/NaN/Inf → default（NaN 会导致 FastAPI JSON 500）。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(v) or math.isinf(v):
        return default
    return v


def _s(value) -> str:
    """字符串清洗：None/NaN → 空串。"""
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return ""
        return str(value)
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------- 板块成分股

def get_sector_detail(name: str) -> dict:
    """板块成分股列表（板块名称 → 新浪 label 映射，行情列表复用 data.sectors）。"""
    def fetch():
        from app.data.sectors import get_sectors

        label = None
        for row in get_sectors():
            if row["name"] == name:
                label = row["label"]
                break
        if label is None:
            raise ValueError(f"未找到板块「{name}」")
        df = _ak().stock_sector_detail(sector=label)
        rows = [
            {"code": _s(r.get("代码")), "name": _s(r.get("名称")),
             "price": _f(r.get("最新价")), "change_pct": _f(r.get("涨跌幅")),
             "change": _f(r.get("涨跌额")), "volume": _f(r.get("成交量")),
             "amount": _f(r.get("成交额"))}
            for _, r in df.iterrows()
        ]
        rows.sort(key=lambda x: -x["change_pct"])
        return {"name": name, "stocks": rows}

    return _cached(f"sector_detail_{name}", 120, fetch)


# ---------------------------------------------------------------- 龙虎榜

def get_lhb() -> dict:
    """最近交易日的龙虎榜（新浪源）。"""
    def fetch():
        ak = _ak()
        date = calendar.latest_trade_date()
        df = ak.stock_lhb_detail_daily_sina(date=date)
        rows = [
            {"code": _s(r.get("股票代码")), "name": _s(r.get("股票名称")),
             "close": _f(r.get("收盘价")), "change_pct": _f(r.get("对应值")),
             "volume_wan": _f(r.get("成交量")), "amount_wan": _f(r.get("成交额")),
             "reason": _s(r.get("指标"))}
            for _, r in df.iterrows()
        ]
        return {"date": date, "rows": rows}

    return _cached("lhb", 1800, fetch)


# ---------------------------------------------------------------- 资金流向

def get_fund_flow(kind: str = "industry") -> list[dict]:
    """资金流向排行（东财即时）：kind = industry 行业 / concept 概念，按净额降序。"""
    def fetch():
        ak = _ak()
        df = (ak.stock_fund_flow_industry(symbol="即时") if kind == "industry"
              else ak.stock_fund_flow_concept(symbol="即时"))
        rows = [
            {"name": _s(r.get("行业")), "change_pct": _f(r.get("行业-涨跌幅")),
             "inflow": _f(r.get("流入资金")), "outflow": _f(r.get("流出资金")),
             "net": _f(r.get("净额")), "count": int(_f(r.get("公司家数"))),
             "leader": _s(r.get("领涨股")), "leader_pct": _f(r.get("领涨股-涨跌幅"))}
            for _, r in df.iterrows()
        ]
        rows.sort(key=lambda x: -x["net"])
        return rows

    return _cached(f"fund_flow_{kind}", 300, fetch)


# ---------------------------------------------------------------- 北向资金

def get_northbound() -> dict:
    """北向资金：当日汇总（沪深股通）+ 近 30 日历史净买额。"""
    def fetch():
        ak = _ak()
        summary = ak.stock_hsgt_fund_flow_summary_em()
        summary_rows = []
        for _, r in summary.iterrows():
            if str(r.get("资金方向", "")) != "北向":
                continue
            summary_rows.append({
                "date": _s(r.get("交易日")), "board": _s(r.get("板块")),
                "net": _f(r.get("成交净买额")),
                "up": int(_f(r.get("上涨数"))), "flat": int(_f(r.get("持平数"))),
                "down": int(_f(r.get("下跌数"))),
                "index": _s(r.get("相关指数")), "index_pct": _f(r.get("指数涨跌幅")),
            })
        hist_df = ak.stock_hsgt_hist_em().tail(30)
        hist = [
            {"date": _s(r.get("日期")), "net": _f(r.get("当日成交净买额")),
             "buy": _f(r.get("买入成交额")), "sell": _f(r.get("卖出成交额")),
             "cum": _f(r.get("历史累计净买额"))}
            for _, r in hist_df.iterrows()
        ]
        return {"summary": summary_rows, "hist": hist}

    return _cached("northbound", 3600, fetch)
