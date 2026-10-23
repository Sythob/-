"""akshare 数据源实现。

注意：akshare 是爬虫型接口，可能限流/改版。所有网络请求带重试退避与请求间隔；
单标的失败只记日志不中断整批。各接口口径与已知限制见 docs/data-notes.md。

标准化纯函数（_normalize_*）不依赖网络，测试可直接对拍。
"""
import threading
import time
from datetime import datetime

import httpx
import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from app.data.base import BAR_COLUMNS, DataSource
from app.data.symbols import sina_index_symbol, sina_symbol
from app.utils.logger import get_logger

logger = get_logger("data.akshare")

REQUEST_INTERVAL = 0.6  # 请求间休息（秒），礼貌限速

_AK = None
_patched = False
_spot_cache: dict[str, tuple[float, "pd.DataFrame"]] = {}  # 实时快照 TTL 缓存
_spot_cache_lock = threading.Lock()


def _patch_eastmoney_hosts() -> None:
    """东财编号子域名（82.push2）在部分网络下不可达，出站时重写到主域名。

    akshare 硬编码编号子域名做 CDN 分片；主域名（push2.eastmoney.com）提供同一套 API。
    仅重写 URL，不改动任何请求行为。失败静默（补丁是尽力而为的兼容层）。
    """
    global _patched
    if _patched:
        return
    _patched = True
    try:
        import requests

        BROWSER_UA = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        )

        def _rewrite(url):
            if isinstance(url, str) and "82.push2.eastmoney.com" in url:
                return url.replace("82.push2.eastmoney.com", "push2.eastmoney.com")
            return url

        def _with_browser_ua(kwargs):
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault("User-Agent", BROWSER_UA)
            kwargs = dict(kwargs)
            kwargs["headers"] = headers
            return kwargs

        # akshare 有的接口用 requests.get，有的用 Session.get，两者都覆盖
        original_get = requests.get

        def _get(url, *args, **kwargs):
            return original_get(_rewrite(url), *args, **_with_browser_ua(kwargs))

        original_session_get = requests.Session.get

        def _session_get(self, url, *args, **kwargs):
            return original_session_get(self, _rewrite(url), *args, **_with_browser_ua(kwargs))

        requests.get = _get
        requests.Session.get = _session_get
        logger.info("已应用东财网络兼容补丁（域名重写 + 浏览器 UA + IPv4 优先）")
    except Exception as exc:  # noqa: BLE001
        logger.warning("东财域名补丁应用失败（不影响其他数据源）: %s", exc)


def _ak():
    """懒加载 akshare（导入较慢，且保证纯函数测试不依赖其安装）。"""
    global _AK
    if _AK is None:
        from app.utils.netcompat import patch_ipv4_preference

        patch_ipv4_preference()
        import akshare

        _AK = akshare
        _patch_eastmoney_hosts()
    return _AK


def _retry_call(fn, *args, **kwargs):
    """网络调用包装：请求前限速，失败指数退避重试（默认 3 次）。"""
    attempts = kwargs.pop("attempts", 3)
    time.sleep(REQUEST_INTERVAL)

    @retry(stop=stop_after_attempt(attempts), wait=wait_exponential(multiplier=1, min=1, max=10), reraise=True)
    def _inner():
        return fn(*args, **kwargs)

    return _inner()


# ---------------------------------------------------------------- 标准化（纯函数）

_HIST_RENAME = {
    "日期": "trade_date",
    "开盘": "open",
    "最高": "high",
    "最低": "low",
    "收盘": "close",
    "成交量": "volume",
    "成交额": "amount",
    "涨跌幅": "pct_change",
    "换手率": "turnover_rate",
}

_SPOT_RENAME = {
    "代码": "symbol", "名称": "name", "最新价": "last_price", "涨跌幅": "pct_change",
    "涨跌额": "change", "成交量": "volume", "成交额": "amount", "振幅": "amplitude",
    "最高": "high", "最低": "low", "今开": "open", "昨收": "pre_close",
    "量比": "volume_ratio", "换手率": "turnover_rate",
    "市盈率-动态": "pe", "市净率": "pb", "总市值": "total_mv", "流通市值": "float_mv",
}

_NEWS_RENAME = {
    "新闻标题": "title", "新闻内容": "content",
    "发布时间": "pub_time", "文章来源": "source", "新闻链接": "url",
}


