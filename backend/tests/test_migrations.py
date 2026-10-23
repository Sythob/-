"""数据库迁移测试：旧库缺列 → run_migrations 补齐。"""
from sqlalchemy import text

from app.data import database
from app.data.migrations import run_migrations


def test_migration_adds_missing_columns(tmp_db):
    """模拟旧库：paper_order 无 condition_type 列 → 迁移后补齐。"""
    with tmp_db() as session:
        # 模拟旧版本的表结构（缺新列）
        session.execute(text(
            "CREATE TABLE IF NOT EXISTS paper_order ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER, symbol VARCHAR(10), "
            "direction VARCHAR(4), order_type VARCHAR(10), price FLOAT, volume INTEGER, "
            "filled_price FLOAT, filled_volume INTEGER, status VARCHAR(20), "
            "ai_check_json TEXT, reject_reason VARCHAR(200), created_at DATETIME)"
        ))
        session.commit()

    run_migrations()

    engine = database.get_engine()
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(paper_order)"))}
        assert "condition_type" in columns
        assert "trigger_price" in columns
        assert "next_session" in columns


def test_migration_idempotent(tmp_db):
    """重复执行不报错（幂等）。"""
    run_migrations()
    run_migrations()
    engine = database.get_engine()
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(paper_account)"))}
        assert "cash" in columns
