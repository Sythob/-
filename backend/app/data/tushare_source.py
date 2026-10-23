"""tushare 数据源预留桩。

未来接入步骤：注册 tushare pro 获得 token → 按 DataSource 接口实现本类 →
在 .env 中设置 DATA_SOURCE=tushare 即可切换，其余代码零改动。
"""
import pandas as pd

from app.data.base import DataSource


class TushareSource(DataSource):
    name = "tushare"

    def _todo(self):
        raise NotImplementedError(
            "tushare 数据源尚未实现：在 .env 配置 TUSHARE_TOKEN 后，"
            "按 app/data/base.py 的 DataSource 接口实现本类即可启用"
        )

    def get_instruments(self) -> pd.DataFrame:
        return self._todo()

    def get_daily_bars(self, symbol: str, start: str = "", end: str = "") -> pd.DataFrame:
        return self._todo()

    def get_realtime_quotes(self, symbols: list[str] | None = None) -> pd.DataFrame:
        return self._todo()

    def get_index_quotes(self) -> pd.DataFrame:
        return self._todo()

    def get_news(self, symbol: str, limit: int = 20) -> pd.DataFrame:
        return self._todo()

    def get_trade_calendar(self) -> pd.DataFrame:
        return self._todo()
