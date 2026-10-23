"""全球指数模块测试（离线）：znb/gb 文本解析。"""
from app.data.global_indices import parse_us_text, parse_znb_text

ZN_TEXT = (
    'var hq_str_znb_UKX="英国富时100,10488.2100,-117.79,-1.11,9/26/2025,'
    '1758859200,2026-10-01,19:35:24,10606.3900,10606.0000,1060";\n'
    'var hq_str_znb_DAX="德国DAX30,25175.0098,-24.18,-0.10,9/26/2025,'
    '1758859200,2026-10-01,19:49:48,25058.0801,25199.1895,25181";\n'
    'var hq_str_znb_N225="";\n'
    'var hq_str_znb_FCHI="";\n'
)

MAPPING = {
    "znb_UKX": "英国富时100", "znb_DAX": "德国DAX30",
    "znb_N225": "日经225", "znb_FCHI": "法国CAC40",
}


def test_parse_znb_text():
    result = parse_znb_text(ZN_TEXT, MAPPING)
    assert len(result["indices"]) == 2
    ukx, dax = result["indices"]
    assert ukx["symbol"] == "znb_UKX"
    assert ukx["name"] == "英国富时100"
    assert ukx["last_price"] == 10488.21
    assert ukx["pct_change"] == -1.11
    assert dax["last_price"] == 25175.0098
    # 已下线接口 → unavailable
    assert result["unavailable"] == ["日经225", "法国CAC40"]


def test_parse_znb_text_empty():
    assert parse_znb_text("", {})["indices"] == []
    assert parse_znb_text(ZN_TEXT, {})["indices"] == []  # 未配置的代码忽略


US_TEXT = (
    'var hq_str_gb_$dji="道琼斯,50906.0508,-0.86,2026-10-01 04:48:43,-443.87,51424.84";\n'
    'var hq_str_gb_inx="标普500指数,7651.5400,-0.25,2026-10-01 04:38:24,-19.30,7688.99";\n'
    'var hq_str_gb_ixic="纳斯达克,26861.0638,0.24,2026-10-01 09:49:20,63.52,26892.79";\n'
)

US_MAPPING = {"gb_$dji": "道琼斯", "gb_inx": "标普500", "gb_ixic": "纳斯达克"}


def test_parse_us_text():
    result = parse_us_text(US_TEXT, US_MAPPING)
    assert len(result["indices"]) == 3
    dji = result["indices"][0]
    assert dji["symbol"] == "gb_$dji"
    assert dji["last_price"] == 50906.0508
    assert dji["pct_change"] == -0.86  # gb_ 格式涨跌幅在字段 2
    assert dji["trade_date"] == "2026-10-01"
    assert result["unavailable"] == []


def test_parse_us_text_empty_payload():
    result = parse_us_text('var hq_str_gb_inx="";\n', US_MAPPING)
    assert result["indices"] == []
    assert result["unavailable"] == ["标普500"]
