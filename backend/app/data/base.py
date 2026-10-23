"""数据源抽象接口：上层只依赖此接口，akshare/tushare 等实现可插拔。

统一约定：
- 日期为 "YYYY-MM-DD" 文本
- 成交量为「股」（akshare 原始单位为手，实现层负责 ×100）
- 金额为「元」
"""
from abc import ABC, abstractmethod

import pandas as pd


def get_source() -> "DataSource":
    """数据源工厂：按 .env 的 DATA_SOURCE 配置返回实现（默认 akshare）。"""
    from app.config import get_settings

    source_name = get_settings().data_source
    if source_name == "tushare":
        from app.data.tushare_source import TushareSource

        return TushareSource()
    from app.data.akshare_source import AkshareSource

    return AkshareSource()

# 统一日线列名
BAR_COLUMNS = [
    "trade_date", "open", "high", "low", "close",
    "volume", "amount", "pct_change", "turnover_rate", "adj_factor",
]


class DataSource(ABC):
    name: str = "base"

    @abstractmethod
    def get_instruments(self) -> pd.DataFrame:
        """全部证券主表：symbol/name/exchange/type/board/status/list_date/is_st"""

    @abstractmethod
    def get_daily_bars(self, symbol: str, start: str = "", end: str = "") -> pd.DataFrame:
        """股票/ETF 日线（不复权原始价 + adj_factor），列为 BAR_COLUMNS"""

    @abstractmethod
    def get_realtime_quotes(self, symbols: list[str] | None = None) -> pd.DataFrame:
        """实时快照（全市场拉取后本地过滤），列含 symbol/name/last_price/pct_change 等"""

    @abstractmethod
    def get_index_quotes(self) -> pd.DataFrame:
        """主要指数快照"""

    @abstractmethod
    def get_news(self, symbol: str, limit: int = 20) -> pd.DataFrame:
        """个股/ETF 新闻：symbol/source/title/content/url/pub_time"""

    @abstractmethod
    def get_trade_calendar(self) -> pd.DataFrame:
        """交易日历：trade_date/is_open"""