def _normalize_hist(df: pd.DataFrame, factor_map: dict | None = None) -> pd.DataFrame:
    """stock_zh_a_hist / index_zh_a_hist 输出 → BAR_COLUMNS 标准列。

    成交量单位 手 → 股（×100）；factor_map 为空时 adj_factor 置 1。
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=BAR_COLUMNS)
    out = df.rename(columns=_HIST_RENAME)
    out["trade_date"] = out["trade_date"].astype(str).str[:10]
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0.0) * 100.0
    for col in ("open", "high", "low", "close", "amount", "pct_change", "turnover_rate"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
        else:
            out[col] = 0.0
    out["adj_factor"] = out["trade_date"].map(factor_map or {}).fillna(1.0)
    out = out.dropna(subset=["trade_date"])
    return out[BAR_COLUMNS].reset_index(drop=True)


def _normalize_spot(df: pd.DataFrame, symbols: list[str] | None = None) -> pd.DataFrame:
    """实时快照 → 标准列（symbol/name/last_price/pct_change/volume 等），成交量 手→股。"""
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.rename(columns={k: v for k, v in _SPOT_RENAME.items() if k in df.columns})
    if "volume" in out.columns:
        out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0.0) * 100.0
    if symbols:
        out = out[out["symbol"].astype(str).isin(symbols)]
    return out.reset_index(drop=True)


def _normalize_news(df: pd.DataFrame, symbol: str, limit: int = 20) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["symbol", "source", "title", "content", "url", "pub_time"])
    out = df.rename(columns=_NEWS_RENAME)
    keep = [c for c in ("title", "content", "pub_time", "source", "url") if c in out.columns]
    out = out[keep].head(limit)
    out["symbol"] = symbol
    return out


# ---- 新浪回退源的标准化（注意：新浪成交量单位已是「股」，与东财的「手」不同）----

_SINA_DAILY_RENAME = {
    "date": "trade_date", "open": "open", "high": "high", "low": "low",
    "close": "close", "volume": "volume", "amount": "amount", "turnover": "turnover_rate",
}

_SINA_SPOT_RENAME = {
    "代码": "symbol", "名称": "name", "最新价": "last_price", "涨跌幅": "pct_change",
    "涨跌额": "change", "成交量": "volume", "成交额": "amount",
    "最高": "high", "最低": "low", "今开": "open", "昨收": "pre_close",
}


_TX_RENAME = {
    "date": "trade_date", "open": "open", "close": "close", "high": "high",
    "low": "low", "volume": "volume", "turnover": "turnover_rate", "amount": "amount",
}


def _normalize_tx_daily(df: pd.DataFrame, factor_map: dict | None = None) -> pd.DataFrame:
    """腾讯日线 → BAR_COLUMNS。注意：volume 单位为手（×100），turnover 为小数（×100）。"""
    if df is None or df.empty:
        return pd.DataFrame(columns=BAR_COLUMNS)
    out = df.rename(columns=_TX_RENAME)
    if "trade_date" not in out.columns:
        return pd.DataFrame(columns=BAR_COLUMNS)
    out["trade_date"] = pd.to_datetime(out["trade_date"]).dt.strftime("%Y-%m-%d")
    for col in ("open", "high", "low", "close", "amount"):
        if col not in out.columns:
            out[col] = 0.0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
    if "volume" not in out.columns:
        out["volume"] = 0.0
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0.0) * 100.0  # 手→股
    if "turnover_rate" not in out.columns:
        out["turnover_rate"] = 0.0
    # 小数→百分数，四舍五入到 2 位避免浮点误差
    out["turnover_rate"] = (pd.to_numeric(out["turnover_rate"], errors="coerce").fillna(0.0) * 100.0).round(2)
    out["pct_change"] = (out["close"].pct_change() * 100).round(4).fillna(0.0)
    out["adj_factor"] = out["trade_date"].map(factor_map or {}).fillna(1.0)
    out = out.dropna(subset=["trade_date"])
    return out[BAR_COLUMNS].reset_index(drop=True)


def _normalize_minute(df: pd.DataFrame) -> pd.DataFrame:
    """新浪分钟线 → 标准列（dt/open/high/low/close/volume，成交量已是股）。"""
    if df is None or df.empty:
        return pd.DataFrame(columns=["dt", "open", "high", "low", "close", "volume"])
    out = df.rename(columns={"day": "dt"})
    out["dt"] = pd.to_datetime(out["dt"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    for col in ("open", "high", "low", "close", "volume"):
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
    return out[["dt", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def _normalize_sina_daily(df: pd.DataFrame, factor_map: dict | None = None) -> pd.DataFrame:
    """新浪日线（stock_zh_a_daily / fund_etf_hist_sina / stock_zh_index_daily）→ BAR_COLUMNS。

    成交量不 ×100；涨跌幅由收盘价推算；换手率缺失时补 0（ETF/指数源无此列）。
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=BAR_COLUMNS)
    out = df.rename(columns=_SINA_DAILY_RENAME)
    if "trade_date" not in out.columns:
        return pd.DataFrame(columns=BAR_COLUMNS)  # 数据结构不符：返回空
    out["trade_date"] = pd.to_datetime(out["trade_date"]).dt.strftime("%Y-%m-%d")
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col not in out.columns:  # 指数源无 amount 列等
            out[col] = 0.0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
    if "turnover_rate" not in out.columns:
        out["turnover_rate"] = 0.0
    else:
        # 新浪 turnover 为小数（0.0086=0.86%），东财为百分数——统一为百分数
        out["turnover_rate"] = pd.to_numeric(out["turnover_rate"], errors="coerce").fillna(0.0) * 100.0
    out["pct_change"] = (out["close"].pct_change() * 100).round(4).fillna(0.0)
    out["adj_factor"] = out["trade_date"].map(factor_map or {}).fillna(1.0)
    out = out.dropna(subset=["trade_date"])
    return out[BAR_COLUMNS].reset_index(drop=True)


