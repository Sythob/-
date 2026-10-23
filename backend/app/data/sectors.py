"""行业板块行情：新浪行业板块（东财板块接口在本机不可用）。

TTL 缓存 5 分钟；返回按涨跌幅排序的板块列表。
"""
import threading
import time

from app.utils.logger import get_logger

logger = get_logger("data.sectors")

_cache: tuple[float, list[dict]] = (0.0, [])
_lock = threading.Lock()


def get_sectors(limit: int = 49) -> list[dict]:
    """新浪行业板块行情（label/名称/平均价格/涨跌幅/领涨股等）。"""
    global _cache
    with _lock:
        if _cache[1] and time.monotonic() - _cache[0] < 300:
            return _cache[1]
    try:
        import akshare as ak

        df = ak.stock_sector_spot(indicator="新浪行业")
        rows = []
        for _, r in df.iterrows():
            code = str(r.get("股票代码", "") or "")
            rows.append({
                "label": str(r.get("label", "")),
                "name": str(r.get("板块", "")),
                "company_count": int(r.get("公司家数", 0) or 0),
                "avg_price": float(r.get("平均价格", 0) or 0),
                "pct_change": float(r.get("涨跌幅", 0) or 0),
                "amount": float(r.get("总成交额", 0) or 0),
                "leader": str(r.get("股票名称", "") or r.get("领涨股", "")),
                "leader_code": code[2:] if len(code) >= 2 else code,
                "leader_pct": float(r.get("个股-涨跌幅", 0) or 0),
            })
        rows.sort(key=lambda x: x["pct_change"], reverse=True)
        result = rows[:limit]
    except Exception as exc:  # noqa: BLE001
        logger.warning("板块行情获取失败: %s", exc)
        result = []
    with _lock:
        _cache = (time.monotonic(), result)
    return result
