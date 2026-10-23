r"""全局配置：pydantic-settings 集中读取 .env，所有模块从这里取配置。

.env 文件位于项目根目录 D:\唯我量化\.env（由 .env.example 复制而来）。
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录 = backend/ 的上一级
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
MODELS_DIR = DATA_DIR / "models"
LOGS_DIR = DATA_DIR / "logs"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 服务 ----
    host: str = "127.0.0.1"
    port: int = 8000
    timezone: str = "Asia/Shanghai"

    # ---- 数据库 ----
    database_path: str = str(DATA_DIR / "qstock.db")
    data_source: str = "akshare"  # akshare / tushare(预留)

    # ---- 大模型（可插拔：deepseek 主力 / claude / qwen）----
    llm_provider: str = "deepseek"
    deepseek_api_key: str = ""
    llm_mock: bool = False
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_max_retries: int = 3
    llm_min_interval: float = 1.0
    llm_timeout: float = 30.0
    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-5-5"
    qwen_api_key: str = ""
    qwen_model: str = "qwen-plus"
    # 本地模型（Ollama，OpenAI 兼容接口；LLM_PROVIDER=local 时启用）
    local_base_url: str = "http://127.0.0.1:11434/v1"
    local_model: str = "qwen2.5:7b"

    # ---- ML 自动重训 ----
    ml_auto_retrain: bool = False  # 每周日自动重训 + 性能退化告警

    # ---- 自选池 ----
    watchlist: str = "600519,000001,510300"

    # ---- 回测默认费用与规则 ----
    commission_rate: float = 0.00025    # 佣金费率
    commission_min: float = 5.0         # 最低佣金（元）
    stamp_tax_rate: float = 0.0005      # 印花税（仅卖出）
    transfer_fee_rate: float = 0.00001  # 过户费
    slippage: float = 0.01              # 滑点（元/股）
    price_limit_ratio: float = 0.10     # 涨跌停阈值（主板 10%）

    # ---- 模拟盘 ----
    ai_risk_mode: str = "warn"  # block / warn / off
    paper_strategy: str = ""  # 模拟盘自动策略名（如 dual_ma），空 = 仅手动交易
    paper_max_loss_per_trade: float = 0.0  # 单笔亏损限额（0=关）：亏损超 X% 强制卖出
    paper_max_daily_loss: float = 0.0      # 单日亏损限额（0=关）：权益较昨收跌超 X% 次日停开仓

    # ---- 通知推送（全部可选，不配置则跳过）----
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    notify_email_to: str = ""
    serverchan_key: str = ""       # Server酱 SendKey（微信推送）
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @property
    def watchlist_symbols(self) -> list[str]:
        return [s.strip() for s in self.watchlist.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
