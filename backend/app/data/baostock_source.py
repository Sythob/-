"""baostock 兜底数据源：免费免注册，日线/60分钟线/指数。

铁律：只做最后一道兜底（腾讯/新浪/东财全部失败时启用）。
- 不复权（原始价）返回，与系统"原始价+因子"存储一致；
- 当日数据收盘后（约 17:30 后）才更新，兜底场景可接受；
- baostock 为全局会话式连接：进程生命周期内登录一次复用。
"""
import threading

import pandas as pd

from app.data.base import BAR_COLUMNS
from app.utils.logger import get_logger

logger = get_logger("data.baostock")

_lock = threading.Lock()
_logged_in = False

_DAILY_FIELDS = "date,open,high,low,close,volume,amount,turn"
_HOUR_FIELDS = "date,time,open,high,low,close,volume,amount"


def _bs_code(symbol: str) -> str:
    """600519 → sh.600519；指数 000300 → sh.000300。"""
    prefix = "sh" if symbol.startswith(("6", "9", "5", "0")) else "sz"
    return f"{prefix}.{symbol}"


def _login() -> None:
    global _logged_in
    with _lock:
        if not _logged_in:
            import baostock as bs

            lg = bs.login()
            if lg.error_code != "0":
                raise RuntimeError(f"baostock 登录失败: {lg.error_msg}")
            _logged_in = True


def _query(code: str, fields: str, start: str, end: str, frequency: str,
           adjustflag: str = "3") -> pd.DataFrame:
    """执行查询并转 DataFrame（cells 均为字符串，数值列由调用方转换）。"""
    import baostock as bs

    _login()
    # 注意：baostock 日期格式要求 YYYY-MM-DD（带横杠），与 akshare 相反
    rs = bs.query_history_k_data_plus(
        code, fields,
        start_date=start, end_date=end,
        frequency=frequency, adjustflag=adjustflag,
    )
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    if rs.error_code != "0":
        raise RuntimeError(f"baostock 查询失败: {rs.error_msg}")
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows, columns=fields.split(","))


def get_daily_bars(symbol: str, start: str = "", end: str = "") -> pd.DataFrame:
    """日线兜底（不复权，原始价）。失败抛异常由上层回退处理。"""
    df = _query(_bs_code(symbol), _DAILY_FIELDS, start, end, "d")
    if df.empty:
        return pd.DataFrame(columns=BAR_COLUMNS)
    out = df.rename(columns={"date": "trade_date", "turn": "turnover_rate"})
    for col in ("open", "high", "low", "close", "volume", "amount", "turnover_rate"):
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
    out["pct_change"] = (out["close"].pct_change() * 100).fillna(0.0).round(2)
    out["adj_factor"] = 1.0
    out["trade_date"] = out["trade_date"].astype(str)
    return out[BAR_COLUMNS].reset_index(drop=True)


def get_hour_bars(symbol: str) -> pd.DataFrame:
    """60 分钟线兜底（baostock 仅保留近一年数据）。返回含 dt 列（YYYY-MM-DD HH:MM:SS）。"""
    df = _query(_bs_code(symbol), _HOUR_FIELDS, "20240101", "", "60")
    if df.empty:
        return pd.DataFrame(columns=["dt", "open", "high", "low", "close", "volume"])
    out = pd.DataFrame({
        "dt": df["date"] + " " + df["time"] + ":00",
        "open": pd.to_numeric(df["open"], errors="coerce").fillna(0.0),
        "high": pd.to_numeric(df["high"], errors="coerce").fillna(0.0),
        "low": pd.to_numeric(df["low"], errors="coerce").fillna(0.0),
        "close": pd.to_numeric(df["close"], errors="coerce").fillna(0.0),
        "volume": pd.to_numeric(df["volume"], errors="coerce").fillna(0.0),
    })
    return out.reset_index(drop=True)
