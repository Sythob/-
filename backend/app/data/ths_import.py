"""同花顺远航版导出文件导入：持仓/交割单 Excel → 模拟账户。

同花顺无官方接口，走「导出文件上传解析」路线。解析要点：
- 导出文件可能是真 xlsx/xls，也可能是 HTML 表格伪装成 .xls —— 多引擎链式兜底；
- 列名随版本变动 —— 表头关键词模糊匹配，不做硬编码列序；
- 所有单元格按字符串读取，数值做容错清洗（千分位逗号、全角字符等）。
"""
import io
import re
from datetime import datetime

import pandas as pd

from app.data import database
from app.data.models import PaperOrder, PaperPosition
from app.paper import account as account_service
from app.utils.logger import get_logger

logger = get_logger("data.ths_import")

# ---------------------------------------------------------------- 表头关键词

_POS_KEYS = {
    "symbol": ["证券代码", "股票代码", "代码"],
    "name": ["证券名称", "股票名称", "名称"],
    "volume": ["股票余额", "持仓数量", "当前持仓", "持仓", "可用余额", "数量"],
    "available": ["可用余额", "可用数量", "可用"],
    "cost": ["成本价", "摊薄成本", "持仓成本", "成本"],
}
_TRADE_KEYS = {
    "time": ["成交日期", "成交时间", "委托日期", "日期"],
    "symbol": ["证券代码", "股票代码", "代码"],
    "name": ["证券名称", "股票名称", "名称"],
    "direction": ["操作", "买卖标志", "业务名称", "方向", "买卖"],
    "price": ["成交均价", "成交价格", "成交价"],
    "volume": ["成交数量", "成交股数", "数量"],
    "fee": ["手续费", "佣金"],
}


def _read_table(content: bytes, filename: str) -> pd.DataFrame:
    """多引擎链式读取：真 Excel → HTML 伪装 xls → CSV/文本。全部返回无表头原始表。"""
    name = (filename or "").lower()
    buf = io.BytesIO(content)
    errors: list[str] = []

    # 1) 真 Excel（xlsx → openpyxl，xls → xlrd）
    engines = ["openpyxl", "xlrd"] if name.endswith(".xls") else ["openpyxl"]
    for engine in engines:
        try:
            df = pd.read_excel(buf, engine=engine, header=None, dtype=str)
            if not df.empty:
                return df
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{engine}: {type(exc).__name__}")
            buf.seek(0)

    # 2) HTML 表格伪装成 xls（同花顺老版本导出的常见格式）
    #    字节流无 charset 声明时 lxml 会按错误编码解码中文 → 自行解码后传 StringIO
    for enc in ("utf-8", "gbk"):
        try:
            text = content.decode(enc, errors="strict")
            tables = pd.read_html(io.StringIO(text), header=None)
            if tables:
                return tables[0].astype(str)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"html-{enc}: {type(exc).__name__}")

    # 3) CSV/纯文本兜底（优先 GBK，同花顺中文导出常见编码）
    for enc in ("gbk", "utf-8"):
        try:
            df = pd.read_csv(io.StringIO(content.decode(enc, errors="ignore")),
                             header=None, dtype=str, encoding_errors="ignore")
            if not df.empty:
                return df
        except Exception as exc:  # noqa: BLE001
            errors.append(f"csv-{enc}: {type(exc).__name__}")

    raise ValueError(f"无法识别文件格式（{'; '.join(errors[:3])}）")


def _find_header_row(df: pd.DataFrame, all_keys: list[str]) -> int:
    """在前 5 行里找表头行：命中已知关键词最多的那一行。"""
    best_row, best_score = -1, 0
    for idx in range(min(5, len(df))):
        cells = [str(df.iat[idx, c]) for c in range(df.shape[1])]
        score = sum(1 for cell in cells for key in all_keys if key in cell)
        if score > best_score:
            best_row, best_score = idx, score
    if best_score < 2:
        raise ValueError("未找到表头行，请确认文件是持仓/交割单导出文件")
    return best_row


def _find_col(headers: list[str], keys: list[str]) -> int:
    """表头关键词匹配：先精确，后包含。未命中返回 -1。"""
    for key in keys:
        for idx, h in enumerate(headers):
            if h == key:
                return idx
    for key in keys:
        for idx, h in enumerate(headers):
            if key in h:
                return idx
    return -1


def _num(value) -> float:
    """数值容错：去千分位/货币符号/空白，失败返回 0。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    s = str(value).replace(",", "").replace("¥", "").replace("￥", "").strip()
    s = s.replace("　", "").replace(" ", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _symbol(value) -> str:
    """提取 6 位代码（容错带交易所前缀/空格的情况）。"""
    digits = re.sub(r"\D", "", str(value))
    return digits[-6:] if len(digits) >= 6 else digits


def _parse_time(value) -> datetime:
    """成交时间解析，支持多种常见写法；失败返回 None（由调用方告警）。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip().replace("/", "-").replace(".", "-")
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _data_rows(df: pd.DataFrame, header_row: int) -> pd.DataFrame:
    """表头行之后的行；跳过全空行与合计/汇总行。"""
    data = df.iloc[header_row + 1:]
    mask = data.notna().any(axis=1) & ~data.apply(
        lambda row: any("合计" in str(v) or "汇总" in str(v) or "总计" in str(v)
                        for v in row), axis=1)
    return data[mask]


