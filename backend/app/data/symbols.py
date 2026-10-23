"""证券代码工具：交易所/板块/新浪前缀推断（纯函数，无网络依赖）。"""


def exchange_of(symbol: str) -> str:
    """代码 → 交易所：sh(沪) / sz(深) / bj(北交所)。"""
    s = symbol.strip()
    if s.startswith(("6", "9", "5")):
        return "sh"
    if s.startswith(("4", "8")):
        return "bj"
    return "sz"


def board_of(symbol: str) -> str:
    """代码 → 板块。"""
    s = symbol.strip()
    if s.startswith("688"):
        return "科创板"
    if s.startswith("300"):
        return "创业板"
    if s.startswith(("4", "8")):
        return "北交所"
    return "主板"


def sina_symbol(symbol: str) -> str:
    """代码 → 新浪接口前缀代码（sh600519 / sz000001 / bj8xxxxx）。"""
    s = symbol.strip()
    if s.startswith(("6", "9", "5")):
        return "sh" + s
    if s.startswith(("4", "8")):
        return "bj" + s
    return "sz" + s


# 指数代码 → 新浪前缀代码。000 开头的指数属上交所，与深市个股同前缀必须特判
# （如 000300 沪深300 = sh000300，而 000001 平安银行 = sz000001）。
_SINA_INDEX_OVERRIDES = {
    "000001": "sh000001",  # 上证指数
    "000016": "sh000016",  # 上证50
    "000300": "sh000300",  # 沪深300
    "000688": "sh000688",  # 科创50
    "000852": "sh000852",  # 中证1000
    "000905": "sh000905",  # 中证500
}


def sina_index_symbol(symbol: str) -> str:
    """指数专用：000 开头指数映射上交所前缀，其余走通用规则。"""
    s = symbol.strip()
    return _SINA_INDEX_OVERRIDES.get(s, sina_symbol(s))
