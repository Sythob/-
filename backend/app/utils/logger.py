"""应用日志：UTF-8、按大小滚动、输出到 data/logs/。"""
import logging
from logging.handlers import RotatingFileHandler

from app.config import LOGS_DIR

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def setup_logger(name: str = "qstock") -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    if logger.handlers:  # 已初始化过（例如测试环境），避免重复 handler
        return logger
    logger.setLevel(logging.INFO)

    file_handler = RotatingFileHandler(
        LOGS_DIR / "qstock.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(_FORMAT))

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


def get_logger(name: str = "qstock") -> logging.Logger:
    return setup_logger(name)
