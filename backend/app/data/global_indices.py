"""全球指数行情：A股/港股/美股/全球四组聚合 + 按组 TTL 缓存。

本机网络下东财不可用，全部走新浪源：
- A股: stock_zh_index_spot_sina（全量一次拉取）
- 港股: stock_hk_index_spot_sina（全量一次拉取）
- 美股: hq.sinajs.cn 的 gb_ 实时接口（单请求，毫秒级）
- 全球: hq.sinajs.cn 的 znb_ 接口（需 Referer，GBK 编码；日经/法国CAC 在新浪已下线）

每组独立缓存：A股/港股盘中更新快（5 分钟），美股/全球（10 分钟）。
全部只读 + 容错：单组失败返回空列表与错误信息，不影响其他组。
"""
import threading
import time

import requests

from app.utils.logger import get_logger

logger = get_logger("data.global_indices")

# ---------------------------------------------------------------- 分组配置

A_INDICES = [
    ("sh000001", "上证指数"), ("sz399001", "深证成指"), ("sz399006", "创业板指"),
    ("sh000300", "沪深300"), ("sh000016", "上证50"), ("sh000688", "科创50"),
]

HK_INDICES = [
    ("HSI", "恒生指数"), ("HSCEI", "国企指数"), ("HSTECH", "恒生科技指数"),
]

US_INDICES = [
    ("gb_$dji", "道琼斯"), ("gb_inx", "标普500"), ("gb_ixic", "纳斯达克"),
]

GLOBAL_INDICES = [
    ("znb_UKX", "英国富时100"), ("znb_DAX", "德国DAX30"), ("znb_KOSPI", "韩国综合指数"),
    ("znb_N225", "日经225"), ("znb_FCHI", "法国CAC40"),
]

# 商品与汇率（新浪 hf_/fx_ 接口）：富时A50期货、原油、黄金、铜、美元人民币
FX_INDICES = [
    ("hf_CHA50CFD", "富时A50期货"), ("hf_CL", "纽约原油"), ("hf_GC", "纽约黄金"),
    ("hf_HG", "美铜"), ("fx_susdcny", "美元/人民币"),
]

TTL = {"a": 300, "hk": 300, "us": 600, "global": 600, "fx": 300}

_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def _cached(key: str, ttl: float, fetcher) -> dict:
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
    result = fetcher()
    with _cache_lock:
        _cache[key] = (time.monotonic(), result)
    return result


def _group_result(indices: list[dict], unavailable: list[str] | None = None) -> dict:
    return {"indices": indices, "unavailable": unavailable or []}


# ---------------------------------------------------------------- 各源抓取

def _fetch_a() -> dict:
    import akshare as ak

    df = ak.stock_zh_index_spot_sina()
    want = dict(A_INDICES)
    rows = []
    for _, r in df.iterrows():
        code = str(r.get("代码", ""))
        if code in want:
            rows.append({
                "symbol": code, "name": want[code],
                "last_price": float(r.get("最新价") or 0.0),
                "pct_change": float(r.get("涨跌幅") or 0.0),
                "change": float(r.get("涨跌额") or 0.0),
            })
    return _group_result(rows)


def _fetch_hk() -> dict:
    import akshare as ak

    df = ak.stock_hk_index_spot_sina()
    want = dict(HK_INDICES)
    rows = []
    for _, r in df.iterrows():
        code = str(r.get("代码", "")).removeprefix("hk")
        if code in want:
            rows.append({
                "symbol": code, "name": want[code],
                "last_price": float(r.get("最新价") or 0.0),
                "pct_change": float(r.get("涨跌幅") or 0.0),
                "change": float(r.get("涨跌额") or 0.0),
            })
    return _group_result(rows)


def parse_us_text(text: str, mapping: dict[str, str]) -> dict:
    """解析 hq.sinajs.cn gb_ 返回文本（GBK 已解码）。

    格式: var hq_str_gb_$dji="道琼斯,50906.05,-0.86,2026-10-01 04:48:43,...";
    字段: [0]名称 [1]最新价 [2]涨跌幅(百分数) [3]行情时间 ...
    """
    rows, unavailable = [], []
    for line in text.strip().splitlines():
        if '="' not in line:
            continue
        code_part, payload = line.split('="', 1)
        code = code_part.split("hq_str_")[-1].strip()
        payload = payload.rstrip().rstrip(';').rstrip('"')
        if code not in mapping:
            continue
        fields = payload.split(",")
        if len(fields) < 3 or not fields[1]:
            unavailable.append(mapping[code])
            continue
        try:
            price, pct = float(fields[1]), float(fields[2])
        except ValueError:
            unavailable.append(mapping[code])
            continue
        trade_date = fields[3].split(" ")[0] if len(fields) > 3 else ""
        change = float(fields[4]) if len(fields) > 4 else 0.0
        rows.append({
            "symbol": code, "name": mapping[code],
            "last_price": price, "pct_change": pct, "trade_date": trade_date,
            "change": change,
        })
    return _group_result(rows, unavailable)


