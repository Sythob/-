"""轻量数据库迁移：为旧库补充后加的列。

SQLite 没有原生迁移机制，create_all 只创建缺失的表、不修改已有表。
此处用 PRAGMA table_info 探测列，缺失则 ALTER TABLE ADD COLUMN（幂等）。
新增列时务必在此登记（表级新增由 create_all 自动处理，无需登记）。
"""
from sqlalchemy import text

from app.data import database
from app.utils.logger import get_logger

logger = get_logger("data.migrations")

# 表名 → [(列名, SQL 类型与默认值)]
_COLUMNS = {
    "paper_account": [
        ("cash", "FLOAT DEFAULT 1000000.0"),
    ],
    "paper_order": [
        ("next_session", "INTEGER DEFAULT 0"),
        ("condition_type", "VARCHAR(20) DEFAULT ''"),
        ("trigger_price", "FLOAT DEFAULT 0.0"),
    ],
    "ai_prediction": [
        ("error_analysis", "TEXT DEFAULT ''"),
    ],
}


def run_migrations() -> None:
    """检查并补齐所有缺失列（幂等，可反复执行）。"""
    engine = database.get_engine()
    with engine.connect() as conn:
        for table, columns in _COLUMNS.items():
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            if not existing:
                continue  # 表不存在：create_all 会负责创建（含全部列）
            for column, ddl in columns:
                if column not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                    logger.info("迁移：%s.%s 已补齐", table, column)
        conn.commit()