def _iter_records(df: pd.DataFrame, keys_map: dict[str, list[str]], *, pos: bool) -> list[dict]:
    header_row = _find_header_row(df, [k for keys in keys_map.values() for k in keys])
    headers = [str(df.iat[header_row, c]).strip() for c in range(df.shape[1])]
    cols = {field: _find_col(headers, keys) for field, keys in keys_map.items()}

    if cols["symbol"] < 0:
        raise ValueError("未找到「证券代码」列，请确认文件是持仓/交割单导出文件")

    records: list[dict] = []
    for _, row in _data_rows(df, header_row).iterrows():
        symbol = _symbol(row.iloc[cols["symbol"]])
        if len(symbol) != 6:
            continue  # 空行/无效代码直接跳过
        rec: dict = {"symbol": symbol, "name": ""}
        if cols["name"] >= 0:
            rec["name"] = str(row.iloc[cols["name"]]).strip()
        if pos:
            volume = _num(row.iloc[cols["volume"]] if cols["volume"] >= 0 else 0)
            available = _num(row.iloc[cols["available"]] if cols["available"] >= 0 else volume)
            cost = _num(row.iloc[cols["cost"]] if cols["cost"] >= 0 else 0)
            if volume <= 0:
                continue
            rec.update(volume=int(round(volume)), available=int(round(available)), cost=cost)
        else:
            price = _num(row.iloc[cols["price"]] if cols["price"] >= 0 else 0)
            volume = _num(row.iloc[cols["volume"]] if cols["volume"] >= 0 else 0)
            fee = _num(row.iloc[cols["fee"]] if cols["fee"] >= 0 else 0)
            direction = ""
            if cols["direction"] >= 0:
                d = str(row.iloc[cols["direction"]])
                direction = "buy" if "买" in d else ("sell" if "卖" in d else "")
            if direction not in ("buy", "sell") or price <= 0 or volume <= 0:
                continue
            rec.update(direction=direction, price=price, volume=int(round(volume)),
                       fee=fee, trade_time=_parse_time(row.iloc[cols["time"]] if cols["time"] >= 0 else None))
        records.append(rec)
    return records


def parse_positions(content: bytes, filename: str = "") -> list[dict]:
    """持仓文件 → [{symbol, name, volume, available, cost}]"""
    return _iter_records(_read_table(content, filename), _POS_KEYS, pos=True)


def parse_trades(content: bytes, filename: str = "") -> list[dict]:
    """交割单文件 → [{symbol, name, direction, price, volume, fee, trade_time}]"""
    return _iter_records(_read_table(content, filename), _TRADE_KEYS, pos=False)


# ---------------------------------------------------------------- 落库应用

def apply_positions(records: list[dict]) -> tuple[int, list[str]]:
    """持仓导入：镜像覆盖——模拟账户持仓 = 文件内容，现金保持不变。"""
    warnings: list[str] = []
    if not records:
        warnings.append("持仓文件中未解析到有效记录")
        return 0, warnings
    account = account_service.ensure_account()
    with database.get_session() as session:
        session.query(PaperPosition).filter(PaperPosition.account_id == account.id).delete()
        session.commit()
    for rec in records:
        available = min(rec["available"], rec["volume"])
        account_service.upsert_position(account.id, rec["symbol"], rec["volume"],
                                        available, rec["cost"])
    logger.info("同花顺持仓导入 %d 条（镜像覆盖）", len(records))
    return len(records), warnings


def apply_trades(records: list[dict]) -> tuple[int, list[str]]:
    """交割单导入：追加生成已成交委托记录（仅展示，不改持仓）。"""
    warnings: list[str] = []
    account = account_service.ensure_account()
    count = 0
    with database.get_session() as session:
        for rec in records:
            trade_time = rec.get("trade_time") or datetime.now()
            if rec.get("trade_time") is None:
                warnings.append(f"{rec['symbol']} 一条成交时间无法解析，已按当前时间记录")
            session.add(PaperOrder(
                account_id=account.id, symbol=rec["symbol"],
                direction=rec["direction"], order_type="market",
                price=rec["price"], volume=rec["volume"],
                filled_price=rec["price"], filled_volume=rec["volume"],
                status="filled", created_at=trade_time,
            ))
            count += 1
        session.commit()
    if not count:
        warnings.append("交割单文件中未解析到有效记录")
    logger.info("同花顺交割单导入 %d 条", count)
    return count, warnings