def _normalize_sina_spot(df: pd.DataFrame, symbols: list[str] | None = None) -> pd.DataFrame:
    """新浪全市场快照（stock_zh_a_spot）→ 标准列。代码剥离 sh/sz 前缀；成交量不 ×100。"""
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.rename(columns=_SINA_SPOT_RENAME)
    out["symbol"] = out["symbol"].astype(str).str.replace(r"^[a-z]{2}", "", regex=True)
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0.0)
    if symbols:
        out = out[out["symbol"].isin(symbols)]
    return out.reset_index(drop=True)


# ---------------------------------------------------------------- 实现

class AkshareSource(DataSource):
    name = "akshare"

    def get_instruments(self) -> pd.DataFrame:
        """全市场 A股 代码+名称（一次请求）。"""
        df = _retry_call(_ak().stock_info_a_code_name)
        out = pd.DataFrame({
            "symbol": df["code"].astype(str),
            "name": df["name"].astype(str),
            "exchange": df["code"].astype(str).map(_exchange_of),
            "type": "stock",
            "board": df["code"].astype(str).map(_board_of),
            "status": "active",
            "list_date": "",
            "is_st": df["name"].astype(str).str.contains("ST", na=False).astype(int),
        })
        return out

    def get_daily_bars(self, symbol: str, start: str = "", end: str = "") -> pd.DataFrame:
        """日线：腾讯主源（股票/ETF 全支持、当日数据无滞后、hfq 因子稳定推导）。

        回退链：腾讯 → 东财 → 新浪。
        """
        start_date = (start or "19900101").replace("-", "")
        end_date = (end or datetime.now().strftime("%Y%m%d")).replace("-", "")
        try:
            df = _retry_call(
                _ak().stock_zh_a_hist_tx, symbol=sina_symbol(symbol),
                start_date=start_date, end_date=end_date, adjust="",
            )
            factor_map = self._fetch_tx_factor_map(symbol, start_date, end_date)
            return _normalize_tx_daily(df, factor_map)
        except Exception as tx_exc:
            logger.warning("腾讯日线失败，回退原链路 %s: %s", symbol, tx_exc)
            factor_map = self._fetch_factor_map(symbol)
            try:
                df = _retry_call(
                    _ak().stock_zh_a_hist, symbol=symbol, period="daily",
                    start_date=start_date, end_date=end_date, adjust="",
                )
                return _normalize_hist(df, factor_map)
            except Exception as exc:
                # 东财被封/限流时回退新浪：股票用 stock_zh_a_daily，ETF 用 fund_etf_hist_sina
                logger.warning("东财日线失败，回退新浪源 %s: %s", symbol, exc)
                try:
                    if _is_etf_symbol(symbol):
                        df = _retry_call(_ak().fund_etf_hist_sina, symbol=sina_symbol(symbol))
                    else:
                        df = _retry_call(_ak().stock_zh_a_daily, symbol=sina_symbol(symbol), adjust="")
                except Exception as fallback_exc:
                    logger.warning("新浪日线回退也失败 %s: %s", symbol, fallback_exc)
                    raise fallback_exc
                out = _normalize_sina_daily(df, factor_map)
                if start:
                    out = out[out["trade_date"] >= start]
                if end:
                    out = out[out["trade_date"] <= end]
                return out.reset_index(drop=True)

    def get_index_daily_bars(self, symbol: str, start: str = "", end: str = "") -> pd.DataFrame:
        """指数日线（无复权，adj_factor=1）。仅用于白名单指数。

        回退链：腾讯 → 东财 → 新浪。
        """
        start_date = (start or "19900101").replace("-", "")
        end_date = (end or datetime.now().strftime("%Y%m%d")).replace("-", "")
        try:
            df = _retry_call(_ak().stock_zh_index_daily_tx, symbol=sina_index_symbol(symbol))
            out = _normalize_tx_daily(df, {})
            if start:
                out = out[out["trade_date"] >= start]
            if end:
                out = out[out["trade_date"] <= end]
            return out.reset_index(drop=True)
        except Exception as tx_exc:
            logger.warning("腾讯指数日线失败，回退原链路 %s: %s", symbol, tx_exc)
            try:
                df = _retry_call(
                    _ak().index_zh_a_hist, symbol=symbol, period="daily",
                    start_date=start_date, end_date=end_date,
                )
                return _normalize_hist(df, {})
            except Exception as exc:
                logger.warning("东财指数日线失败，回退新浪源 %s: %s", symbol, exc)
                df = _retry_call(_ak().stock_zh_index_daily, symbol=sina_index_symbol(symbol))
                out = _normalize_sina_daily(df, {})
                if start:
                    out = out[out["trade_date"] >= start]
                if end:
                    out = out[out["trade_date"] <= end]
                return out.reset_index(drop=True)

    def get_hour_bars(self, symbol: str) -> pd.DataFrame:
        """60 分钟线（新浪源，仅保留近端数据，需每日积累）。"""
        df = _retry_call(_ak().stock_zh_a_minute, symbol=sina_symbol(symbol), period="60")
        return _normalize_minute(df)

    def get_today_minutes(self, symbol: str) -> pd.DataFrame:
        """最近交易日分时数据（1 分钟粒度，新浪源；周末/节假日显示上一交易日）。

        返回列：time(HH:MM)/price/volume/avg（均价线 = 累计成交额/累计成交量）。
        盘中实时、盘后保留当日完整数据；失败返回空。
        """
        from app.data.calendar import latest_trade_date

        try:
            df = _retry_call(_ak().stock_zh_a_minute, symbol=sina_symbol(symbol), period="1")
        except Exception as exc:
            logger.warning("分时数据获取失败 %s: %s", symbol, exc)
            return pd.DataFrame(columns=["time", "price", "volume", "avg"])
        if df is None or df.empty:
            return pd.DataFrame(columns=["time", "price", "volume", "avg"])
        trade_day = latest_trade_date()
        out = df[df["day"].astype(str).str.startswith(trade_day)].copy()
        if out.empty:
            return pd.DataFrame(columns=["time", "price", "volume", "avg"])
        out["time"] = pd.to_datetime(out["day"]).dt.strftime("%H:%M")
        out["price"] = pd.to_numeric(out["close"], errors="coerce").fillna(0.0)
        out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0.0)
        out["amount"] = pd.to_numeric(out["amount"], errors="coerce").fillna(0.0)
        cum_vol = out["volume"].cumsum()
        out["avg"] = out["amount"].cumsum() / cum_vol.replace(0, 1)  # 真实均价线
        return out[["time", "price", "volume", "avg"]].reset_index(drop=True)

    def get_realtime_quotes(self, symbols: list[str] | None = None) -> pd.DataFrame:
        """全市场快照一次拉取，本地过滤（远比逐只查询高效）。

        - 30 秒 TTL 缓存：模拟盘每 30 秒轮询会持续刷新缓存，行情页直接读缓存秒回
        - 东财失败只试 1 次（长期被封时不做无谓重试），立即回退新浪全市场快照（约 20~30 秒）
        """
        cache_key = "all" if not symbols else ",".join(sorted(symbols))
        with _spot_cache_lock:
            hit = _spot_cache.get(cache_key)
            if hit and time.monotonic() - hit[0] < 30:
                return hit[1]
        try:
            df = _retry_call(_ak().stock_zh_a_spot_em, attempts=1)
            result = _normalize_spot(df, symbols)
        except Exception as exc:
            logger.warning("东财快照失败，回退新浪源: %s", exc)
            df = _retry_call(_ak().stock_zh_a_spot)
            result = _normalize_sina_spot(df, symbols)
        with _spot_cache_lock:
            _spot_cache[cache_key] = (time.monotonic(), result)
        return result

    def get_turnover_batch(self, symbols: list[str]) -> dict[str, float]:
        """腾讯批量报价取换手率（%）：一次请求 60 只，返回 {代码: 换手率}。

        用于打板候选池：避免为每只候选逐股补日线的慢速路径。
        """
        from app.data.symbols import sina_symbol

        result: dict[str, float] = {}
        for i in range(0, len(symbols), 60):
            chunk = symbols[i:i + 60]
            codes = ",".join(sina_symbol(s) for s in chunk)
            try:
                resp = httpx.get(f"http://qt.gtimg.cn/q={codes}", timeout=10)
                resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                logger.warning("腾讯换手率批量获取失败: %s", exc)
                continue
            for line in resp.text.split(";"):
                if '"' not in line:
                    continue
                try:
                    fields = line.split('"')[1].split("~")
                    symbol = fields[2]
                    turnover = float(fields[38])
                except (IndexError, ValueError):
                    continue
                if symbol:
                    result[symbol] = turnover
        return result

    def get_index_quotes(self) -> pd.DataFrame:
        """主要指数快照（东财失败回退新浪）。"""
        try:
            df = _retry_call(_ak().stock_zh_index_spot_em, symbol="沪深重要指数")
        except Exception as exc:
            logger.warning("东财指数快照失败，回退新浪源: %s", exc)
            df = _retry_call(_ak().stock_zh_index_spot_sina)
        return _normalize_spot(df)

    def get_news(self, symbol: str, limit: int = 20) -> pd.DataFrame:
        df = _retry_call(_ak().stock_news_em, symbol=symbol)
        return _normalize_news(df, symbol, limit)

    def get_trade_calendar(self) -> pd.DataFrame:
        df = _retry_call(_ak().tool_trade_date_hist_sina)
        dates = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
        return pd.DataFrame({"trade_date": dates, "is_open": 1})

    def _fetch_tx_factor_map(self, symbol: str, start_date: str, end_date: str) -> dict[str, float]:
        """腾讯复权因子：hfq 价 ÷ 原始价（稳定推导，无新浪因子毛刺）。"""
        try:
            raw = _retry_call(
                _ak().stock_zh_a_hist_tx, symbol=sina_symbol(symbol),
                start_date=start_date, end_date=end_date, adjust="",
            )
            hfq = _retry_call(
                _ak().stock_zh_a_hist_tx, symbol=sina_symbol(symbol),
                start_date=start_date, end_date=end_date, adjust="hfq",
            )
            if raw is None or raw.empty or hfq is None or hfq.empty:
                return {}
            raw_close = pd.to_numeric(raw["close"], errors="coerce")
            hfq_close = pd.to_numeric(hfq["close"], errors="coerce")
            factors = (hfq_close / raw_close.replace(0, 1)).fillna(1.0)
            factors = factors.clip(lower=0.1, upper=1000.0)  # 极端值防护
            from app.data.loader import sanitize_factors

            return dict(zip(pd.to_datetime(raw["date"]).dt.strftime("%Y-%m-%d"),
                            sanitize_factors(factors)))
        except Exception as exc:
            logger.warning("腾讯复权因子获取失败 %s: %s（按 adj_factor=1 处理）", symbol, exc)
            return {}

    def _fetch_factor_map(self, symbol: str) -> dict[str, float]:
        """新浪日线取 hfq 复权因子；失败返回空（adj_factor 置 1，行情仍可用）。"""
        try:
            df = _retry_call(_ak().stock_zh_a_daily, symbol=sina_symbol(symbol), adjust="hfq-factor")
            if df is None or df.empty or "hfq_factor" not in df.columns:
                return {}
            dates = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            factors = pd.to_numeric(df["hfq_factor"], errors="coerce").fillna(1.0)
            from app.data.loader import sanitize_factors

            return dict(zip(dates, sanitize_factors(factors)))
        except Exception as exc:
            logger.warning("获取复权因子失败 %s: %s（按 adj_factor=1 处理）", symbol, exc)
            return {}


def _is_etf_symbol(symbol: str) -> bool:
    """ETF/LOF 代码前缀：5xxxxx（沪 ETF）、15/16/18xxxx（深 ETF/LOF）。"""
    return symbol.startswith(("5", "15", "16", "18"))


def _exchange_of(symbol: str) -> str:
    from app.data.symbols import exchange_of

    return exchange_of(symbol)


def _board_of(symbol: str) -> str:
    from app.data.symbols import board_of

    return board_of(symbol)