def _fetch_us() -> dict:
    """美股三大指数：新浪 gb_ 实时接口（单请求，毫秒级）。"""
    url = "https://hq.sinajs.cn/list=" + ",".join(c for c, _ in US_INDICES)
    resp = requests.get(
        url,
        headers={"Referer": "https://finance.sina.com.cn",
                 "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        timeout=10,
    )
    resp.raise_for_status()
    return parse_us_text(resp.content.decode("gbk", errors="replace"), dict(US_INDICES))


def parse_znb_text(text: str, mapping: dict[str, str]) -> dict:
    """解析 hq.sinajs.cn znb_ 返回文本（GBK 已解码）。

    格式: var hq_str_znb_UKX="英国富时100,10488.21,-117.79,-1.11,...";
    字段: [0]名称 [1]最新价 [2]涨跌额 [3]涨跌幅(百分数) [4]日期 [5]时间戳 ...
    空 payload（如 znb_N225=""）表示该指数已下线。
    """
    rows, unavailable = [], []
    seen = set()
    for line in text.strip().splitlines():
        if '="' not in line:
            continue
        code_part, payload = line.split('="', 1)
        code = code_part.split("hq_str_")[-1].strip()
        payload = payload.rstrip().rstrip(';').rstrip('"')
        if code not in mapping:
            continue
        fields = payload.split(",")
        if len(fields) < 4 or not fields[1]:
            unavailable.append(mapping[code])
            continue
        try:
            price, pct = float(fields[1]), float(fields[3])
            change = float(fields[2]) if len(fields) > 2 else 0.0
        except ValueError:
            unavailable.append(mapping[code])
            continue
        seen.add(code)
        rows.append({
            "symbol": code, "name": mapping[code],
            "last_price": price, "pct_change": pct, "change": change,
        })
    return _group_result(rows, unavailable)


def _fetch_global() -> dict:
    url = "https://hq.sinajs.cn/list=" + ",".join(c for c, _ in GLOBAL_INDICES)
    resp = requests.get(
        url,
        headers={"Referer": "https://finance.sina.com.cn",
                 "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        timeout=10,
    )
    resp.raise_for_status()
    return parse_znb_text(resp.content.decode("gbk", errors="replace"), dict(GLOBAL_INDICES))


def _parse_hf_line(code: str, fields: list[str], mapping: dict[str, str]) -> dict:
    """hf_ 期货行解析。

    格式: 最新价,_,买价,卖价,最高,最低,时间,昨结算,今开,...,日期,名称,...
    涨跌幅 = (最新/昨结算 - 1) × 100。
    """
    try:
        latest, settle = float(fields[0]), float(fields[7])
    except (ValueError, IndexError):
        return _group_result([], [mapping[code]])
    if settle <= 0:
        settle = latest
    return _group_result([{
        "symbol": code, "name": mapping[code],
        "last_price": latest,
        "pct_change": round((latest / settle - 1) * 100, 2),
        "change": round(latest - settle, 3),
        "trade_date": fields[12] if len(fields) > 12 else "",
    }])


def _parse_fx_line(code: str, fields: list[str], mapping: dict[str, str]) -> dict:
    """fx_ 汇率行解析。

    格式: 时间,买价,卖价,...,昨收,...,名称,...,日期
    涨跌幅 = (买价/昨收 - 1) × 100。
    """
    try:
        latest, prev = float(fields[1]), float(fields[5])
    except (ValueError, IndexError):
        return _group_result([], [mapping[code]])
    if prev <= 0:
        prev = latest
    return _group_result([{
        "symbol": code, "name": mapping[code],
        "last_price": latest,
        "pct_change": round((latest / prev - 1) * 100, 4),
        "change": round(latest - prev, 4),
        "trade_date": fields[-1] if len(fields) > 13 else "",
    }])


def _fetch_fx() -> dict:
    """商品与汇率：新浪 hf_/fx_ 接口（单请求）。"""
    url = "https://hq.sinajs.cn/list=" + ",".join(c for c, _ in FX_INDICES)
    resp = requests.get(
        url,
        headers={"Referer": "https://finance.sina.com.cn",
                 "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        timeout=10,
    )
    resp.raise_for_status()
    text = resp.content.decode("gbk", errors="replace")
    mapping = dict(FX_INDICES)
    indices: list[dict] = []
    unavailable: list[str] = []
    for line in text.strip().splitlines():
        if '="' not in line:
            continue
        code_part, payload = line.split('="', 1)
        code = code_part.split("hq_str_")[-1].strip()
        if code not in mapping:
            continue
        fields = payload.rstrip().rstrip(';').rstrip('"').split(",")
        if not fields or not fields[0]:
            unavailable.append(mapping[code])
            continue
        result = _parse_fx_line(code, fields, mapping) if code.startswith("fx_") \
            else _parse_hf_line(code, fields, mapping)
        indices.extend(result["indices"])
        unavailable.extend(result["unavailable"])
    return _group_result(indices, unavailable)


# ---------------------------------------------------------------- 对外接口

def get_grouped_indices() -> dict:
    """返回 {groups: [{key, name, indices, unavailable?, error?}]}，全组容错。"""
    groups = []
    for key, name, fetcher in (
        ("a", "A股", _fetch_a),
        ("hk", "港股", _fetch_hk),
        ("us", "美股", _fetch_us),
        ("global", "全球", _fetch_global),
        ("fx", "商品汇率", _fetch_fx),
    ):
        try:
            result = _cached(key, TTL[key], fetcher)
            groups.append({"key": key, "name": name, **result})
        except Exception as exc:  # noqa: BLE001 单组失败不影响其他组
            logger.warning("指数组 %s 获取失败: %s", name, exc)
            groups.append({
                "key": key, "name": name, "indices": [],
                "unavailable": [], "error": str(exc)[:100],
            })
    return {"groups": groups}
