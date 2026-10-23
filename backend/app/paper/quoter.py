"""行情轮询：akshare 全市场快照一次拉取，本地过滤。

限制（文档明示）：数据约延迟 30~60 秒、非 tick 级、爬虫接口可能限流。
因此日线/小时级策略模拟完全可用；分钟级高频模拟不承诺精度。
"""
from app.data.base import get_source
from app.utils.logger import get_logger

logger = get_logger("paper.quoter")


def fetch_quotes(symbols: list[str]) -> dict[str, dict]:
    """返回 {symbol: {last_price, pct_change, open, high, low, volume, amount, ...}}。

    网络失败返回空 dict（调用方跳过本轮，不影响状态）。
    """
    try:
        df = get_source().get_realtime_quotes(symbols)
    except Exception as exc:  # noqa: BLE001 轮询必须容错
        logger.warning("行情轮询失败: %s", exc)
        return {}
    quotes = {}
    for _, row in df.iterrows():
        sym = str(row.get("symbol", ""))
        quotes[sym] = {
            "last_price": float(row.get("last_price") or 0.0),
            "pct_change": float(row.get("pct_change") or 0.0),
            "open": float(row.get("open") or 0.0),
            "high": float(row.get("high") or 0.0),
            "low": float(row.get("low") or 0.0),
            "volume": float(row.get("volume") or 0.0),
            "amount": float(row.get("amount") or 0.0),
        }
    return quotes
